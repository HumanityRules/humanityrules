# Area 7: Agent-Built Workloads & Public Access

## Scope

Agent-created HTTP surfaces on the same Hermes task: Caddy host/path routing, process-supervisor port pool, same-origin Widgets (iframe is not a security boundary), admin registry endpoints, and human-approved `WebappPublicGrant` that can expose a Web App without the normal personal-assistant owner gate.

**In scope (HumR-owned):**

- Runtime: `template_repos/hermes_agent/humr_runtime/webapps/`, `humr_runtime/widgets/`, `humr_runtime/http_router/Caddyfile`, `humr_runtime/process_supervisor/`
- WebUI extensions: `webui-extension/humr-webapps.js`, `humr-widgets.js`
- CP: `humanityrules_app/views/webapp_public_access.py`, model `WebappPublicGrant`, PDP `api/pdp/evaluate-public`
- Policy-proxy public path (interaction with grants only): `template_repos/policy_proxy/policy_proxy/app.py`, `proxy.py`, `pdp.py`
- Template flag: `AppTemplate.enable_webapp_hosts`
- Design: `docs/app_workloads_design.md`

**Out of scope:** SSO cookie minting details (except public-grant interaction), credential broker internals, MCP, IAM editor, ECS provisioning, upstream Hermes under `vendor/`.

## Summary

The architecture correctly keeps the browser gate at the policy proxy, binds app workloads to loopback, generates Caddy routes from owned namespaces, and requires an org-admin human to mint public grants. Public-host parsing, identity-header stripping, and Widget snapshot path hardening are solid.

The highest-risk gap is that **managed port declarations are not re-validated when routes are generated**, so `webapps create --env WEBAPP_PORT=…` or `webapps set-env … WEBAPP_PORT=…` plus `reload`/`start` can point Caddy at privileged loopback services (WebUI, gateway API, process-compose, broker). Combined with a live public grant, that becomes anonymous internet access to those services. Platform-internal `__*` Web App slugs (including `__admin`) are also mutable via the CLI despite skill guidance. Widgets are intentionally same-origin with WebUI, so agent-written Widget JS is equivalent to XSS on the agent origin.

## Findings

### [HIGH] Managed `WEBAPP_PORT` can escape the 4000–4019 pool and retarget Caddy

- **Location:** `template_repos/hermes_agent/humr_runtime/webapps/webapps` (`apply_env_pairs`, `cmd_create`, `cmd_set_env`); `webapps_lib.port_from_entry` / `regenerate_webapp_routes` (lines 144–174); contrast `process_supervisor.managed_port_from_entry` (lines 246–284) which *does* range-check during allocation.
- **Issue:** Port allocation validates `4000–4019`, but route generation reads `WEBAPP_PORT` with no range check. `--env WEBAPP_PORT=8789` on create, or later `webapps set-env <slug> WEBAPP_PORT=8789` followed by `webapps reload` / `start`, regenerates `webapps.caddy` as `reverse_proxy 127.0.0.1:8789` (WebUI), or similarly `8642` (gateway API), `9956`/`9957` (process-compose), `9951` (broker). Readiness probes still use the originally allocated port, so the supervised process may fail while Caddy continues to proxy to the privileged listener.
- **Impact:** Authenticated traffic to `<slug>-<agent-host>` can reach platform loopback APIs the Web App was never meant to front. With a live `WebappPublicGrant`, **anonymous internet** can reach those same services (full WebUI session surface, agent gateway with the well-known loopback bearer, process supervision, integrations control API).
- **Next:** Reject `WEBAPP_PORT` / `WIDGET_PORT` in `apply_env_pairs` and any create `--env`; make `port_from_entry` / route regeneration call the same range + uniqueness checks as `managed_port_from_entry`; refuse to write a route whose target is outside the pool.

### [HIGH] Platform-internal `__*` Web Apps (including `__admin`) are CLI-mutable

- **Location:** `webapps_lib.SLUG_PATTERN` / `validate_slug` (lines 19–58) allow optional `__` prefix; `webapps` `cmd_unregister` / `cmd_delete` / `cmd_create` have no internal-slug guard; skill only documents “Don’t create or delete slugs starting with `__`” (`skills/development/webapps/SKILL.md` ~128). Bootstrap: `webui.sh` `bootstrap_admin_webapp` creates `webapp.__admin`.
- **Issue:** An agent can `webapps unregister __admin` / `delete __admin --yes` and recreate `__admin` (or any `__foo`) with an arbitrary command. Internal slugs are path-routed on the bare agent host (`route_block_internal`), same origin as WebUI, at `/webapps/__admin/`.
- **Impact:** Replacement of the read-only admin status API; same-origin malicious HTML/JS under `/webapps/__*/` for anyone who can open the agent host (PA owner, or anyone with `app:use`). Persistence across cold start is partial (`--if-missing` only recreates when missing), so a substituted entry survives until manually fixed.
- **Next:** Hard-refuse mutate/create/delete for `is_internal_slug` in the CLI (except a privileged bootstrap path); optionally make `__admin` registration owned solely by image boot, not the user CLI.

