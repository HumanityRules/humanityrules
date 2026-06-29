# Platform-shared & OAuth-shared integration credentials

## Context

`docs/shared_credentials_design.md` covers **org-shared** credentials: an org admin shares a
vault key with a user / workspace / everyone *inside their org*, authorized by ABAC, with the
org's share winning over a user's personal key. This doc covers two extensions on top of it:

1. **Platform tier** — HumR (the vendor) shares a credential with *all* customer orgs. First
   use case: a platform Tavily web-search key.
2. **OAuth/device sharing** — today only vault *keys* are shareable. Extend sharing to
   OAuth/device (refresh-token) credentials. First use case: a platform Codex (ChatGPT) login.
   The same mechanism later lets a customer admin share their own OAuth/device login within
   their org.

## Mental model — the resolution ladder

For one `(user, app, provider)`, `integrations_tokens_batch` resolves top-down and the **first
usable secret wins**:

1. **org-shared** credential — existing; ABAC; org wins over personal.
2. **personal** pasted key — existing.
3. **platform** credential — new; global; HumR-provided.

A customer's own credential (org-shared or personal) auto-overrides the platform default — no
opt-out step. The platform tier is a *default*, never mandatory.

## Locked decisions

1. **Precedence `org-shared > personal > platform`.** The platform credential is the
   lowest-priority fallback; a user's own pasted key wins over it automatically (decision 2a).
2. **The platform tier is ABAC-free.** "All customers" is global by construction. ABAC is
   per-org and cannot evaluate a credential against a *different* org's policies, so platform
   credentials bypass the engine entirely — they are never in a queryset
   `filter_permitted_credentials` sees.
3. **Two tables, not one.** Platform credentials live in a new `PlatformSharedCredential`
   (no org FK, no ABAC). Org credentials stay in `IntegrationSharedCredential` (ABAC). They
   differ on exactly one axis — authorization — which is the thing that genuinely differs.
   Keeping platform rows out of the org table makes "a global credential is never
   ABAC-evaluated against the wrong org" true *by schema*, not by convention.
4. **Plaintext at rest (v1).** Mirrors the existing `IntegrationSharedCredential` /
   `IntegrationUserCredential` debt (JSON in Postgres). Acknowledged: a platform secret's blast
   radius is every customer; revisit with Secrets Manager.
5. **v1 audience = all orgs.** Targeted platform sharing ("only these orgs", "only this plan
   tier") is designed-for but **not built**: it is one membership check at the resolver
   chokepoint plus an `audience` / `target_organizations` addition. With `audience=all`, newly
   onboarded orgs auto-receive the share.
6. **Opt-out / connector-disable deferred.** "An org makes connector X unavailable to all its
   employees regardless of key source" is a separate org-level feature that plugs into the same
   resolver chokepoint. Not in v1.
7. **Management via the unified Provider Keys UI (delivered) + Django admin (fallback).** Platform
   credentials are created from the org-admin Integrations → Provider Keys tab by the
   **platform-owner org** — a single org named by slug in `settings.HUMR_PLATFORM_OWNER_ORG_SLUG`
   (mirrors the `HUMR_SANDBOX_*` settings pattern; `platform_owner.is_platform_owner_org` is the
   gate). That org's admins get an extra **"All customers"** scope on the same share form; every
   other org sees only the per-org scopes. The gate is enforced server-side on every write — UI
   hiding is not sufficient for a global credential. The superuser `PlatformSharedCredentialAdmin`
   remains as a staff fallback.

## Shared service (Phase 2) — OAuth/device login + central refresh

Tier-agnostic, built once, used by both platform and org sharing:

- **Control-plane-native login.** Today the device-code flow runs in the customer broker, which
  POSTs the resulting refresh-token to HumR. A *shared* credential has no per-user broker
  driving it, so the **control plane** drives the device/OAuth flow and stores the refresh-token.
  Org OAuth sharing reuses this from the customer admin UI.
- **Central rotation + token fan-out.** A shared refresh-token must be exchanged in one place.
  The control plane holds the refresh-token, exchanges it for a short-lived access-token, caches
  that access-token, and fans it out to every requesting broker **behind a lock** — so
  concurrent broker refreshes do not each rotate the token and orphan the others. Codex's refresh
  already runs control-plane-side (the refresh-token never reaches the broker); the new part is
  the shared cache + lock. **Implemented (Phase 2a) DB-backed:** the access-token bundle is cached
  on the credential row (`token_cache` JSONField) and concurrent refreshes serialize via
  `select_for_update` (Postgres row lock; no-op on SQLite) — the refresh-token rotation already
  needs a DB write, so caching in the same locked transaction is atomic and needs no extra infra.
  `provider_common.run_shared_refresh_exchange` is the generic engine;
  `provider_openai_codex.refresh_outcome_from_shared` is the first consumer.
- **Pool-ready shape.** Key the central cache by *backing login*, not by provider, so a future
  pool of N accounts (to spread one provider's rate limits — relevant at both platform and org
  scale) is additive, not a refactor. v1 ships N=1.

## Phasing

**Phase 1 — platform vault keys (Tavily).** No new auth machinery.

- New `PlatformSharedCredential` model (+ migration).
- `platform_credential_resolver.resolve(provider)` → the `enabled` row for the provider, or None.
- Wire into `integrations_tokens_batch` as the **lowest-priority fallback** (after the personal
  refresh), reusing each vault provider's existing `refresh_outcome_from_shared` (duck-typed —
  Tavily needs no change). Mark the outcome `platform_shared` in metadata for the status card.
