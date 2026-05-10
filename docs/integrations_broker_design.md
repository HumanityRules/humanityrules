# Integrations Broker Design

How a Hermes Personal Assistant talks to third-party APIs (Google Workspace today; Slack, Notion, Linear tomorrow) on behalf of one specific user, without any credential — not even a short-lived one — ever entering the sandbox.

## The core constraint

A Hermes agent runs inside a customer's AWS account, inside a nono sandbox, on behalf of one specific user. It needs to call Gmail, Calendar, Drive, etc. on that user's behalf. This requires an OAuth refresh token somewhere, long-lived enough to mint short-lived access tokens for the lifetime of the connection.

**The architectural question is where that refresh token lives.** Three plausible homes:

1. **Customer env Secrets Manager.** The sandbox-adjacent supervisor refreshes it. Rejected: puts DOH's OAuth client secret into every customer env too, and a compromised env yields long-term Gmail access.
2. **DOH control plane.** Env-resident components call DOH to get a fresh access token. Chosen.
3. **Inside the sandbox (with Google's SDK doing its own refresh).** Rejected for the same reasons as (1), amplified — the sandbox itself becomes the credential custodian.

Choice (2) is the design. It costs a runtime dependency on DOH's control plane — if DOH is unreachable for ~an hour, Gmail-dependent tools stop working. That's an acceptable tradeoff for custody.

## The trust boundary

Only one kind of secret crosses from DOH into a customer env: a **short-lived access token per provider** (minutes of validity). Nothing else provider-related is stored in the env, not even transiently.

The refresh token, DOH's OAuth client secret, and every other long-lived credential live only in DOH's database.

**The sandbox sees nothing — not even the access token.** All provider API calls from inside the sandbox are intercepted by an in-container HTTPS forward proxy that terminates TLS, swaps the Authorization header for the current access token, and forwards to the real upstream. The sandbox sends a hardcoded placeholder Bearer; the broker substitutes the real token in flight.

## Three actors, three roles

**The browser (user on their laptop).** Drives the initial OAuth consent. Traverses DOH's control plane to authenticate the user, then the provider's consent screen, then DOH's callback. Lands back on the Hermes WebUI with the connection recorded.

**DOH's control plane.** Owns the provider OAuth clients, stores refresh tokens per `(user, env, provider)`, and exposes authenticated endpoints that mint access tokens. Also the identity authority — a DOH user is the unit grants are attached to, and Hermes users are by construction DOH users.

**The customer environment.** Has a supervisor sidecar process (`integrations_broker.py`) that lives outside the sandbox. It refreshes access tokens on a timer, terminates sandbox-initiated TLS with DOH-signed leaf certs, and swaps Authorization headers at forward time.

## Authentication between the three

- **Browser → DOH control plane:** standard OIDC/Okta login. The connect link carries a validated `next` parameter that lands the user on `/integrations/<provider>/start` post-auth.
- **DOH control plane → provider:** DOH's OAuth client, registered with the provider, with redirect URIs covering every host DOH can be reached at (prod, ngrok for dev). The right URI is chosen per request based on the browser's current host.
- **Customer env supervisor → DOH control plane:** the per-environment bearer token that already authenticates the policy-proxy's PDP calls. Env-scoped, not user-scoped; the user identity is passed in the request body and validated server-side against the env's org. The supervisor has the bearer; the sandboxed agent does not.
- **Sandbox → broker:** no authentication. Loopback only, covered by nono's `open_port` grant. The broker is a sibling sidecar process on the same host; anything already running in the sandbox is already trusted as much as the sandbox allows.

## The broker

`integrations_broker.py` runs as a supervisor-managed sidecar (same trust level as `aws-sigv4-proxy` and `haproxy`). One asyncio process with three concurrent responsibilities.

### 1. HTTPS forward proxy (127.0.0.1:9950)

The sandbox is configured with `HTTPS_PROXY=http://127.0.0.1:9950` and `SSL_CERT_FILE` pointed at a CA bundle the broker controls. Clients (`gws`, `curl`, any TLS client that respects those env vars) send `CONNECT gmail.googleapis.com:443` to the broker.

For each CONNECT:

- **Known host** (listed in the broker's `PROVIDERS` config): mint a leaf cert for that hostname on demand, signed by the broker's CA. Answer the CONNECT with `200 Connection established`, then run a TLS handshake with the sandbox client *as the server*. Parse the inbound HTTPS request, rewrite the `Authorization` header (and `Host`), open a fresh outbound TLS connection to the real upstream, replay the request, stream the response back.
- **Unknown host** (e.g. `api.tavily.com`): opaque CONNECT tunnel. Bytes are forwarded both ways without inspection. The sandbox sees an end-to-end TLS session with the real upstream; the broker injects nothing.

Sandbox TLS clients verify the leaf cert against the CA bundle (because the CA's public cert is in `SSL_CERT_FILE`). They see a valid chain, send the request normally, and never know a MITM is in-path.

**Why not a shim per tool?** A `HERMES_GWS_BIN=gws-shim` that forwards argv to the broker over a local JSON-RPC socket works for `gws`. But the moment a future integration's skill shells out to `curl` or uses Python `httpx`, we'd need a new shim. MITM-with-a-CA generalizes: any TLS client that respects standard trust-store env vars gets swapped by the same mechanism. We paid the "build a cert authority" cost once instead of once-per-integration.

**Why not `HTTPS_PROXY`-without-MITM?** When `HTTPS_PROXY` is set, TLS clients send `CONNECT` and then do TLS end-to-end with the real origin. The proxy sees opaque bytes and cannot touch the Authorization header. To swap it, we have to *be* the TLS peer from the sandbox's perspective. Hence the CA + on-demand leaves.

### 2. Control API (127.0.0.1:9951)

Plain HTTP, three endpoints:

- **`GET /status`** — current provider state, serialized from in-memory at request time. Shape: `{doh_control_plane_url, env_slug, owner_username, providers: {<slug>: {label, status, last_refreshed_at}}}`. No on-disk status file.
- **`POST /kick`** — wakes every refresh loop's `asyncio.Event`. Used by the WebUI extension after the OAuth connect/disconnect round-trip returns the user to Hermes, so state flips are sub-second instead of waiting for the next polling tick.
- **`GET /healthz`** — liveness.

Reached from the browser same-origin via a WebUI reverse-proxy patch (`patches-webui/07-doh-broker-proxy.patch`) that forwards `/__doh_broker/*` to `127.0.0.1:9951`. Deliberately bypasses the WebUI's CSRF gate — the broker is loopback-only and the endpoints are stateless.

### 3. Refresh loops

One coroutine per provider, driven by a single `PROVIDERS` dict at the top of the broker. Each entry: `{label, refresh_path, hosts[]}`. Adding Slack/Notion/Linear is a new dict entry, not new functions.

Each loop POSTs `{owner_username}` with the env bearer to DOH's `refresh_path`, classifies the response by status code (200 ok / 404 not_connected / 410 revoked / 401+500 fatal / else transient), updates an in-memory `{host: token}` map for its hosts, updates `{slug: state}` for the control API, then sleeps until `expires_in - 5min` or an `asyncio.Event.wait()` from `/kick` fires.

Refresh cadence is ~55 minutes when connected. `/kick` replaces the short polling interval the old design used to mask the lack of a synchronous signal.

## What lives where

- **OAuth client ID + secret** — DOH database. One per DOH deployment per provider. Never crosses the boundary.
- **Refresh tokens** — DOH database, keyed by `(user, env, provider)`. Custody stays on DOH. Scoped to env so revocation can be env-surgical.
- **Env bearer token** — customer env secrets manager, with only the hash stored on DOH. Pre-existing pattern, reused.
- **Access tokens** — broker's in-memory `{host: token}` map. Never hits disk. Lost on container restart (recomputed within seconds from DOH).
- **Broker CA private key** — broker's process memory. Generated at startup, never persisted. A new container gets a new CA; the sandbox reboots with it and inherits the new `SSL_CERT_FILE` via supervisor env.
- **Broker CA public cert** — `/opt/doh/ca/bundle.pem`, readable by the sandbox. Contains our CA cert plus system roots so the sandbox trusts both our MITM'd hosts and real internet hosts (DOH-proxied or tunneled).

## Network topology inside the container

```
┌──────────────────────── customer container ────────────────────────┐
│                                                                    │
│  ┌─ nono sandbox ─────────────────────┐                            │
│  │  HTTPS_PROXY=http://127.0.0.1:9950 │                            │
│  │  SSL_CERT_FILE=/opt/doh/ca/...     │                            │
│  │                                    │                            │
│  │   agent → gws/curl → ─CONNECT─────►│───────► 127.0.0.1:9950     │
│  │                                    │            (broker)        │
│  └────────────────────────────────────┘            │               │
│                                                    │               │
│   WebUI :8787 ─── /__doh_broker/* ─reverse-proxy─► 127.0.0.1:9951  │
│                                                    │               │
│                                                    ▼               │
│                           integrations_broker.py (supervisor child)│
│                                                    │               │
└────────────────────────────────────────────────────┼───────────────┘
                                                     │
                                                     ▼
                       real Google / Slack / ... over real TLS (fresh
                       outbound conn per request, system trust store)
```

The sandbox never opens a direct TCP connection to a provider — every connection terminates at `127.0.0.1:9950`, is torn apart, and a new one is opened from the broker process.

## The `PROVIDERS` config, end to end

```python
PROVIDERS = {
    "google": {
        "label": "Google Workspace",
        "refresh_path": "/api/integrations/google/token",
        "hosts": [
            "gmail.googleapis.com",
            "calendar-json.googleapis.com",
            "drive.googleapis.com",
            "docs.googleapis.com",
            "sheets.googleapis.com",
            "people.googleapis.com",
            "www.googleapis.com",
            "oauth2.googleapis.com",
        ],
    },
}
```

That dict alone drives: which hostnames get MITM'd vs tunneled; which DOH endpoint to call for refresh; the `label` shown in the Integrations UI; and (implicitly) which status key the extension renders.

## Cert lifecycle

- **CA generation**: at broker startup, an RSA-4096 CA key is generated in memory. The CA cert is valid for 5 years, with `BasicConstraints(ca=True, path_length=0)` and `KeyUsage(cert_sign, crl_sign)`. Subject Key Identifier is attached.
- **Bundle write**: CA cert is concatenated with the system root bundle (from `/etc/ssl/certs/ca-certificates.crt`) and written to `/opt/doh/ca/bundle.pem`. Supervisor exports this as `SSL_CERT_FILE` inside the nono env. Sandbox TLS clients use this bundle for verification, which is why they trust both our MITM'd hosts and direct-tunneled real hosts (Tavily etc.).
- **Leaf minting**: first `CONNECT` for a known hostname mints an RSA-2048 leaf, 2-year validity, with `SubjectAlternativeName=[DNS:<host>]`, `BasicConstraints(ca=False, critical)`, `KeyUsage(digital_signature, key_encipherment, critical)`, `ExtendedKeyUsage=[SERVER_AUTH]`, `SubjectKeyIdentifier`, and `AuthorityKeyIdentifier.from_issuer_public_key(CA)`. Cached in-memory by hostname. Never written to disk except transient per-leaf tmp PEMs consumed by `ssl.SSLContext.load_cert_chain`.
- **Rotation**: a new container boot regenerates the CA and all leaves. The sandbox reboots with the container, so there's no "CA rotated under a live agent" corner.

Python's cert validation (OpenSSL) rejects chains missing `SubjectKeyIdentifier` or `AuthorityKeyIdentifier`. Both must be attached — learned the hard way.

## Failure modes

- **User hasn't connected the provider yet.** DOH returns 404. Broker removes the host→token mapping and publishes `status: not_connected`. Subsequent sandbox calls get a synthetic HTTP 503 from the broker with a human-readable message — the sandbox-side skill surfaces it to the user verbatim.
- **Refresh token revoked at provider** (user revoked, or security system did). DOH returns 410 and deletes the stored grant. Same behavior as not_connected from the broker's side; the UI distinguishes "never connected" from "revoked" via the `status` field.
- **Transient DOH failure.** Broker keeps the last in-memory token and retries with exponential backoff. Tool calls continue working until the current token expires.
- **DOH unreachable long enough to expire the token.** Provider calls start failing with the broker's "integration not connected" 503 once the token is purged — or with provider 401s if the broker still has a stale token. Mitigations: `MIN_SLEEP_SECONDS=30` floor + backoff caps mean retries continue; once DOH is back, `/kick` resyncs instantly.
- **Customer env compromised.** The attacker gets the env bearer, which can mint access tokens for users already connected in that env — bounded to env users, bounded in token lifetime. They cannot extract refresh tokens (not in the env) and cannot mint for other envs (bearer is env-scoped). They also cannot extract historical access tokens — the broker never persists them.
- **Malicious skill inside the sandbox.** Can send arbitrary API calls through the broker *for the current user* (this is the point — the broker has to let tool calls through to do work). Cannot read the CA private key (process memory, outside sandbox). Cannot read access tokens (same). Cannot bypass the broker to hit Google directly — the nono profile's `allow_domain` doesn't grant provider hostnames; only the local broker port is reachable.

## The nono profile

Two changes from the pre-broker design:

- **`allow_domain` dropped from 8 to 1.** Previously: Tavily + 7 `*.googleapis.com`. Now: just Tavily (which reaches its API as an opaque CONNECT tunnel through the broker). Future integrations add zero entries here — their hostnames go in the broker's `PROVIDERS.hosts` list instead.
- **`allow_vars` adds `HTTPS_PROXY` and `SSL_CERT_FILE`.** `allow_vars` is nono's env-variable passthrough list; without these entries, nono would strip the values at sandbox entry and the proxy/trust wiring would be silently undone.
- **`open_port` adds 9950 (proxy) and 9951 (control).** Both loopback; neither is publicly exposed by the task definition.

## Shape that generalizes to other providers

Everything above is provider-agnostic. Adding a second provider is:

1. A row in the `PROVIDERS` dict.
2. A refresh endpoint on DOH at the matching `refresh_path`.
3. A start/disconnect flow pair in DOH that the extension links to.
4. An entry in the `integrations/` section of the WebUI extension (label, any provider-specific connect URL params).

No new broker code. No new supervisor code. No new patches. No new containers.

## Open questions

- **Governance.** "Admin hasn't enabled Gmail for this workspace" isn't expressed yet. Probably a workspace-level allowlist checked before `/integrations/<provider>/start` proceeds.
- **Revocation surfacing.** When DOH returns 410, the UI shows `status: revoked` — distinct from `not_connected`. Good, but there's no notification: the user has to open the Integrations pane to notice. A push via the WebUI's existing SSE infra could close the loop.
- **Scope granularity.** Currently all-or-nothing per app family. Per-scope toggles ("read mail but not send") would need a `scopes[]` field per provider and a more nuanced refresh contract.
- **Multi-account.** A user might want to connect personal + work Google accounts. Schema supports it in principle (drop the `(user, env, provider)` uniqueness); routing ("which account does this tool call use?") isn't designed.
- **HTTP/2 at the terminator.** gws/hyper ALPN-negotiate H2 with Google; the broker advertises only `http/1.1` in its server ALPN list, forcing fallback. Fine today; large uploads and long pagelists might benefit from H2 later.
- **Request-body streaming.** Today's broker buffers request bodies (Drive uploads, Gmail attachments). Multi-GB attachments would need streaming plumbing on both the inbound and outbound halves.
- **Encryption at rest for refresh tokens.** Plaintext in Postgres today, matching existing posture. A cross-cutting initiative would cover them uniformly.
