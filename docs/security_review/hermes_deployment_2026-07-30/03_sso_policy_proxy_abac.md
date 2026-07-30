# Area 3: SSO, Policy Proxy, PDP & ABAC

## Scope

Who may open a Hermes URL: env-level Okta/OIDC (and WorkOS) auth, JWT session cookies, per-request PDP evaluation against ABAC policies (owner + admins for personal assistants), decision caching, fail-closed behavior, public-path evaluation for webapps, and multi-tenancy/org scoping at the authz layer.

**In scope (HumR-owned only):**

- Runtime: `template_repos/policy_proxy/` — `app.py`, `proxy.py`, `jwt_verify.py`, `pdp.py`, `pdp_cache.py`, `activity_reporter.py`, `config.py`
- CP: `humanityrules_app/views/auth_env_sso.py`, `views/pdp.py`, `views/policy_proxy_activity.py`, `views/env_bearer_auth.py`, `services/abac_service.py`, `views/security_abac.py`, `views/abac_view_checks.py`, `views/apps.py` (`app_tags_save`), `views/webapp_public_access.py` (grant → PDP public path)
- Models: `Policy`, `IdentityAttribute`, `Group*`, `ResourceTag`, `EnvironmentBearerToken`, `WebappPublicGrant`
- Docs: `docs/policy_proxy_design.md`, `docs/authorization_design_abac.md`, `docs/tenant_isolation_rls_note.md`
- Template wiring: `seed_app_templates.py` policy-proxy container; `deploy_app.py` env overlays (`HUMR_*`)
- Local stand-in: `template_repos/hermes_agent_local/local_policy_proxy/` (dev only)

**Out of scope:** Credential MITM, MCP, ECS provisioning details (except JWT/cookie domain assumptions), IAM permissions editor, webapp process isolation (public grant / evaluate-public PDP path is in scope; agent-process blast radius is Area 7).

## Summary

The gate is well-structured: central RS256 session JWTs with `aud=env_domain`, HttpOnly Secure cookies, identity-header stripping (including underscore aliases), org-scoped PDP lookups by IdP `sub`, env-bearer hashing, deny-by-default ABAC with self-referential PA owner policy, and fail-closed behavior when the PDP is unreachable. Personal Assistants correctly skip the open-access default app policy.

The highest-severity gap is **session lifetime**: production mints **30-day** JWTs with **no revocation**, against a design that specified 1 hour. Combined with mutable ABAC anchors (`IdentityAttribute` `username`, editable `owner` / `app-type` tags) and nondeterministic env resolution on the **shared sandbox hosted zone**, residual risk is concentrated in long-lived stolen sessions, admin footguns that transfer PA ownership, and sandbox SSO org/IdP confusion for OIDC orgs.

## Findings

### [CRITICAL] Session JWT TTL is 30 days with no revocation

- **Location:** `humanityrules_app/views/auth_env_sso.py` lines 41, 153–168 (`SESSION_TTL_SECONDS = 30 * 24 * 60 * 60`); contrast `docs/policy_proxy_design.md` line 68 (“TTL: 1 hour”) and deferred “Session revocation lists” (line 17).
- **Issue:** After IdP login, the control plane mints a session JWT valid for 30 days. There is no denylist, no server-side session store, and no way to invalidate a stolen `humr_session` cookie short of rotating the central signing key (which logs everyone out). PDP still re-evaluates ABAC (subject to cache), so removing org membership or attributes eventually denies — but a still-valid user’s stolen cookie works for a month without re-auth, and IdP-side logout does not cut the env session.
- **Impact:** Laptop theft, XSS on a same-registrable-domain page that can read non-HttpOnly side channels, or ALB/access-log capture of install URLs (see Medium finding) yields long-lived access to every policy-proxy’d app the victim is still authorized for. Offboarding that only disables the IdP account does not kill existing env JWTs until attributes/membership change propagate past the PDP cache.
- **Next:** Align TTL with the design (hours, not weeks); add revocation (jti denylist, version claim checked at PDP, or short TTL + silent refresh); document that IdP logout ≠ env session kill until then.

