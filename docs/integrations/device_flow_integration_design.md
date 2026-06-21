# Device-Flow Provider Integration Design

Use broker-run OAuth device flows to connect LLM providers whose refresh tokens
must stay outside the sandbox. This started with ChatGPT-subscription Codex auth
and now also covers Nous Portal. Companion to `integrations_broker_design.md`
for TLS-intercept mechanics.

Status: implemented (backend + WebUI device dialog). Not yet wired: deploy-time selection of `DOH_LLM_PROVIDER=openai-codex` (a deploy_app.py concern, not in this repo).

## Decisions

- **Target ChatGPT-subscription OAuth**, not the OpenAI API key. (API key is the trivial GitHub-style path; out of scope.)
- **Credential model A: TLS-intercept.** Sandbox holds a placeholder; broker swaps the real token on the wire to `chatgpt.com`. Refresh token never enters the sandbox.
- **Device flow, not redirect.** Codex uses OpenAI's first-party public client on an Auth0 tenant we don't administer, so we can't register a `humanityrules.io` callback. Device flow needs no callback of ours.
- **Broker polls, DOH stores.** The minutes-long poll loop fits the always-on broker, not stateless Django. On success the broker hands the refresh token to DOH, which owns storage, refresh, revoke, and cross-app reuse. Cost: refresh token transits broker memory once (acceptable — broker is outside the sandbox).
- **Provider selection: set `DOH_LLM_PROVIDER=openai-codex`.** Hermes ships this provider and the Responses dialect natively; no agent code needed.
- **Broker injects `ChatGPT-Account-ID`** (not a JWT-shaped placeholder). Account-id never enters the sandbox; placeholder stays a plain non-JWT sentinel.

## Wire contract

- Backend: `https://chatgpt.com/backend-api/codex`, endpoint `/responses`. (`api.openai.com` is rejected for ChatGPT auth.)
- Two headers: `Authorization: Bearer <token>` + `ChatGPT-Account-ID: <account_id>`.
- `account_id` is a claim inside the access-token JWT — derivable by whoever holds the token, no separate fetch.
- Cloudflare 403s non-residential egress unless the request carries `originator: codex_cli_rs` + a codex-shaped `User-Agent`. Hermes sends these from the sandbox; the broker must **not strip them** when forwarding.

## Flow

```
Connect (once per env):
  Broker POST auth.openai.com/api/accounts/deviceauth/usercode {client_id}
    ──▶ {user_code, device_auth_id, interval}
  WebUI shows user_code + auth.openai.com/codex/device   (user approves in own browser)
  Broker polls .../deviceauth/token  (200=approved → authorization_code + code_verifier; 403/404=pending)
  Broker POST .../oauth/token grant_type=authorization_code ──▶ refresh_token + access_token
  Broker POSTs refresh_token ──▶ DOH (validated + stored as openai-codex credential)

Inference (steady state):
  Sandbox Hermes ──▶ chatgpt.com  (placeholder bearer, no account-id, cloudflare headers)
  Broker swaps bearer, injects ChatGPT-Account-ID, preserves cloudflare headers, forwards
  Access token comes from DOH via existing /api/integrations/tokens (DOH refreshes)
```

Device flow is OpenAI's bespoke `deviceauth` JSON API, **not** RFC 8628 / `oauth/token` polling. The PKCE `code_verifier` is generated server-side and returned in the approval body — the client never computes a challenge.

## DOH control plane

- `provider_openai_codex.py`: OAUTH-kind provider. `refresh_outcome` exchanges the stored refresh_token (`grant_type=refresh_token`, public `client_id`, no secret) and returns two secrets — `access_token` + `chatgpt_account_id` (JWT claim, no verification). `revoke` is a no-op (no revocation endpoint for this public client). Registered in `provider_registry.py`; enum member added to `IntegrationUserCredential.Provider` (migration 0063).
- `provider_device.py`: generic device completion endpoint `POST /api/integrations/credentials/{provider}/device-complete` (env-bearer). Dispatches through `provider_registry`; `provider_openai_codex.store_device_credentials` validates the broker-forwarded access token has `chatgpt_account_id`, then stores the refresh token. Disconnect reuses the unified handler.