- `PlatformSharedCredentialAdmin` (superuser), live-validating the key via the provider's
  `validate_shared_key`.
- Tests: resolver precedence, and the batch HTTP path (platform used when nothing else applies;
  personal and org-shared both override platform).

**Phase 2 — OAuth/device login + central refresh.** Delivers platform Codex *and* org-level
OAuth/device sharing via the shared service above. Split into three increments:

- **Phase 2a (DONE) — the central-refresh engine.** `token_cache` on both shared models;
  `provider_common.run_shared_refresh_exchange` (cache + `select_for_update` + exchange + rotate);
  `provider_openai_codex.refresh_outcome_from_shared` (+ `provider_nous.refresh_outcome_from_shared`).
  The platform fallback in `token_refresh_batch` already dispatches it by `getattr`, so a populated
  shared OAuth credential works end-to-end with no further wiring.
- **Phase 2b/2c (DONE) — one unified credential-sharing dialog.** The originally-separate 2b
  (CP device-login UI) and 2c (org-level OAuth sharing) ship as a single surface: the org-admin
  Integrations → Provider Keys tab has **one "Add shared credential" dialog** whose body adapts to
  the chosen provider — **paste a key** (vault providers) or **connect a login** (device-flow OAuth
  — Codex, Nous). The provider dropdown spans both kinds; selecting one toggles the API-key field
  vs. a connect note client-side (JS keyed on a provider→kind map, driven by the `el-select` change
  event + a `value`-attribute MutationObserver), and the add/edit endpoints **dispatch by provider
  kind** server-side. Each share targets everyone / a workspace / a user (per-org
  `IntegrationSharedCredential`) or — for the platform-owner org only — **all customers**
  (`PlatformSharedCredential`); both paths route writes through `shared_credential_store`, which
  owns the table choice + the platform-owner gate. Each device-flow provider exposes
  `device_authorize()` (start) + `device_poll(opaque)` (one attempt); shared dataclasses
  (`DeviceAuthorization`, `DevicePollResult`) live in `provider_common`. The control plane drives
  the handshake (a shared credential has no per-user broker); Django is request-scoped, so the
  admin's browser **self-polls**: submitting a login provider swaps the same dialog to a
  code/verification-link stage that htmx-polls `poll` every few seconds; each poll is one
  `device_poll`; on approval the refresh-token is stored on the routed row. The UI + device flow
  live in one module (`views/integrations/org_shared_keys.py`); in-flight state lives in
  `request.session`, so identity + the platform gate are re-derived from the live request, never
  trusted from the session.

**Reserved, not built:** redirect-OAuth providers (Google / GitHub / X — they need a HumR-hosted
callback + per-provider `refresh_outcome_from_shared`); audience targeting; account pool;
opt-out / connector-disable.
