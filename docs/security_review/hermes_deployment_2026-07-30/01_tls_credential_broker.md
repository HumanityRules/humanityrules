# Area 1: TLS Credential Broker & Token Custody

## Scope

TLS-intercept credential broker and token custody for HumR-owned Hermes Agent deployment code only (not upstream `vendor/`). Covers: how OAuth refresh tokens, vault API keys, and device-flow tokens stay on the control plane (CP); how the env-resident broker MITMs known HTTPS hosts and injects short-lived (or vault) secrets; sandbox↔broker↔CP trust boundaries; and billing metering abuse/spoofing on this path.

Reviewed: `template_repos/hermes_agent/humr_runtime/integrations/` (broker runtime), `humanityrules_app/views/integrations/`, `env_bearer_auth.py`, `billing_usage.py`, credential models, WebUI thin client under `webui-extension/humr-integrations*.js`, design docs under `docs/integrations/`, `docs/billing_design.md`, `docs/app_removal_data_cleanup_audit.md`, `docs/platform_shared_credentials_design.md`, and `humanityrules_app/tests/test_integrations_broker.py` / related CP tests.

## Summary

The custody model is sound in its core claim: refresh tokens and HUMR OAuth client secrets stay on the CP; the sandbox only sees placeholders; injection happens outside nono with secrets held in broker memory. CP broker endpoints consistently bind env-bearer auth to org membership and app ownership for token minting, disconnect, vault setup, and device-complete. The main residual risks are (1) the opaque CONNECT tunnel as an unconstrained forward proxy from the sandbox, (2) the env-scoped bearer as a confused-deputy for every app/user in the environment, (3) an unauthenticated loopback control API reachable from the sandbox, and (4) known cleanup and plaintext-at-rest gaps. Billing metering on this path is intentionally fail-open and trusts the env reporter; spoofing is acknowledged deferred complexity, not a surprise.

## Findings

### [High] Opaque CONNECT tunnel is an unconstrained forward proxy (SSRF / IMDS)

- **Location:** `template_repos/hermes_agent/humr_runtime/integrations/tls_intercept.py:237–244`, `_tunnel_opaque` at `447–466`; nono profile blocks direct non-proxy egress (`hermes-nono-profile.json:87–120`) while allowing `open_port` to `9950`
- **Issue:** Unknown hosts are tunnelled with no destination allow/deny list: any `CONNECT host:port` the sandbox can send is opened from the broker process (root, outside nono, full task network). There is no filter for link-local (`169.254.169.254`, `169.254.170.2`), RFC1918, localhost, or non-443 ports. No `HttpPutResponseHopLimit` / IMDS hardening showed up in HumR infra code.
- **Impact:** A malicious skill (or anything already in the sandbox) can speak raw HTTP to the proxy and `CONNECT` to IMDS or other in-task / VPC targets the sandbox cannot reach directly. That can steal the ECS task role, hit loopback supervisor APIs the broker can reach, or pivot inside the customer VPC — defeating the nono network block via the broker’s own egress.
- **Next:** Deny-list link-local / metadata / private ranges (and optionally allow-list only 443) on the opaque path; set IMDSv2 hop limit 1 on tasks; treat proxy SSRF as a first-class threat model item for Team/Enterprise customer-cloud.

### [High] Env-scoped bearer is a cross-app / cross-user token minting deputy

- **Location:** `humanityrules_app/services/infra_customer/deploy_app.py` (shared `HUMR_ENV_BEARER` per env); `humanityrules_app/views/integrations/token_refresh_batch.py:77–100`, `broker_request_context.resolve_owned_app_slug`; same pattern on disconnect / device-complete / vault setup-session
- **Issue:** One `EnvironmentBearerToken` is injected into every app container in the environment. CP checks that `owner_username` is in the org and owns `app_slug`, but does **not** bind the caller to a specific app. Any holder of the bearer who knows `(owner_username, app_slug)` can mint short-lived secrets (and for vault providers, the long-lived key itself) for that pair. Design (`integrations_broker_design.md` failure mode “Customer env compromised”) acknowledges env-wide blast radius; tests only cover unowned/missing apps (`test_integrations_tokens_batch.py:594–633`), not cross-user minting with a valid ownership pair.
- **Impact:** Compromise of one HA container (or leak of the shared secret) yields access tokens / vault keys for **all** connected integrations of **all** users/apps in that environment, not only the compromised app’s owner. Lifetime is bounded for OAuth access tokens; vault keys are not.
- **Next:** Move to per-app (or per-broker) bearers, or require a signed app identity claim the CP verifies against the bearer; until then, document multi-app envs as a shared-fate security domain.