## Broker (`doh_runtime/`)

- `device_flow.py`: provider-keyed device handshakes + minutes-long polling as background asyncio tasks. Codex is one adapter in the registry; Nous Portal is another. Exposed on the 9951 control API as `{provider}/device/{start,status,cancel}`. On success POSTs the token payload to DOH (`{provider}/device-complete`) and drops that provider's TLS cache.
- `tls_intercept.py`: `OAuthHeaderMultiInject` credential method (`connect_mode="device"`) — swaps the bearer via the existing `_rewrite_authorization`, then injects named extra headers via `_inject_headers` (which can't be spoofed — it overrides any client-sent value). Cloudflare headers pass through untouched (the rewrite only ever touches Authorization/Host/proxy + the named header). `TlsProviderSpec(slug="openai-codex", hosts=("chatgpt.com",))`.
- WebUI `doh-integrations.js`: `connect_mode === 'device'` → `startDeviceConnect` (a code+link dialog that polls the provider-keyed status route). Disconnect reuses the unified TLS path.

## Dropdown visibility (secondary-provider case)

The WebUI model picker derives availability from **local** state only (`config.yaml` + `auth.json`); a DOH-side credential is invisible to it. Codex is a *secondary* provider that must appear **only while connected** — so the broker mirrors connect-state into auth.json:

- `sandbox_seed.py --auth-marker connect|disconnect` writes or clears the placeholder provider block (Codex uses the agent's locked/atomic `_save_codex_tokens`; Nous uses upstream `persist_nous_credentials`). Invoked via `runuser -u hermeswebui` from the (root) broker at the same two hook points that invalidate the provider TLS cache (device connect; `disconnect_route`). Presence — not value — is the picker's signal; the picker's cache keys on a semantic hash of auth.json that includes this block, so add/remove flips it and the next `/api/models` rebuild shows/hides Codex **without a WebUI restart**.
- `config.yaml` `model.provider` (the real default, e.g. bedrock) wins over auth.json's `active_provider`, so the marker doesn't hijack the default — verified. Codex shows as a secondary pick.
- **Not** a generalized post-connect WebUI restart: other providers contribute no dropdown entries, and codex needs no gateway env (unlike GitHub's `GITHUB_TOKEN`, whose restart is for env propagation, not the picker). A restart is also the wrong layer — it's server-side and can't repopulate an already-loaded tab (see client refresh below).
- **Client refresh.** The marker makes `/api/models` *return* Codex, but the composer/Settings model dropdowns are populated once at boot and only re-fetched on a Settings provider change — not on an integrations connect — so a loaded tab shows Codex only after a full page reload. Model-provider specs set `affects_model_picker=True`; `doh-integrations.js` uses that metadata on connect/disconnect to flush the slash-command model cache and trigger the same dropdown rebuild sequence the Settings provider flow uses. `populateModelDropdown` itself has no client-side cache (fresh `/api/models` fetch each call), so this is sufficient; no reload needed.

## Placeholder seed (model A — default-provider case)

`sandbox_seed.py`, run from `webui.sh` before `system.gateway` is seeded, writes the boot placeholder when the rendered `config.yaml` has a DOH-managed model provider (`openai-codex` or `nous`) — gets locking + atomic write + `active_provider` for free. For Codex, the sentinel is a non-JWT access/refresh token pair. Idempotent — leaves a real token untouched.

This is for when Codex is the **default** backend (agent needs a token present to emit at boot). When Codex is **secondary**, the boot seed doesn't run; the connect-time marker above writes the same block. Both paths write the identical placeholder block and are idempotent, so they don't conflict.

One known edge: a real upstream 401 triggers Hermes's `force_refresh`, which fails against the sentinel refresh_token and surfaces a relogin error. Acceptable — a genuinely dead token does need a reconnect.