### [HIGH] Shared-sandbox SSO resolves env by hosted zone alone — org/IdP binding is nondeterministic

- **Location:** `humanityrules_app/views/auth_env_sso.py` `_resolve_env_for_rd` (lines 97–121) scans **all** `Environment` rows; `sandbox_service.ensure_org_sandbox` (lines 48–54) sets every org’s sandbox env to the **same** `HUMR_SANDBOX_HOSTED_ZONE` and slug `"sandbox"`. Callback re-resolves and only checks `env.slug == env_slug` (lines 253–256), which is always `"sandbox"` for every sandbox org.
- **Issue:** Many orgs share one DNS zone. Longest-suffix matching does not disambiguate equal-length zones; whichever row the queryset yields first wins. For WorkOS this mostly still works (global WorkOS client; `aud` is the shared zone). For OIDC, start and callback may bind **different orgs’** `oidc_client_id` / issuer / secret to the same `rd`, causing failed exchanges or — worse — a login dance against the wrong customer IdP when resolution flips between requests.
- **Impact:** Flaky or wrong-IdP SSO on the shared sandbox; confused-deputy risk if an OIDC org’s authorize URL is shown to a user intending another org’s app on the same zone. Dedicated customer zones (unique per account) are largely unaffected.
- **Next:** Resolve env from the **app hostname label** (app slug → `App` → org/env), not zone alone; or include a stable env/org id in signed state and refuse callback if zone match is ambiguous; never key sandbox uniqueness on `env_slug` alone under a shared zone.

### [HIGH] PA ownership anchors are mutable after deploy (`owner` / `app-type` tags)

- **Location:** `humanityrules_app/views/apps.py` `app_tags_save` (lines 271–293) deletes all direct app tags and recreates from POST; gated only by `workspace:admin`. Design explicitly deferred hardening: `docs/policy_proxy_design.md` line 19. Seed PA tags: `seed_app_templates.py` `default_tags` app-type; `template_deploy_service._stamp_template_tags` owner tag (lines 123–145).
- **Issue:** Anyone with `workspace:admin` (org admins via seed policies; others if custom policies grant it) can change `owner` to another username, remove it, or drop `app-type=personal-assistant`. The global PA policy (`username = $resource.owner` ∧ `app-type=personal-assistant`) then grants `app:use` to the new owner or stops matching entirely. There is no immutability flag, no audit-only path, and no distinction between decorative tags and authorization-critical tags.
- **Impact:** Silent PA takeover or lockout without redeploy; also breaks broker ownership checks that trust the same `owner` ResourceTag. Accidental bulk-tag edits are as dangerous as malicious ones.
- **Next:** Treat `owner` and `app-type` (and optionally `app-name`) as system tags: reject delete/replace in `app_tags_save`; require an explicit transfer API with membership validation; surface them as read-only in the tag UI.

### [HIGH] ABAC `username` identity attribute is editable and multi-valued

- **Location:** `User.save` locks `User.username` after creation (`models.py` lines 46–57); ABAC PA policy matches `IdentityAttribute` key `username` (`abac_service.bootstrap_organization` lines 690–697; `materialize_membership` lines 608–613). Mutation UI: `security_people_attribute_add` / `_remove` (`security_abac.py` lines 171–199) and group attributes (lines 393–420) with **no** reserved-key checks. Uniqueness is `(org, user, key, value)` — a user may hold multiple `username` values.
- **Issue:** Org admins can set Alice’s `username` IA to Bob’s username (or add it alongside her own). Effective attributes OR values per key, so Alice matches `$resource.owner` for Bob’s PA. Removing or altering the seeded username IA desyncs ABAC from the immutable `User.username` the product claims is the stable anchor (`policy_proxy_design.md` lines 180–182).
- **Impact:** Privilege escalation to another member’s personal assistant (and any other self-referential `username = $resource.*` policies) by a compromised or malicious org admin — without changing `User.username`.
- **Next:** Make `username` (and ideally `org-role`) system-managed: sync only from `materialize_membership` / role changes; reject manual add/remove/edit; enforce at most one value; optionally derive username at evaluation time from `User.username` instead of a mutable IA row.

