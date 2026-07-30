# Integrations Broker Design

How a Hermes Personal Assistant talks to third-party APIs on behalf of one specific user, without any credential — not even a short-lived one — ever entering the sandbox. `TLS_INTERCEPT_PROVIDER_SPECS` in `tls_provider_catalog.py` is the live list of providers on this path: Google Workspace, GitHub, Slack, Telegram, X, Tavily, Browser Use, and the LLM providers.

## The core constraint

A Hermes agent runs inside a customer's AWS account, inside a nono sandbox, on behalf of one specific user. It needs to call Gmail, Calendar, Drive, etc. on that user's behalf. This requires an OAuth refresh token somewhere, long-lived enough to mint short-lived access tokens for the lifetime of the connection.

**The architectural question is where that refresh token lives.** Three plausible homes:

1. **Customer env Secrets Manager.** The sandbox-adjacent supervisor refreshes it. Rejected: puts HUMR's OAuth client secret into every customer env too, and a compromised env yields long-term Gmail access.
2. **HUMR control plane.** Env-resident components call HUMR to get a fresh access token. Chosen.
3. **Inside the sandbox (with Google's SDK doing its own refresh).** Rejected for the same reasons as (1), amplified — the sandbox itself becomes the credential custodian.

Choice (2) is the design. It costs a runtime dependency on HUMR's control plane — if HUMR is unreachable for ~an hour, Gmail-dependent tools stop working. That's an acceptable tradeoff for custody.

## The trust boundary

What crosses from HUMR into a customer env is a **secrets map per provider**: for OAuth providers a short-lived access token (1–8h of validity) plus any extra secret the upstream demands alongside it (Codex also gets a `chatgpt_account_id`); for vault providers the API key or bot token the user pasted into HUMR. It is held in broker process memory and nowhere else — no disk, no env file.

The refresh token, HUMR's OAuth client secret, and everything else HUMR needs in order to *mint* those secrets live only in HUMR's database.

**The sandbox sees none of it.** All provider API calls from inside the sandbox are intercepted by an in-container HTTPS forward proxy that terminates TLS, writes the real secret into the request, and forwards to the real upstream. Where the secret goes, and how the proxy knows the request is asking for it, is per-provider (`tls_credential_injection`): vault providers hand the sandbox a fixed placeholder — as a bearer, as a custom auth header, or embedded in the URL path — and the proxy swaps it for the real value; OAuth providers get no marker at all, and the proxy injects unconditionally on every request to their hosts.

## Three actors, three roles

**The browser (user on their laptop).** Drives the initial OAuth consent. Traverses HUMR's control plane to authenticate the user, then the provider's consent screen, then HUMR's callback. Lands back on the Hermes WebUI with the connection recorded.

**HUMR's control plane.** Owns the provider OAuth clients, stores refresh tokens per `(user, env, provider)`, and exposes authenticated endpoints that mint access tokens. Also the identity authority — a HUMR user is the unit grants are attached to, and Hermes users are by construction HUMR users.

**The customer environment.** Has a supervisor sidecar process (`humr_broker.py`) that lives outside the sandbox. It fetches provider secrets from HUMR and caches them in memory, terminates sandbox-initiated TLS with leaf certs signed by its own boot-generated CA, and writes the real credential into each request at forward time.

## Authentication between the three

- **Browser → HUMR control plane:** standard OIDC/Okta login. The connect link carries a validated `next` parameter that lands the user on `/integrations/<provider>/start` post-auth.
- **HUMR control plane → provider:** HUMR's OAuth client, registered with the provider, with redirect URIs covering every host HUMR can be reached at (prod, ngrok for dev). The right URI is chosen per request based on the browser's current host.
- **Customer env supervisor → HUMR control plane:** the per-environment bearer token that already authenticates the policy-proxy's PDP calls. Env-scoped, not user-scoped; the user identity is passed in the request body and validated server-side against the env's org. The supervisor has the bearer; the sandboxed agent does not.
- **Sandbox → broker:** no authentication. Loopback only, covered by nono's `open_port` grant. The broker is a sibling sidecar process on the same host; anything already running in the sandbox is already trusted as much as the sandbox allows.

## The broker

`humr_broker.py` runs as a supervisor-managed sidecar — root, outside the nono sandbox, same trust level as the AWS signer. One asyncio process serving three ports. `humr_broker.py` itself is a pure composition root: it reads the environment contract, constructs the subsystems, wires them together, and runs the servers.

### 1. HTTPS forward proxy (127.0.0.1:9950)

The sandbox is configured with `HTTPS_PROXY=http://127.0.0.1:9950` and `SSL_CERT_FILE` pointed at a CA bundle the broker controls. Clients (`gws`, `curl`, any TLS client that respects those env vars) send `CONNECT gmail.googleapis.com:443` to the broker.

For each CONNECT:

- **Known host** (claimed by some `TlsProviderSpec.hosts` in the provider catalog): mint a leaf cert for that hostname on demand, signed by the broker's CA. Answer the CONNECT with `200 Connection established`, then run a TLS handshake with the sandbox client *as the server*. Read each inbound HTTPS request off that socket, write the provider's real secret into it, open a fresh outbound TLS connection to the real upstream, replay the request, and relay the response back in the upstream's own framing. The client socket is reused for further requests until either side asks to close.
- **Unknown host** (any host in no provider's `hosts` list): opaque CONNECT tunnel. Bytes are forwarded both ways without inspection. The sandbox sees an end-to-end TLS session with the real upstream; the broker injects nothing.

Sandbox TLS clients verify the leaf cert against the CA bundle (because the CA's public cert is in `SSL_CERT_FILE`). They see a valid chain, send the request normally, and never know a MITM is in-path.

Response bodies are never buffered whole — the relay flushes per chunk, which both keeps SSE deltas live for the sandbox client and caps broker memory at one chunk per in-flight response. Buffering whole bodies made broker RSS track the largest response ever proxied; git clone packs through `github.com` reached multi-GB peaks.

**Why not a shim per tool?** A `HERMES_GWS_BIN=gws-shim` that forwards argv to the broker over a local JSON-RPC socket works for `gws`. But the moment a future integration's skill shells out to `curl` or uses Python `httpx`, we'd need a new shim. MITM-with-a-CA generalizes: any TLS client that respects standard trust-store env vars gets swapped by the same mechanism. We paid the "build a cert authority" cost once instead of once-per-integration.

**Why not `HTTPS_PROXY`-without-MITM?** When `HTTPS_PROXY` is set, TLS clients send `CONNECT` and then do TLS end-to-end with the real origin. The proxy sees opaque bytes and cannot touch the Authorization header. To swap it, we have to *be* the TLS peer from the sandbox's perspective. Hence the CA + on-demand leaves.

### 2. Integrations control API (127.0.0.1:9951)

Starlette/uvicorn server. One unified URL space for **all** browser-facing integration management — TLS-intercept integrations, direct MCP OAuth, and Merge's HUMR-relay passthroughs:

- **`GET /healthz`** — liveness.
- **`GET /integrations`** — flat unified status, one entry per card: `[{kind, category, slug, label, status, …}, …]`. `kind` selects the connection mechanism (`tls_intercept`, `mcp_aggregator`, or `merge_connector`); `category` selects the product grouping (`model_provider` or `connector`).
- **`POST /integrations/refresh_all`** — explicit user refresh: reload the MCP catalog and invalidate all TLS-intercept provider caches (cooldown-gated by the broker's credentials service; 429 if called too soon). The WebUI re-fetches `GET /integrations` after a successful refresh.
- **`POST /integrations/tls_intercept/<provider>/disconnect`**, **`POST /integrations/tls_intercept/<provider>/setup-session`**, **`POST /integrations/tls_intercept/<provider>/invalidate`**, **`POST /integrations/tls_intercept/<provider>/device/start|status|cancel`** — TLS-intercept providers (Google, GitHub, Telegram, Slack, device-flow LLMs). Registered directly on the broker router.
- **`GET /integrations/mcp/<provider>/oauth/start`**, **`GET /integrations/mcp/<provider>/oauth/callback`**, **`POST /integrations/mcp/<provider>/disconnect`** — direct-MCP OAuth flow (PostHog today). The aggregator owns the handlers; the broker mounts them via `MCPAggregator.routes(prefix="/integrations")`.
- **`GET /integrations/merge/connector-status`**, **`POST /integrations/merge/link-token`**, **`POST /integrations/merge/disconnect`** — Merge passthroughs that forward to HUMR with the env bearer attached. See `merge_integration_design.md`.

Reached from the browser same-origin through Caddy (`humr_runtime/http_router/Caddyfile`, listening on `:8787`), which matches `path /__humr_broker/*`, strips that prefix, and reverse-proxies to `127.0.0.1:9951`. The request never reaches the Hermes WebUI (`127.0.0.1:8789`), so the WebUI's CSRF gate does not apply — the broker is loopback-only and its endpoints are stateless.

### 3. MCP aggregator (127.0.0.1:9952)

Sandbox-only MCP transport: the in-sandbox agent connects here for direct-MCP and Merge tools. The browser never reaches 9952 — the aggregator's browser-facing OAuth routes are mounted on the control API instead. See `mcp_aggregator_design.md`.

## Broker modules

Every module in `humr_runtime/integrations/` is imported by bare name: `supervisor.sh` runs the broker with `PYTHONPATH=${HUMR_RUNTIME_DIR}/integrations`, so there is no package prefix. Each module logs under its own name, and the broker's log format is `[humr_broker.<module>]`, so a log line names the part that emitted it.

Composition and transport:

- **`humr_broker.py`** — composition root. Reads the env contract, builds everything, starts the three servers, handles shutdown.
- **`control_api.py`** — the Starlette app on 9951. Pure transport: parse, delegate, serialize. Also holds the card-visibility policy (which slugs an org's WebUI is allowed to see).
- **`credentials_service.py`** — the choreography that follows any credential change, whatever surface started it: confirm it with HUMR, drop and refetch the token cache, re-render the gateway-managed env block, and kick what the provider spec declares (gateway/WebUI restart, models-cache drop, local auth markers). Everything below it is mechanism and never calls back up.
- **`humr_client.py`** — the outbound JSON client for HUMR's per-env endpoints, holding the control-plane URL, the env bearer, and the owner/app identity every one of them requires. Token refresh, device-flow completion, disconnect, and vault setup sessions all go through it. (`mcp_merge_backend` is the one component that keeps its own copy of the bearer, for its HUMR relay calls.)
- **`device_flow.py`** — broker-run OAuth device flows (no redirect callback of ours). See `device_flow_integration_design.md`.
- **`permissions_control.py`** — the `/permissions/*` relay for the self-referential IAM editor. See `permissions_broker_design.md`.
- **`mcp_aggregator.py`**, **`mcp_merge_backend.py`**, **`mcp_top_level_tools.py`** — the MCP surface: the FastMCP server on 9952, the Merge-backed backend, and the four progressive-disclosure tools that keep ~1500 connector tools out of the prompt. See `mcp_aggregator_design.md` and `merge_integration_design.md`.

The TLS-intercept subsystem is six modules. Dependencies point strictly downward — `tls_intercept` composes the four mechanism modules, all of which read the catalog; there are no edges between siblings:

- **`tls_intercept.py`** — the front door. `TlsInterceptRuntime` (the object the control API and credentials service hold) plus the CONNECT lifecycle: accept the CONNECT, tunnel or terminate, and run the per-request loop that decides, injects, forwards, and evicts on a 401.
- **`tls_provider_catalog.py`** — static data only. The credential-method dataclasses, the `TlsProviderSpec` entries, and the registries built from them. The only file a provider that uses an existing credential method needs.
- **`tls_certificate_authority.py`** — `CertMinter`: the boot-generated CA, the `bundle.pem` the sandbox trusts, and the per-hostname leaf certs. Owns that material end to end and nothing else.
- **`tls_token_store.py`** — `TokenStore`: the real secret for a host, the HUMR refresh protocol, and the durable per-provider connection state the status cards and gateway env read.
- **`tls_credential_injection.py`** — whether a request is asking for HUMR's credential (`needs_injection`) and where the secret is written into it (`rewrite_request_for_provider`). Touches neither the network nor the cache; the caller looks the secrets up and passes them in.
- **`tls_http_message_relay.py`** — provider-agnostic HTTP/1.1: parse, frame, replay upstream, stream the response back. Nothing here knows that providers, credentials, or tokens exist, and that discipline is what keeps it tractable.

## Token refresh

There is no per-provider refresh loop and no timer. Every fetch is the same batched `POST /api/integrations/tokens` against HUMR with `{owner_username, app_slug, providers: [...]}`, answered with `200 {results: {<slug>: {outcome, secrets?, expires_in?, config, metadata}, ...}}` where `outcome` is `has_token | absent | transient`. A disconnected provider is a normal `absent` entry, not an HTTP 4xx, so it costs no error log per refresh. Single-slug refreshes use the same endpoint with a one-element list.

Four things trigger a fetch:

- **Broker bootstrap.** Every slug, before the control port opens. A transient failure here is deliberately fatal: the broker exits, supervisor tears the container down, and ECS restarts the task. Dying is what guarantees the gateway and WebUI children only ever launch with an env rendered from live HUMR state, so there is no stale-env recovery path to maintain.
- **The proxy hot path.** An intercepted request whose cached secret is missing, expired, or within `REFRESH_LEAD_SECONDS` (300) of expiry refetches that one slug before going upstream. Fetch and apply both happen under the store's single lock, which is what makes "a parked fetch wrote past an invalidate" structurally impossible.
- **A known state change.** Connect, disconnect, vault save, or device-flow completion invalidates that slug and refetches only it. Fanning out to every slug on each connect would ask HUMR about providers the user has never connected.
- **Explicit Refresh.** `POST /integrations/refresh_all` drops the whole cache and refetches everything, rate-limited to one call per 30s (429 otherwise).

`transient` overwrites nothing: an unreachable HUMR leaves a still-unexpired cached secret in place and the proxy keeps serving it for the rest of that token's life, rather than reporting the provider disconnected because HUMR hiccuped.

Two pieces of state come out of a refresh, with deliberately different lifetimes. The **token cache** holds the injectable secrets and is pruned by expiry on the injection path. The **connection state** is what the WebUI status cards and the gateway env block read, and it changes only on a refresh outcome (`has_token`/`absent`) or a confirmed disconnect. A lapsed token is not a disconnect: an idle provider's card still reads `connected` after its access token has been pruned, and its env bindings stay in the managed block.

## What lives where

- **OAuth client ID + secret** — HUMR database. One per HUMR deployment per provider. Never crosses the boundary.
- **Refresh tokens** — HUMR database, keyed by `(user, env, provider)`. Custody stays on HUMR. Scoped to env so revocation can be env-surgical.
- **Env bearer token** — customer env secrets manager, with only the hash stored on HUMR. Pre-existing pattern, reused.
- **Provider secrets** — `TokenStore`'s in-memory slug→secrets-map cache, each entry carrying its own expiry. Never hits disk. Lost on container restart and refetched from HUMR during bootstrap.
- **Placeholders** — the gateway-managed block of `${HERMES_HOME}/.env`, and therefore visible inside the sandbox. These are the fixed non-secrets (`000000:HUMR_PLACEHOLDER`, `xoxb-HUMR_PLACEHOLDER`, …) whose *presence* activates a binding and which the proxy swaps in flight. See `gateway_env_and_restart_design.md`.
- **Broker CA private key** — broker's process memory. Generated at startup, never persisted. A new container gets a new CA; the sandbox reboots with it and inherits the new `SSL_CERT_FILE` via supervisor env.
- **Broker CA public cert** — `/run/humr/integrations-broker/ca/bundle.pem`, readable by the sandbox (the nono profile grants read on that directory). Contains our CA cert plus system roots so the sandbox trusts both our MITM'd hosts and real internet hosts (HUMR-proxied or tunneled).

## Network topology inside the container

```
┌──────────────────────── customer container ────────────────────────┐
│                                                                    │
│  ┌─ nono sandbox ─────────────────────┐                            │
│  │  HTTPS_PROXY=http://127.0.0.1:9950 │                            │
│  │  SSL_CERT_FILE=<CA bundle path>    │                            │
│  │                                    │                            │
│  │   agent → gws/curl → ─CONNECT─────►│───────► 127.0.0.1:9950     │
│  │                                    │            (proxy)         │
│  └────────────────────────────────────┘            │               │
│                                                    │               │
│   Caddy :8787 ── /__humr_broker/* ─reverse-proxy─► 127.0.0.1:9951  │
│                                                    │  (control API)│
│                                                    ▼               │
│                                   humr_broker.py (supervisor child)│
│                                                    │               │
└────────────────────────────────────────────────────┼───────────────┘
                                                     │
                                                     ▼
                       real Google / Slack / ... over real TLS (fresh
                       outbound conn per request, system trust store)
```

The sandbox never opens a direct TCP connection to a provider — every connection terminates at `127.0.0.1:9950`, is torn apart, and a new one is opened from the broker process.

## The provider catalog, end to end

`tls_provider_catalog.py` is static data — frozen dataclasses, no behavior. `TLS_INTERCEPT_PROVIDER_SPECS` is a tuple of `TlsProviderSpec`, and two registries are derived from it at import: `TLS_INTERCEPT_PROVIDERS` (slug → spec, fails fast on a duplicate slug) and `HOST_TO_TLS_PROVIDER` (normalized host → slug, fails fast when two providers claim the same host). CONNECT hostnames are normalized the same way before routing (`normalize_connect_host`: strip, drop the trailing dot, lowercase).

```python
TlsProviderSpec(
    slug="telegram",
    label="Telegram",
    hosts=("api.telegram.org",),
    logo_url="/extensions/humr/telegram.svg",
    credential_method=VaultUrlRewrite(placeholder="000000:HUMR_PLACEHOLDER"),
    env_bindings=(
        EnvBinding(env_var="TELEGRAM_BOT_TOKEN", value="000000:HUMR_PLACEHOLDER"),
        EnvBinding(env_var="TELEGRAM_ALLOWED_USERS", config_key="allowed_users", list_separator=","),
    ),
    restart_gateway_after_save=True,
    restart_webui_after_save=False,
    category="connector",
)
```

What each field drives:

- **`slug`** — the key everywhere: the identity in HUMR's provider registry, the path segment in `/integrations/tls_intercept/<slug>/*`, and the cache/connection-state key in the token store.
- **`hosts`** — which CONNECTs get intercepted. Everything else tunnels opaquely. Listing a host is the whole decision; there is no separate allowlist.
- **`label`**, **`logo_url`** — what the WebUI card shows. `logo_url` resolves to an SVG shipped in `webui-extension/`.
- **`credential_method`** — the dataclass that decides both whether a request is asking for HUMR's credential and where the secret is written. See below.
- **`env_bindings`** — env vars rendered into the gateway-managed env block while the provider is connected. Either a static `value` (a placeholder the proxy later swaps) or a `config_key` read from what HUMR returned, optionally joined with `list_separator` for list-shaped config. Exactly one of the two, enforced in `__post_init__`. A provider whose credential nothing in the sandbox reads out of the environment declares `env_bindings=()`. See `gateway_env_and_restart_design.md`.
- **`restart_gateway_after_save`**, **`restart_webui_after_save`** — which process-compose entries the credentials service restarts after a connect or disconnect *that actually changed the env block*. Needed when a process reads the value at startup or caches it.
- **`category`** — `model_provider` or `connector`, which section of the integrations panel the card renders under. `affects_model_picker` is derived from it rather than stored, so the two cannot drift: connecting a model provider is precisely what changes `/api/models`.

Five credential methods exist. Each is a frozen dataclass in the catalog, and each has one branch in `needs_injection` and one in `rewrite_request_for_provider`:

- **`OAuthHeader`** — HUMR's OAuth token injected as `Authorization`. `auth_format` picks the encoding: `bearer` (Google, X, Nous) or `basic_x_access_token` for GitHub's git smart-HTTP, which wants HTTP Basic with the token as the password under the `x-access-token` username. `connect_mode` defaults to redirect `oauth`; Nous overrides it to `device`.
- **`OAuthHeaderMultiInject`** — one bearer plus named extra headers, when HUMR's refresh returns several secrets. `bearer_secret` names the one carried as `Authorization: Bearer`; `header_secrets` maps each remaining secret to the header it is injected as. Codex is the consumer: an `access_token` plus a `chatgpt_account_id` sent as `ChatGPT-Account-ID`. Injected headers override anything the client sent under the same name, so the sandbox cannot spoof them; every other client header passes through untouched.
- **`VaultUrlRewrite`** — the secret sits in the URL path. The sandbox uses `placeholder` where the real token goes (Telegram's `/bot{token}/`) and the proxy substitutes before forwarding. Every path on that host embeds a token, so a request without the placeholder is an un-rewritable credential rather than public traffic, and it is refused.
- **`VaultHeaderInject`** — vault-pasted secret(s) riding `Authorization: Bearer`. `placeholders` maps secret name → placeholder, and selection is by reverse-mapping the incoming placeholder bearer, not by request path: the env hands the sandbox a distinct placeholder per secret and the sandbox already sends the right one per call (Slack's app token opens Socket Mode, its bot token posts messages). Also OpenRouter, OpenAI API, and Tavily, each with a single `api_key`.
- **`VaultApiKeyHeader`** — same idea, but the credential rides a provider-specific header instead of `Authorization`: Anthropic's `x-api-key`, Browser Use's `X-Browser-Use-API-Key`. The proxy confirms the named header carries `placeholder`, then swaps in the real key. No `Authorization` is added, and other required headers (Anthropic's `anthropic-version`) pass through.

Beyond that, three refusals are worth knowing, all raised as `SecretSelectionError` and answered with a 400 rather than forwarded:

- A vault credential slot holding something that is neither empty nor a recognized placeholder. A BYO key is neither injected over nor silently forwarded.
- A `VaultApiKeyHeader` request that sent no api-key header but did send an `Authorization` — credentialed by other means, so refused.
- A required secret missing from the cache for a multi-secret provider.

An empty slot on a vault provider is the one benign case: it means anonymous public traffic (OpenRouter's unauthenticated `/api/v1/models`, say), which the proxy forwards as-is without consulting the token store, so a disconnected provider costs no HUMR refresh per request.

## Cert lifecycle

- **CA generation**: at broker startup, an RSA-4096 CA key is generated in memory. The CA cert is valid for 5 years, with `BasicConstraints(ca=True, path_length=0)` and `KeyUsage(cert_sign, crl_sign)`. Subject Key Identifier is attached.
- **Bundle write**: CA cert is concatenated with the system root bundle (`/etc/ssl/certs/ca-certificates.crt`, falling back to `/etc/pki/tls/certs/ca-bundle.crt`) and written to `/run/humr/integrations-broker/ca/bundle.pem`, mode 0644. Supervisor points `SSL_CERT_FILE` at it inside the nono env. Sandbox TLS clients use this bundle for verification, which is why they trust both our MITM'd hosts and direct-tunneled real hosts (arbitrary third-party APIs the agent reaches that no provider claims).
- **Leaf minting**: first `CONNECT` for a known hostname mints an RSA-2048 leaf, 2-year validity, with `SubjectAlternativeName=[DNS:<host>]` (or `IPAddress` when the CONNECT target is a literal IP), `BasicConstraints(ca=False, critical)`, `KeyUsage(digital_signature, key_encipherment, critical)`, `ExtendedKeyUsage=[SERVER_AUTH]`, `SubjectKeyIdentifier`, and `AuthorityKeyIdentifier.from_issuer_public_key(CA)`. The resulting `SSLContext` — ALPN `http/1.1` only — is cached in memory by hostname. Leaf cert/key PEMs are written only as transient 0600 files under `/run/humr/integrations-broker/private`, loaded into the context, and immediately unlinked; that directory is 0700 and not granted to the sandbox.
- **Rotation**: a new container boot regenerates the CA and all leaves. The sandbox reboots with the container, so there's no "CA rotated under a live agent" corner.

Python's cert validation (OpenSSL) rejects chains missing `SubjectKeyIdentifier` or `AuthorityKeyIdentifier`. Both must be attached — learned the hard way.

## Failure modes

- **User hasn't connected the provider yet.** HUMR returns `outcome: "absent"` for that slug inside a 200 response. The broker drops any cached secret, flips the provider's connection state to not-connected, and publishes `status: not_connected`. Subsequent sandbox calls to that provider's hosts get a synthetic HTTP 503 from the broker with a human-readable message — the sandbox-side skill surfaces it to the user verbatim.
- **Refresh token revoked at provider** (user revoked, or a security system did). HUMR deletes the stored grant and returns the same `outcome: "absent"`. Indistinguishable from never-connected on the broker side, and the card reads `not_connected` either way — the only two statuses are `connected` and `not_connected`.
- **Upstream 401 on an injected request.** The cached secret is treated as no longer valid and evicted, so the next request through the proxy refetches from HUMR. No retry inside the current connection. A 401 on a request the broker did *not* inject into (anonymous pass-through) never evicts: it did not use our secret, so the cache is not implicated.
- **Transient HUMR failure.** Neither cache nor connection state is touched. Tool calls keep working off the cached secret until it actually expires; the next trigger tries HUMR again.
- **HUMR unreachable long enough to expire the secret.** Once the entry is pruned, calls to that provider fail with the broker's "integration not connected" 503. There is no retry loop to wait on — the next intercepted request to that host is itself the retry, and any success resyncs the card.
- **Customer env compromised.** The attacker gets the env bearer, which can mint access tokens for users already connected in that env — bounded to env users, bounded in token lifetime. They cannot extract refresh tokens (not in the env) and cannot mint for other envs (bearer is env-scoped). They also cannot extract historical access tokens — the broker never persists them.
- **Malicious skill inside the sandbox.** Can send arbitrary API calls through the broker *for the current user* (this is the point — the broker has to let tool calls through to do work). Cannot read the CA private key (process memory, outside the sandbox). Cannot read cached secrets (same). Cannot bypass the broker to reach a provider directly — the nono profile grants no provider hostname (`network.block` is on and there is no `allow_domain`); the broker's loopback port is what's reachable.

## The nono profile

Three things in `humr_runtime/hermes-nono-profile.json` make the broker path work:

- **No `allow_domain`.** Every provider host — `*.googleapis.com`, `api.tavily.com`, and the rest — is reached through the broker proxy, which MITMs known hosts and opaquely tunnels the rest. Adding an integration is a `TlsProviderSpec` in `tls_provider_catalog.py`, never a network-policy entry.
- **`filesystem.read` grants `/run/humr/integrations-broker/ca`.** That is how the sandbox can read `bundle.pem`. The sibling `private` directory, where leaf key PEMs are transiently written, is deliberately not granted.
- **`open_port` includes 9950 (proxy), 9951 (control), and 9952 (MCP).** All loopback; none is publicly exposed by the task definition.

`HTTPS_PROXY` and `SSL_CERT_FILE` are *not* nono `allow_vars` entries. `supervisor.sh` sets them (plus `GIT_SSL_CAINFO`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE`, for clients that ignore `SSL_CERT_FILE`) inside the sandbox via `nono run … -- /usr/bin/env VAR=… …`, past nono's env filter, and only when the broker actually started.

## Shape that generalizes to other providers

Everything above is provider-agnostic. Adding a provider that reuses an existing credential method is:

1. A `TlsProviderSpec` appended to `TLS_INTERCEPT_PROVIDER_SPECS` in `tls_provider_catalog.py`.
2. A `provider_<slug>.py` module under `humanityrules_app/views/integrations/` plus a row in that package's `provider_registry.py`. The registry's `ProviderKind` (OAUTH or VAULT) determines which uniform functions the module must expose; the module's `refresh_outcome` is what the batched token endpoint calls.
3. An SVG in `webui-extension/` matching the spec's `logo_url`.

No new broker code, no new supervisor code, no new containers. The generic `tls_intercept` card in the WebUI extension renders the new provider from its status payload, including the connect, disconnect, vault-paste, and device-login flows — a per-slug card specialization (as Google and Slack have) is only needed for UI a generic card can't express.

A *new credential method* costs more: a dataclass in the catalog and one more branch in each of `needs_injection` and `rewrite_request_for_provider`. Nothing else in the subsystem has to change.

## Open questions

- **Governance.** "Admin hasn't enabled Gmail for this workspace" isn't expressed yet. Probably a workspace-level allowlist checked before `/integrations/<provider>/start` proceeds.
- **Revocation surfacing.** A revoked grant is reported as plain `not_connected`, so the user cannot tell "I never connected this" from "this stopped working", and nothing notifies them either way — they have to open the Integrations pane to notice. A distinct status plus a push over the WebUI's existing SSE infra would close the loop.
- **Scope granularity.** Currently all-or-nothing per app family. Per-scope toggles ("read mail but not send") would need a `scopes[]` field per provider and a more nuanced refresh contract.
- **Multi-account.** A user might want to connect personal + work Google accounts. Schema supports it in principle (drop the `(user, env, provider)` uniqueness); routing ("which account does this tool call use?") isn't designed.
- **HTTP/2 at the terminator.** gws/hyper ALPN-negotiate H2 with Google; the broker advertises only `http/1.1` in its server ALPN list, forcing fallback. Fine today; large uploads and long pagelists might benefit from H2 later.
- **Request-body streaming.** Responses stream; request bodies are still read whole before forwarding (Drive uploads, Gmail attachments), so a multi-GB upload is a broker-memory spike. Streaming the inbound half would need the framing decision moved ahead of the read.
- **Encryption at rest for refresh tokens.** Plaintext in Postgres today, matching existing posture. A cross-cutting initiative would cover them uniformly.