### [High] Integration credentials survive app removal (orphan refresh tokens / vault keys)

- **Location:** `docs/app_removal_data_cleanup_audit.md` (open action); `IntegrationUserCredential` in `humanityrules_app/models.py:1432–1486` (`app_slug` is a `SlugField`, not FK); no cleanup in app-removal executor
- **Issue:** Removing an app never deletes `IntegrationUserCredential` rows. Refresh tokens and vault secrets remain in the CP DB keyed by `(owner_user, environment, app_slug, provider)`.
- **Impact:** Recreating an app with the same slug under the same owner/env silently reconnects integrations (“phantom reconnect”). Also a GDPR/data-deletion gap: user-visible “remove app” does not remove stored third-party grants.
- **Next:** Unconditionally delete (and best-effort revoke) matching `IntegrationUserCredential` rows in `app_remove_executor`; add the regression test already listed in the audit.

### [Medium] Loopback control API has no auth; sandbox can drive credential lifecycle

- **Location:** `control_api.py` (all `/integrations/tls_intercept/...` routes); `humr_broker.py:203–205` binds `127.0.0.1:9951`; `hermes-nono-profile.json:102–115` grants `open_port` `9951`; design explicitly says “Sandbox → broker: no authentication”
- **Issue:** Disconnect, invalidate, refresh_all, setup-session, and device start/status/cancel are callable by any process that can open loopback:9951 — including the sandboxed agent. Identity for outbound HUMR calls is fixed by the broker’s `HumrClient` (`HUMR_OWNER_USERNAME` / `HUMR_APP_SLUG`), so the sandbox cannot pick another user, but it can act as that user.
- **Impact:** Malicious skill can disconnect integrations, force gateway/WebUI restarts via invalidate choreography, start device-flow sessions (phishing user_codes), or mint vault setup sessions for the app. Destructive DoS and device-flow social engineering within the app’s trust domain.
- **Next:** Require a broker-local shared secret or Unix peer credential check on mutating control routes; keep status read-only if needed; reconsider whether sandbox `open_port` to `9951` is required vs Caddy-only.

### [Medium] Vault (and shared) secrets are long-lived inside the env broker

- **Location:** Design `integrations_broker_design.md:19–21`, `117–121`; `tls_token_store.py` cache; vault `refresh_outcome` helpers return the stored API key / bot token as injectable secrets
- **Issue:** Custody narrative emphasizes short-lived OAuth access tokens, but vault providers (Telegram, Slack, OpenRouter, OpenAI API, Anthropic, Tavily, Browser Use, …) and platform/org-shared vault keys deliver the real long-lived secret into broker memory on every refresh. Env compromise therefore yields durable third-party keys, not hour-scale tokens.
- **Impact:** Matches “customer env compromised” for vault more severely than for OAuth: attacker keeps working credentials after rotating the env bearer until the upstream key is revoked.
- **Next:** Product-clear distinction in threat model; prefer OAuth/device where possible; for vault, consider broker-side short-lived wrapping or faster rotation hooks (e.g. Telegram managed rotation) as default posture.

### [Medium] Billing usage ingest trusts env reporter; weak attribution checks

- **Location:** `humanityrules_app/views/billing_usage.py:89–154`; `tls_usage_metering.py` (`UsageReporter` drop-on-failure); `docs/billing_design.md` §6.1, §6.3, §10.11
- **Issue:** Endpoint authenticates the env bearer and checks `app_slug` exists in the environment, but does **not** verify `owner_username` org membership or app ownership (unlike `token_refresh_batch`). Quantities are only schema-validated (non-negative ints); no rate/magnitude cap. Metering is fire-and-forget with no spool; enforcement (402) is designed but not present in the broker control API yet.
- **Impact:** Compromised or malicious broker can (a) attribute usage to arbitrary `owner_username` strings, (b) inflate token counts to drain org credits once rating/enforcement ship, or (c) under-report / drop events (intentional fail-open today). Cross-org spoofing is blocked by the bearer→org binding.
- **Next:** Align ingest with `resolve_owned_app_slug` + org membership; add sanity caps; keep fail-open undercharge until customer-cloud, then implement fraud-resistant reporting as named in billing design §10.11.