### [MEDIUM] Session JWT is delivered in a URL query string

- **Location:** `auth_env_sso.env_callback` builds `https://{rd_host}/__humr_session_install?token=...&rd=...` (lines 287–293); install handler sets cookie then redirects (`policy_proxy/app.py` lines 275–310) with `referrer-policy: no-referrer`.
- **Issue:** The full session JWT appears in browser history, possible IdP/relay Referer headers before the install response, and infrastructure access logs (ALB, CDN, reverse proxies) on both the control plane redirect and the env host. `referrer-policy` on the install **response** does not protect the request that delivered the token.
- **Impact:** Token theft via logs or history yields the same access as cookie theft for the remaining JWT lifetime (currently up to 30 days).
- **Next:** Prefer fragment (`#token=`) or a one-time code exchanged over POST for the cookie; shorten TTL; scrub install paths from access logs; consider binding install to a short-lived code rather than the session JWT itself.

### [MEDIUM] PDP allow decisions stay warm for ~60s after revocation

- **Location:** `policy_proxy/config.py` default `HUMR_PDP_CACHE_TTL_SECONDS=60` (line 72); `pdp_cache.PdpDecisionCache` caches allow and deny alike (`pdp_cache.py` lines 58–64; tests assert deny caching). Proxy fails closed only when cache misses and PDP is down (`app.py` lines 146–147, 327–330).
- **Issue:** After an admin removes `app:use` (membership, attributes, owner tag, policy), a previously allowed user can keep reaching the app until the cache entry expires. Public-grant cache defaults to 10s (`HUMR_PUBLIC_CACHE_TTL_SECONDS`) — better — but identity cache is 6× longer. Design accepted warm-cache continuity; document and size deliberately.
- **Impact:** Short revocation lag on PA access and any future shared `app:use` grants. Not a bypass past TTL; amplifies CRITICAL session longevity for the last minute after lockout.
- **Next:** Keep TTL small; consider not caching allows longer than denies, or push invalidation when ABAC objects change; expose metrics for cache hit age on deny-worthy events.

### [MEDIUM] Seed / system policies are fully editable and deletable

- **Location:** `Policy.is_system` help text: “Display-only flag” (`models.py` line 1359); `security_policy_detail` / `security_policy_delete` (`security_abac.py` lines 597–703) apply no `is_system` guard. Matches ABAC design (“org admin can edit or delete them (at their own risk)”, `authorization_design_abac.md` lines 66–67).
- **Issue:** Deleting “Personal Assistant: owner access” or “Org admins: app usage”, or replacing conditions with `*` → `app:use` on broad resource conditions, silently changes the security posture of every PA / app in the org. UI does not warn that seed policies are load-bearing for Hermes.
- **Impact:** Org-wide lockout or org-wide open access from a single policy edit; hard to detect without an access explainer.
- **Next:** Soft-lock system policies (confirm + type name); prevent delete of PA owner / admin app:use seeds; add “who can open this PA” explainer on app detail.

### [MEDIUM] Session JWT `username` claim is the email, not the ABAC username

- **Location:** `auth_env_sso.env_callback` mints `username=email` for both OIDC and WorkOS (lines 271–281). Proxy injects `X-Auth-User` from that claim (`proxy.py` lines 204–208). PDP **ignores** request username and authorizes via `sub` → `User` → attributes (`pdp.py` lines 133–154).
- **Issue:** Authorization is correct (sub-based), but any upstream that trusts `X-Auth-User` / `X-Auth-Email` as the HumR username will see the email address. Prefill / owner tooling elsewhere uses `User.username`, which may differ from email.
- **Impact:** Identity confusion and mis-attribution in app logs or future header-based features; not an authz bypass today because Hermes template code does not appear to consume these headers yet.
- **Next:** Mint `username` from the resolved HumR `User.username` after IdP exchange (require org membership before minting), or stop sending a misleading claim until consumers exist.

### [MEDIUM] Platform `is_superuser` bypasses all app ABAC

