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
7. **Management via Django admin (v1).** Platform credentials are HumR-staff-only; the superuser
   Django admin is the input surface. The "platform-owner org" / master-account UI (a gated
   "share with all customers" checkbox) is deferred until there is a customer-facing master
   account — it is not needed while management is superuser-only.

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
  the shared cache + lock.
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
OAuth/device sharing via the shared service above. Extends `IntegrationSharedCredential` (org)
and `PlatformSharedCredential` (platform) to the OAuth/device kind; adds the control-plane login
UI and the central-refresh cache.

**Reserved, not built:** audience targeting; account pool; opt-out / connector-disable;
master-account UI.