### [Medium] Refresh tokens and platform secrets stored plaintext in Postgres

- **Location:** `IntegrationUserCredential.credentials`, `IntegrationSharedCredential.credentials`, `PlatformSharedCredential.credentials` (`models.py:1462–1595`); acknowledged in `platform_shared_credentials_design.md` locked decision #4 and broker design open question “Encryption at rest”
- **Issue:** OAuth refresh tokens, vault API keys, and platform-tier secrets (blast radius: all customers) live as JSON plaintext in the CP database.
- **Impact:** CP DB compromise or overly broad staff/backup access yields permanent third-party grants. Platform rows amplify blast radius beyond a single org.
- **Next:** Cross-cutting Secrets Manager / envelope encryption initiative; prioritize `PlatformSharedCredential` first.

### [Medium] AlwaysInject hosts are an ambient confused deputy for the connected user

- **Location:** `tls_provider_catalog.py` Google / GitHub / Codex / Nous specs (`AlwaysInjectHeaders`); `tls_credential_injection.py:109–114`; `tls_intercept.py:396–444`
- **Issue:** Any sandbox request to a claimed host gets the managed credential rewritten in, replacing client Authorization. There is no per-tool or per-path scope gate on the broker. Broad host claims (e.g. Google’s `www.googleapis.com`, `oauth2.googleapis.com`; GitHub’s `github.com`) widen the deputy surface.
- **Impact:** By design the agent can call provider APIs as the user; a malicious skill can exfiltrate mail/repos or mutate state within granted OAuth scopes without ever reading the token. Not secret leakage into the sandbox, but full use of the grant.
- **Next:** Product judgment on tighter host lists, path allowlists, or ABAC/approval hooks for high-impact verbs; keep documenting this as intentional until governance exists (broker design open question “Governance”).

### [Low] Broker 502 / control errors may echo exception text to the sandbox or browser

- **Location:** `tls_intercept.py:366–372` (`broker provider error: {exc}`); `control_api.py:119–122`, `148`
- **Issue:** Upstream connect/TLS failures and some control-path `RuntimeError`s are returned as response bodies.
- **Impact:** Information disclosure (internal hostnames, libraries, paths) to the sandbox or WebUI; not direct secret leakage observed.
- **Next:** Map to generic client messages; keep detail in broker logs only.

### [Low] Device-flow refresh token briefly resides in broker memory and is POSTed to CP

- **Location:** `device_flow.py:322–333`; `credentials_service.complete_device_flow`; design `device_flow_integration_design.md` decision “Broker polls, HUMR stores”
- **Issue:** On connect, the refresh token crosses broker RAM and the env→CP channel once. Accepted by design because the broker is outside the sandbox.
- **Impact:** Memory disclosure or CP channel compromise during that window yields the long-lived grant. Steady-state refresh does not return refresh tokens to the broker.
- **Next:** Keep as documented residual risk; ensure logs never print token payloads (current code looks clean).

### [Low] Nono grants broad `/run` filesystem allow alongside CA read

- **Location:** `hermes-nono-profile.json:44–57`; leaf PEMs under `/run/humr/integrations-broker/private` at `tls_certificate_authority.py:96–97`, `169–174` (mode `0700` / `0600`, brief unlink)
- **Issue:** Design says the private leaf directory is “deliberately not granted,” but `/run` is in filesystem `allow` (readwrite). Unix `0700` root ownership is the real barrier for leaf key PEMs during the brief write window; CA private key stays in memory only.
- **Impact:** If nono path policy is ever interpreted more permissively than Unix DAC, or broker uid changes, leaf keys could become readable. Today likely blocked by permissions.
- **Next:** Narrow nono allow to `/run/humr/integrations-broker/ca` only (or explicit deny on `private`); avoid relying on DAC alone for the story.

### [Info] Billing enforcement and entitlement snapshot not yet on the broker path