- **Location:** `pdp.py` lines 105–114 — unscoped `User` lookup by IdP sub with `is_superuser=True` → allow with reason `platform-admin`.
- **Issue:** Intentional cross-tenant break-glass for HumR staff. Any account with `is_superuser` and a matching WorkOS/OIDC sub that can complete env SSO receives `app:use` on every HA behind that env’s bearer, without org membership.
- **Impact:** Compromised platform-admin IdP identity is a skeleton key to customer personal assistants. Blast radius is all policy-proxy apps reachable with any env bearer the attacker can cause to evaluate (or any env they can log into).
- **Next:** Require dual control / break-glass audit; time-box superuser sessions; optionally require org membership even for superusers except a separate staff tool; alert on `reason=platform-admin` allows.

### [MEDIUM] `evaluate-public` ignores path and does not prove the webapp exists

- **Location:** `pdp.py` `pdp_evaluate_public` (lines 26–69) — live `WebappPublicGrant` on `(app, slug)` only; `path` accepted by the proxy client (`pdp.py` evaluate_public) but unused. Grant create (`webapp_public_access.py`) validates slug shape only, org-admin gated.
- **Issue:** A grant makes the **entire** webapp hostname anonymous; no path restriction. Granting a slug before the agent registers that webapp pre-authorizes whatever later binds to that hostname (Area 7 covers process retargeting). Proxy public path correctly skips session and strips identity headers (`app.py` lines 105–121; `proxy.py` lines 183–185).
- **Impact:** Intentional product shape for demos, but path-blind public access plus agent-controlled upstream increases blast radius of a single grant (see Area 7). PDP side cannot narrow by path today.
- **Next:** Product warning on grant UI; if route overrides ever apply to public hosts, key public cache on path and evaluate path in PDP; optionally require the slug to exist in a CP-visible registry before grant.

### [LOW] Public decision cache is attacker-keyed (DoS / eviction), not privilege escalation

- **Location:** `pdp_cache.PublicWebappDecisionCache` (lines 73–115); slug from Host via `_webapp_slug_for_host` (`app.py` 59–75); `max_entries=512`, default TTL 10s.
- **Issue:** Anonymous clients can force PDP public calls and fill the cache with denies for fictional slugs, evicting a live allow. Host normalization (port/case/trailing dot) and slug regex limit key diversity somewhat.
- **Impact:** Brief forced re-auth / 503-like friction for a public webapp under scan load; not an allow forgery (fail closed on PDP errors).
- **Next:** Negative-cache shorter TTL than allows; rate-limit evaluate-public per app; ignore caching for definite non-grants if cheap.

### [LOW] PDP `path` is unused and identity cache is not path-keyed

- **Location:** `pdp.py` accepts `path` but never evaluates route overrides; `PdpDecisionCache` keys only `(provider, sub)` (`pdp_cache.py` lines 1–11, 45–56), documented as intentional until route overrides land.
- **Issue:** Design docs describe route-level narrowing (`authorization_design_abac.md` lines 332–357); implementation has none. When overrides land, forgetting to path-key the cache would widen access incorrectly.
- **Impact:** Latent footgun; no current bypass.
- **Next:** When implementing route overrides, key cache on path pattern (or disable cache for overridden apps) and add tests for allow-on-/ vs deny-on-/admin.

### [LOW] Local policy proxy is a full auth bypass — confirm it never ships to prod

- **Location:** `template_repos/hermes_agent_local/local_policy_proxy/` — forwards all traffic with no SSO/PDP (`app.py` lines 1–47). Prod Hermes template uses `template_repos/policy_proxy` via `role: policy_proxy` (`seed_app_templates.py` lines 164–190), not the local stand-in.
- **Issue:** Correct for compose/dev. Risk is operational: copying the local image/path into a customer template or disabling the real proxy container.
- **Impact:** Complete unauthenticated exposure of Hermes if deployed by mistake.
- **Next:** Keep `hermes_agent_local` out of `seed_app_templates`; CI assert prod templates never reference `local_policy_proxy`; README already marks it dev-only.

### [INFO] Env bearer can probe PDP for any `app_id` in the environment

