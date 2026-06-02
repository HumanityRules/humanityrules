# OpenAI Codex (ChatGPT-subscription) Integration Design

Use a user's ChatGPT subscription (Codex OAuth) as a Hermes LLM backend, with the refresh token held outside the sandbox. Companion to `integrations_broker_design.md` for TLS-intercept mechanics.

Status: design only.

## Decisions

- **Target ChatGPT-subscription OAuth**, not the OpenAI API key. (API key is the trivial GitHub-style path; out of scope.)
- **Credential model A: TLS-intercept.** Sandbox holds a placeholder; broker swaps the real token on the wire to `chatgpt.com`. Refresh token never enters the sandbox.
- **Device flow, not redirect.** Codex uses OpenAI's first-party public client on an Auth0 tenant we don't administer, so we can't register a `devopshero.ai` callback. Device flow needs no callback of ours.
- **Broker polls, DOH stores.** The minutes-long poll loop fits the always-on broker, not stateless Django. On success the broker hands the refresh token to DOH, which owns storage, refresh, revoke, and cross-app reuse. Cost: refresh token transits broker memory once (acceptable — broker is outside the sandbox).
- **Provider selection: set `DOH_LLM_PROVIDER=openai-codex`.** Hermes ships this provider and the Responses dialect natively; no agent code needed.

## Wire contract

- Backend: `https://chatgpt.com/backend-api/codex`, endpoint `/responses`. (`api.openai.com` is rejected for ChatGPT auth.)
- Two headers: `Authorization: Bearer <token>` + `ChatGPT-Account-ID: <account_id>`.
- `account_id` is a claim inside the access-token JWT — derivable by whoever holds the token, no separate fetch.
- Cloudflare 403s non-residential egress unless the request carries `originator: codex_cli_rs` + a codex-shaped `User-Agent`. Hermes sends these from the sandbox; the broker must **not strip them** when forwarding.

## Flow

```
Connect (once per env):
  Broker requests device code ──▶ auth.openai.com
  WebUI shows user_code + auth.openai.com/codex/device   (user approves in own browser)
  Broker polls oauth/token ──▶ refresh_token + access_token
  Broker POSTs refresh_token ──▶ DOH (stored as openai-codex credential)

Inference (steady state):
  Sandbox Hermes ──▶ chatgpt.com  (placeholder bearer, no account-id, cloudflare headers)
  Broker swaps bearer, injects ChatGPT-Account-ID, preserves cloudflare headers, forwards
  Access token comes from DOH via existing /api/integrations/tokens (DOH refreshes)
```

## Work — DOH control plane

- New endpoint to receive the device-flow refresh token and store it as an `openai-codex` credential (keyed `owner+app`).
- Extend `/api/integrations/tokens` for `openai-codex`: mint an access token from the refresh token, derive `chatgpt_account_id`, return both in `secrets` (multi-secret shape already exists for Slack).
- Disconnect via the existing unified handler.

## Work — broker (`doh_runtime/`)

- Device-flow init + poll loop, exposed on the 9951 control API for the WebUI. Reference request shapes from Hermes `hermes_cli/auth.py` codex path.
- POST the refresh token to DOH on success.
- New `TlsProviderSpec(slug="openai-codex", hosts=("chatgpt.com",))`.
- New credential method: inject `Authorization: Bearer` **and** `ChatGPT-Account-ID` from the secrets map, without stripping the sandbox's cloudflare headers. Existing `OAuthHeader` only does Authorization; closest precedent is Slack's `VaultHeaderInject`, but bearer-plus-extra-header is new — write it clean.

## Seed the placeholder (required for model A)

Hermes won't emit a request unless `~/.hermes/auth.json` has a codex token. Write a placeholder block at `providers.openai-codex.tokens` at container boot, before the gateway starts. Tested against the pin:

- Needs **both** `access_token` and `refresh_token` as non-empty strings, else load raises. Use a non-JWT sentinel for both.
- A non-JWT sentinel reads as non-expiring → Hermes never self-refreshes, never calls the network, never rewrites `auth.json`. These are the properties model A depends on.
- Keep it in the `tokens` singleton, not `credential_pool`.
- To pin at implementation: the exact write path/lock contract for auth.json (it's structured and file-locked, unlike the `GITHUB_TOKEN` env placeholder).

## Open decision — account-id injection

- **A1 (preferred): broker injects `ChatGPT-Account-ID`.** Account-id never enters the sandbox.
- **A2: JWT-shaped placeholder** carrying the real (non-secret) account-id, so Hermes emits the header and the broker only swaps the bearer (plain `OAuthHeader`, no new method). Viable — Hermes reads the claim from an unsigned JWT — but leaks the account-id into the sandbox and the placeholder must avoid a future `exp` or it triggers self-refresh.

## To pin at implementation

- Device-authorization endpoint URL + params (scopes, PKCE for device grant) — read from codex `login/src/device_code_auth.rs` or Hermes's codex path.
- Broker must preserve the cloudflare headers end-to-end (the egress test was direct, not through the proxy).