### [HIGH] Public Web Apps inherit full sandbox loopback reachability (SSRF / capability proxy)

- **Location:** Design `docs/app_workloads_design.md` §§189–196; skill documents intentional loopback Hermes API at `127.0.0.1:8642` with `API_SERVER_KEY` (`webapps/SKILL.md` ~136–157); key is fixed non-secret (`webui.sh` lines 24–32); nono profile allows those listen/open ports (`hermes-nono-profile.json`).
- **Issue:** A public grant opens the entire Web App hostname anonymously (PDP ignores path). The app process runs as the same sandbox uid with access to gateway, MCP aggregator, and sibling ports. Nothing in the runtime prevents the app from proxying those APIs on `/`. The skill warns “never expose it through your webapp to the public route,” but enforcement is advisory only.
- **Impact:** Prompt-injected or careless agent code + org-admin grant → unauthenticated callers drive the agent, burn credits, or touch other loopback surfaces the Web App chooses to forward. This is the product’s intentional trust of agent-written servers, amplified by public access.
- **Next:** Product: warn prominently on the grant confirm UI that the *process* can reach privileged loopback APIs. Engineering options: network-isolate public-marked workloads, deny outbound to non-pool loopback ports from `webapp.*` processes, or require an explicit “public-safe” capability flag before grant.

### [MEDIUM] Same-origin Widgets are a full agent-origin XSS surface

- **Location:** `docs/app_workloads_design.md` lines 26–27, 191–193; `humr-widgets.js` loads Widget URLs in an iframe with no sandbox attribute (lines 327–332); Caddy serves `/widgets/<slug>/` on the bare agent host (`widgets_runtime._widget_route_block`); broker is also bare-host path `/__humr_broker/*` (`http_router/Caddyfile` lines 11–18).
- **Issue:** Widget frontend JS shares the agent origin with WebUI and broker. Validated registry URLs (`validatedWidgetUrl`) prevent open redirects into the iframe, but once loaded, Widget code can `fetch` same-origin authenticated endpoints as the signed-in user (cookie sent to the policy proxy; session JWT remains HttpOnly but requests are authorized).
- **Impact:** Malicious or prompt-injected Widget source can call integrations/permissions APIs, read Web App admin JSON, and interact with WebUI state. Widgets are not publicly grantable (strength), so blast radius is “whoever can use the agent host,” typically the PA owner.
- **Next:** Keep documenting this as non-negotiable product trust. Consider `iframe sandbox` only if theme integration can move to `postMessage`; otherwise add CSP/`connect-src` tightening if WebUI CSP can exclude Widget paths from broker without breaking intentional calls.

### [MEDIUM] Web Apps admin API returns command and full environment

- **Location:** `humr_runtime/webapps/admin/server.py` `detail` (lines 57–79) returns `command`, `environment`, port, status; `logs` (lines 82–106) returns up to 2000 log lines. Served at `/webapps/__admin/api/...` on the bare host (authenticated via policy proxy only).
- **Issue:** Any principal with `app:use` on the agent can read process commands and every `set-env` value, including secrets the agent stored for the Web App. The WebUI panel is read-only and hides internals by default (`humr-webapps.js`), but the API does not.
- **Impact:** Secret disclosure within the set of users allowed to use the app (broader than PA-only if the app is ever shared). Logs may also contain tokens printed by buggy apps.
- **Next:** Redact or omit `environment` from the list/detail API (or return only non-secret keys); keep secrets out of process env where possible; confirm shared-app ABAC expectations.

### [MEDIUM] Public grants do not require the Web App to exist, and cover every path on the host

- **Location:** `webapp_public_access_create` (lines 150–181) validates slug regex only; `pdp_evaluate_public` (lines 26–69) allows on live grant without path checks; policy proxy caches allow by slug (`PublicWebappDecisionCache`, default TTL 10s).
- **Issue:** An org admin can grant `dashboard` before any such process exists. When the agent later creates and starts that slug, it is immediately public. PDP `path` is accepted then ignored—one grant publishes the whole origin. `expiry=never` is offered.
- **Impact:** Pre-positioned or typo’d grants; surprise exposure when a later app reuses the slug; long-lived anonymous surface if “never” is chosen and forgotten. Cache delay after revoke is bounded (~10s) and documented—acceptable but real.
- **Next:** Optionally require a registered routed slug (runtime signal or CP-side check); show “this slug is not running” on confirm; reconsider or gate `never`; keep path-blind grant unless product needs path-scoped public APIs.

### [MEDIUM] Generated router/supervisor state under `/workspace` is agent-writable