- **Location:** PDP trusts bearer → env, then client-supplied `app_id` (`pdp.py` lines 116–131); proxy bakes `HUMR_APP_ID` at deploy time. Design accepts self-reported app_id inside the env trust boundary (`policy_proxy_design.md` lines 125–126).
- **Issue:** Any container holding `HUMR_ENV_BEARER` (policy proxy; also integrations/Hermes when `requires_env_bearer`) can ask allow/deny for other apps in the same env and enumerate public grants via evaluate-public.
- **Impact:** Cross-app information disclosure within the env compromise boundary — consistent with “env is one trust domain,” weaker than per-app attested identity.
- **Next:** Optional per-app audience on the bearer or attested app_id claim; out of scope to fully fix without broader broker identity work (see Area 2).

## Sound design notes

1. **Audience = env DNS zone, not slug** — Blocks cross-env JWT replay when slugs collide across AWS accounts (`jwt_verify.py` 32–52; `auth_env_sso` mint with `aud=env_domain`; tests in `test_auth_env_sso.py` / `test_jwt_verify.py`).
2. **Algorithm allowlist** — RS256/EdDSA only; rejects `alg=none` style downgrades (`jwt_verify.py` 11–12, 46–49).
3. **Identity headers stripped inbound** — Including underscore aliases so WSGI cannot rehydrate spoofed `X-Auth-*` (`proxy.py` 83–96, 120–141, 245–260). Session cookie stripped before upstream.
4. **Fail closed** — PDP transport errors → 503 / WS 1011 when cache cold; no local passthrough mode implemented despite older design text about `*` optimization.
5. **Org scoping on PDP** — App must belong to bearer’s env’s organization **and** that environment; user must be an org member looked up by provider-specific sub; outsider org members get `user-not-found` (`test_pdp_endpoint.py`).
6. **Env bearer stored hashed** — SHA-256 lookup + `hmac.compare_digest` (`env_bearer_auth.py`); sandbox secrets namespaced per org.
7. **PA open-access default suppressed** — Templates with a `policy_proxy` container skip `create_default_app_policy` open `*` grant (`abac_service.py` 761–763).
8. **Return URL validation** — HTTPS + host suffix of env domain on both CP `_resolve_env_for_rd` (scheme check) and proxy `_is_valid_return_url` / session install (`app.py` 178–185, 281–282). Leading-dot suffix checks avoid the classic `evilcustomer.com` vs `customer.com` string-suffix bug.
9. **Cookie flags** — `Secure; HttpOnly; SameSite=Lax; Domain=.<env_domain>` for one login across apps in an env, with per-app PDP still enforced.
10. **Public grants are org-admin-only** — Creating/revoking internet exposure is not a workspace-member action (`webapp_public_access.py` 57–60, 150–155).
11. **Username immutability on `User`** — Correct instinct for the ownership anchor; the gap is that ABAC reads the mutable IA copy instead.

## Open questions / needs product judgment

1. **Session lifetime vs UX** — Is 30 days intentional product preference over the design’s 1 hour? If yes, revocation becomes mandatory for enterprise offboarding claims.
2. **Org-admin as PA superuser** — Today org admins get `app:use` on all apps via seed policy. Should PA access be owner-only even for admins (break-glass separate)?
3. **Shared sandbox SSO** — Accept zone-level cookie sharing across all sandbox orgs, or isolate cookies/audiences per org despite one DNS zone?
4. **Public webapps vs PA gate** — Confirm product acceptance that a public grant fully bypasses PA owner ABAC for that hostname (anonymous, no `X-Auth-*`), including interaction with Area 7 loopback reachability.
5. **Route overrides** — Still unimplemented; keep them deferred or schedule before any customer needs path-scoped admin surfaces on Hermes.
6. **RLS / org-scoped managers** — `docs/tenant_isolation_rls_note.md` remains unimplemented; PDP paths reviewed here are carefully scoped, but the class of bug remains for other views.
7. **Mint-only-after-membership** — Should env SSO refuse to mint a session JWT for IdP subjects that are not HumR org members (fail at callback instead of at first PDP deny)?