- **Location:** `docs/billing_design.md` §7; `control_api.py` has no `/billing` route; `tls_usage_metering` reports only; `billing_usage.py` returns `{"ok": True}` without a balance snapshot
- **Issue:** Designed 402 / snapshot refresh loop is not implemented in the reviewed broker code.
- **Impact:** No credit enforcement yet; metering is shadow/ingest-only. Not a vulnerability today, but spoofing findings above become enforcement-bypass findings when §7 lands.
- **Next:** When implementing enforcement, close attribution/caps on ingest in the same change set.

### [Info] Platform-shared provenance drives metering correctly under one lock

- **Location:** `tls_token_store.py:269–284`, `382–403`; `tls_intercept.py:340–346`; `tls_usage_metering.tap_for_request`
- **Issue:** (Strength, noted as Info for completeness.) `platform_shared` is read from the same locked snapshot as secrets; only platform-funded Codex responses are tapped.
- **Impact:** Customer-funded credentials are not billed; tap failures do not break the relay.
- **Next:** None for custody; keep this invariant when adding providers to `_PARSED_PROVIDER_SLUGS`.

## Sound design notes

1. **Custody split is real.** Refresh tokens / OAuth client secrets stay on CP (`IntegrationUserCredential`, `IntegrationConfig`); broker cache holds only injectable secrets and never writes them to durable disk (`tls_token_store.py`, broker design “What lives where”).
2. **Sandbox never holds the MITM CA private key.** Boot-generated CA in process memory; leaf PEMs transient under `0700` private dir; sandbox trusts `bundle.pem` only (`tls_certificate_authority.py`).
3. **Injection policy refuses BYO/foreign credentials** on placeholder providers and strips/replaces Authorization when required (`tls_credential_injection.py` `SecretSelectionError` paths; covered in `test_integrations_broker.py`).
4. **Host routing is exact and fail-closed on conflicts.** `normalize_connect_host` + duplicate-host rejection at catalog build; unknown hosts do not get credentials (they tunnel — see SSRF finding).
5. **CP broker envelope is consistent.** Env bearer hashed lookup with `hmac.compare_digest` (`env_bearer_auth.py`); ownership via `ResourceTag` owner on token, disconnect, device-complete, and vault setup paths.
6. **Vault setup tokens are origin-bound and signed.** `user_credential_vault.py` ties submit/poll to signed `allowed_origin` under the env’s hosted zone; credentials POST browser→CP without entering the sandbox.
7. **Google disconnect tombstones** prevent racing OAuth callbacks from resurrecting a revoked grant (`provider_disconnect.py` / `provider_google.py` `TOMBSTONE_ON_DISCONNECT`).
8. **Transient HUMR failures do not flip connected→absent** or wipe a still-valid cache (`tls_token_store.py`), avoiding availability-induced lockouts that would push users toward unsafe workarounds.
9. **Metering is observe-only** and cannot break the byte relay (`tls_http_message_relay._GuardedObserver`; Accept-Encoding stripped only when tapped).
10. **Resolution ladder and platform ABAC-free schema** keep global credentials out of per-org policy evaluation by construction (`platform_shared_credentials_design.md`, `token_refresh_batch.py` fallback order).

## Open questions / needs product judgment

1. **Is multi-app / multi-user per environment an accepted shared-fate domain**, or should per-app bearers land before Team/Enterprise customer-cloud?
2. **Opaque tunnel policy:** allow-list public HTTPS only, or keep “full internet via broker” and accept SSRF mitigations (IMDS hop limit + deny-lists) as sufficient?
3. **Should mutating `/__humr_broker` routes be unreachable from the sandbox** (Caddy/WebUI only), given skills can already misuse injected grants via 9950?
4. **App-removal credential cleanup:** always delete (audit recommendation) vs rare “keep credentials” escape hatch?
5. **Governance / scope granularity** (broker design open questions): when do host/path allowlists or approval gates become required for AlwaysInject providers?
6. **Encryption at rest** timeline for platform-tier secrets vs personal refresh tokens?
7. **Billing:** when enforcement ships, is trusted env reporting still acceptable for Operator-on-HumR-infra only, and what is the bar for customer AWS (design §10.11)?