- **Location:** nono profile `workdir.access: readwrite` and `/workspace` allow (`hermes-nono-profile.json`); skills say do not hand-edit `webapps.caddy` / process-compose YAML; Caddy imports `/workspace/.config/caddy/*.caddy` (`Caddyfile` lines 8–9).
- **Issue:** The agent (or a Web App/Widget process as the same uid) can rewrite Caddy fragments or process-compose YAML directly, bypassing CLI validation entirely—same class of routing escape as the `WEBAPP_PORT` bug, without using `set-env`.
- **Impact:** Within the “agent owns the sandbox” model this is expected; combined with public grants it expands anonymous reach to whatever routes are written. No integrity check or root-owned regenerate-on-boot for fragments.
- **Next:** Treat as defense-in-depth: root-owned or signed route publish, or regenerate-and-verify routes on a timer from a non-writable template plus CLI-owned desired state outside agent write paths if the threat model includes malicious agent code.

### [LOW] `webapps create --cwd` is not confined to `/workspace/webapps/projects/`

- **Location:** `webapps` `cmd_create` (lines 36–44) resolves any existing directory.
- **Issue:** Working directory may be anywhere readable in the sandbox (`/tmp`, other workspace trees). `delete` only removes `projects/<slug>/`, so off-tree source is orphaned; more importantly, commands run with that cwd and inherited env.
- **Impact:** Mostly operational confusion; limited extra privilege beyond what the agent already has in-sandbox.
- **Next:** Warn or require cwd under `WEBAPP_PROJECTS_DIR` unless an explicit escape hatch is set.

### [LOW] No per-workload CPU/memory isolation

- **Location:** `docs/app_workloads_design.md` lines 195–196, 215; shared app-workloads process-compose project.
- **Issue:** A runaway Web App or Widget backend can starve WebUI/gateway and sibling workloads. Shared 20-port pool means exhaustion in one product blocks the other.
- **Impact:** Availability / DoS inside one agent task, not cross-tenant.
- **Next:** Product non-goal today; track if multi-tenant-on-one-task ever appears (it should not).

## Sound design notes

1. **Policy proxy remains the only browser gate.** Apps do not re-implement HumR auth. Public access is an explicit, org-admin-gated exception (`webapp_public_access.py` docstring; `webapps expose` only prints a CP deep link).
2. **Loopback + Caddy boundary.** Admin and user workloads bind `127.0.0.1`; Caddy `admin off`; unknown hosts get `404 unknown host` (`Caddyfile`).
3. **Host routing hygiene.** Proxy strips inbound `X-Forwarded-Host` and `X-Auth-*`, then sets `X-Forwarded-Host` from the real `Host` (`policy_proxy/proxy.py`). Tests cover forged Host/XFH and underscore identity aliases (`test_app_flow_public.py`).
4. **Public slug parsing is strict.** `_webapp_slug_for_host` requires a valid user slug (no `__` internals); internal apps stay path-routed on the bare host and are not grantable (`WebappPublicGrant.SLUG_PATTERN_TEXT`, confirm page rejects `__admin` prefill).
5. **Public PDP is org+env scoped** via env bearer → app lookup (`pdp_evaluate_public`); fail-closed when PDP is down; body size/chunked caps on anonymous traffic.
6. **Product namespaces.** `webapp.*` vs `widget.*` process names; separate Caddy fragments; Widgets refuse `__` slugs in core validation.
7. **Widget static snapshot hardening.** `O_NOFOLLOW`, symlink rejection, contained-path checks, atomic publish with rollback (`widgets_runtime.py`)—strong against filesystem escape during reconcile.
8. **WebUI Widget registry consumer** validates schema, slug, and exact same-origin URL before setting iframe `src` (`humr-widgets.js`).
9. **Grant lifecycle.** Soft-revoke history, partial unique live grant, lazy expiry at PDP, org-admin-only create/revoke, workspace-view for status—thoughtful CP controls.

## Open questions / needs product judgment

1. **Should a public Web App be allowed to call the loopback Hermes API at all?** Today the skill teaches the pattern and warns against exposing it; public grants make the warning load-bearing. Options: block loopback-to-8642/995x from public-marked processes, or require a second confirmation (“this app can run the agent”).
2. **Is “never” expiry acceptable for MVP demos?** Auto-expiry (24h default) is safer for forgotten grants; “never” may need org policy or audit alerts.
3. **Will non-PA app templates use webapp hosts?** If shared team apps get `enable_webapp_hosts`, admin API secret exposure and Widget same-origin risk expand beyond a single owner—ABAC and redaction become more urgent.
4. **Integrity of generated Caddy/YAML vs agent filesystem write.** Accept residual risk under “agent is the TCB,” or invest in non-writable publish paths before marketing public webapps broadly.
5. **Path-scoped public grants** (e.g. only `/` and static assets) vs whole-host publish—only needed if apps mix public UI with private admin routes on the same origin (they should not today).
