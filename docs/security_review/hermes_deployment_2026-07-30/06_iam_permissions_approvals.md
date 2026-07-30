# Area 6: IAM Task-Role Permissions & Approvals

## Scope

HumR-owned code and architecture for expanding an app’s ECS task-role IAM policy: draft statements, ABAC `environment:approve` on Apply, apply executors, HumR UI editor, and the Hermes WebUI self-referential editor relayed through `humr_broker` `/permissions/*`. Focus is privilege escalation into the customer AWS account via IAM policy changes.

**In scope paths**

- Models: `AppPermissions`, `AppPermissionRequest` (`humanityrules_app/models.py`)
- CP: `humanityrules_app/services/permissions_service.py`, `views/security_permissions_editor.py`, `views/permissions_api.py`, `services/jobs/permissions_apply_executor.py`, `services/infra_customer/iam_utils.py`, `services/jobs/job_worker.py` (claim)
- Runtime: `template_repos/hermes_agent/humr_runtime/integrations/permissions_control.py`, `humr_client.py`, `control_api.py`, `humr_broker.py`
- WebUI: `template_repos/hermes_agent/webui-extension/humr-permissions.js`
- Docs: `docs/permissions_broker_design.md`, ABAC approval section in `docs/authorization_design_abac.md`

**Out of scope:** TLS credential MITM, SSO login, MCP tools, webapps, full ABAC engine (except approve checks on this path).

## Summary

The architecture correctly keeps the env bearer and AWS credentials out of the sandbox, scopes broker requests to an org/env, and gates **Apply** with ABAC `environment:approve` plus a HumR-sandbox platform-owner gate. Job claiming uses `select_for_update` / `skip_locked` and a unique-`applying` constraint.

The main residual risk is that **approval does not freeze the statement set**, the HumR HTML editor can mutate non-draft requests, and the broker client **lets browser/agent JSON override** `owner_username` / `app_slug`. Combined with empty-resource → `*` expansion, unrestricted “Permissions management,” and a loopback Apply surface reachable from the agent, a compromised or confused-deputy writer can escalate the task role far beyond what an approver reviewed.

## Findings

### [CRITICAL] Approved statements remain mutable until (and during) apply

- **Location:** `humanityrules_app/services/permissions_service.py` `approve` (220–239) only flips `status`; `update_statements` / `apply_statement_action` (109–177) save statements with no status gate; `humanityrules_app/views/security_permissions_editor.py` `security_permissions_editor_update_statement` (111–161) never checks `status == DRAFT`; `permissions_apply_executor.run_apply` (49–55) reads live `apr.statements` at apply time.
- **Issue:** After Apply, status becomes `approved_pending_apply` / `applying`, but any caller that can hit the HumR HTML mutation endpoints can change the statement JSON the worker will write to IAM. The JSON API correctly returns 409 for statement mutations on non-drafts (`permissions_api.py` 137–138); the session HTML path does not. There is no statement snapshot / hash at approve time.
- **Impact:** A non-approver who can open the HumR editor (any logged-in org member today) can race an approval and substitute `iam` + “Permissions management” + `*` (or other over-broad grants) for the reviewed draft. The approver’s ABAC check does not re-run on the mutated content.
- **Next:** Freeze statements on approve (immutable copy or content hash checked in the executor); reject all mutations unless `status == DRAFT` on every write path (HTML + API + cancel); optionally re-verify ABAC + hash in `run_apply`.

### [CRITICAL] HumrClient payload merge lets callers override deployment identity

- **Location:** `template_repos/hermes_agent/humr_runtime/integrations/humr_client.py` 37–41 — builds `body = {"owner_username": ..., "app_slug": ..., **payload}`; `permissions_control.py` statement/description routes forward the browser JSON body unchanged (54–62).
- **Issue:** Design and comments claim the target `(app, environment)` is never a client parameter and is fixed by broker identity. Because `**payload` is applied last, a browser or agent POST can replace `owner_username` and `app_slug`. HUMR then resolves that identity under the env bearer (`permissions_api._resolve_deployment` 40–59 + `broker_request_context.resolve_owned_app_slug`).
- **Impact:** Anyone who can reach `/__humr_broker/permissions/*` (Hermes WebUI session, or agent on loopback — see below) can draft/mutate/apply permissions for **another app in the same environment** owned by a chosen org user, subject only to that user’s ownership tag and that user’s `environment:approve`. Cross-app privilege escalation inside the customer account.
- **Next:** Always force broker identity last (`{**payload, "owner_username": self.owner_username, "app_slug": self.app_slug}`); strip client-supplied identity fields in `permissions_control` and/or reject mismatches on HUMR; add a regression test that overridden identity is ignored.

### [HIGH] Broker Apply authorizes the deployment owner, not the browser user

- **Location:** `humanityrules_app/views/permissions_api.py` `permissions_apply` (207–213) calls `abac_service.check_action(..., user=deployment.owner_user, ...)`; control API and WebUI never forward the SSO / policy-proxy identity (`permissions_control.py` `apply_route` 70–74; `humr-permissions.js` `doApply` 146–154).
- **Issue:** ABAC design separates submitters (`workspace:edit`) from approvers (`environment:approve`) (`docs/authorization_design_abac.md` ~375–386). On the Hermes path, Apply is evaluated as the **app owner** baked into the task env, not the human who clicked Apply. For Personal Assistants the WebUI is typically owner-only (policy proxy), so this collapses to owner self-approval. For any shared WebUI access (admins, future non-PA templates), a visitor Approves with the owner’s privileges.
- **Impact:** Confused-deputy Apply: WebUI access ⇒ ability to expand that deployment’s (or, with finding #2, another app’s) task role whenever the owner holds `environment:approve` (true for bootstrap `org-role=admin` on all envs).
- **Next:** Product decision — either treat Hermes self-approval as intentional and document it, or require a CP session / SSO subject on Apply and evaluate ABAC for that subject; never equate “can open WebUI” with “is the owner for IAM approval.”

### [HIGH] Agent (and any sandbox process) can Apply via unauthenticated loopback control API

- **Location:** `humr_broker.py` binds control API to `127.0.0.1:9951` (203–208); `supervisor.sh` documents sandbox reachability of 9950/9951 via `NO_PROXY` (144–146, 203); `permissions_control.apply_route` has no caller auth; design (`docs/permissions_broker_design.md` 207–212) says phase-2 Apply/cancel stay human-only at the **tool** layer only.
- **Issue:** There is no broker-side distinction between WebUI and agent. A prompt-injected or malicious agent can `curl` `http://127.0.0.1:9951/permissions/draft/<id>/apply` today; HumrClient attaches the env bearer and owner identity.
- **Impact:** Sandbox code execution → IAM task-role expansion in the customer account (gated only by the owner’s ABAC + sandbox platform gate), without a human click.
- **Next:** Deny Apply/cancel from non-browser callers (shared secret / hop header from Caddy only, or separate human-gated CP approval); do not expose Apply on the agent-reachable control port; enforce “human-only” in HUMR (e.g. require a short-lived user-bound approval token).

### [HIGH] Empty resource list expands to Resource `["*"]`; “Permissions management” and full IAM catalog are unrestricted

- **Location:** `iam_utils._build_iam_policy_document` 307–314 (`resources if resources else ["*"]`); `ACCESS_LEVELS` includes `"Permissions management"` (`permissions_service.py` 19; `iam_utils.py` 10); `get_all_service_options` exposes the full policy_sentry IAM catalog (428–441); Hermes UI labels empty resources as “All resources (*) — no resource restriction” (`humr-permissions.js` 221–223).
- **Issue:** Adding a service + access level and applying without selecting resources grants all resources for every expanded action. “Permissions management” expands via policy_sentry to IAM permission-management actions for that service (including dangerous `iam` / similar prefixes when selected). No denylist, no require-non-empty resources, no ARN account-id binding.
- **Impact:** One approved (or raced) draft can grant account-wide Read/Write/Permissions-management on sensitive services — classic privilege escalation inside the customer AWS account.
- **Next:** Refuse apply when any statement has empty resources or `*`; require resource ARNs matching the environment’s account (and region where applicable); block or dual-control “Permissions management” and high-risk service prefixes (`iam`, `sts`, `organizations`, `account`, …); surface an explicit break-glass UX if wildcards remain product-necessary.

### [HIGH] HumR editor draft mutations lack `workspace:edit` (any org member can write)

- **Location:** `security_permissions_editor.py` — all mutation/view endpoints are `@login_required` only (20–228); ABAC `environment:approve` only on `security_permissions_editor_apply` (73–75). Confirmed by `test_permissions_editor_renders_for_multiple_users` (non-approver GET 200) in `test_abac_views_security.py` 459–476.
- **Issue:** ABAC design says submitters need `workspace:edit` / `workspace:admin` (`authorization_design_abac.md` 379). Implementation allows any member of the org to open any app’s editor (`context_app` slug) and mutate the shared draft.
- **Impact:** Draft poisoning and (with finding #1) post-approve substitution by low-privilege org users; weakens separation of requestor vs approver.
- **Next:** Enforce `workspace:edit` (or equivalent) on GET editor + all draft-mutating endpoints; keep `environment:approve` only on Apply.

### [HIGH] Same-site CSRF on broker permission POSTs within the env domain

- **Location:** Policy-proxy session cookie is `Domain=.<env-domain>; SameSite=Lax` (`docs/policy_proxy_design.md` ~71); `/__humr_broker/permissions/*/apply` etc. are state-changing POSTs with no CSRF token or Origin check (`permissions_control.py`, `humr-permissions.js` `postSimple` 78–85); HUMR API is `@csrf_exempt` bearer auth (`permissions_api.py` 93+).
- **Issue:** Lax cookies are sent on same-site cross-origin requests. Sibling apps under the same env parent domain share the SSO cookie. A malicious or XSS’d page on another app host can POST Apply/mutate through the victim’s Hermes origin (or directly if CORS is not required for “fire and forget” navigation/form tricks where cookies attach).
- **Impact:** Cross-app CSRF → IAM Apply as the Hermes deployment owner without an intentional click on the permissions panel.
- **Next:** Require `Origin`/`Referer` allowlist on control_api permission writes; double-submit CSRF for browser routes; prefer `SameSite=Strict` for app cookies if product allows; keep Apply on HumR CP with session CSRF when possible.

### [MEDIUM] Cancel and description ignore draft status (API + HTML)

- **Location:** `permissions_api.permissions_cancel` / `permissions_description` (157–190) — no draft check; `security_permissions_editor_cancel` / `_update_description` (87–206) — same; only statement mutate on the JSON API checks draft.
- **Issue:** Cancel resets statements to baseline while leaving `approved_pending_apply` / `applying` unchanged. Description can be rewritten after approval (audit confusion). Combined with the executor reading live statements, cancel-during-apply can change what lands in AWS.
- **Impact:** Integrity / TOCTOU on the approved artifact; weaker audit trail; possible unexpected baseline apply.
- **Next:** Gate cancel/description/refresh like statement mutate; after approve, treat the request as read-only.

### [MEDIUM] Arbitrary resource ARNs accepted (free-text and API)

- **Location:** HTML resource search allows Enter when value starts with `arn:` (`_permission_service_group.html` ~64); `update_statements` `add_resource` appends any `arn` string (142–145); S3 prefix concatenation (`apply_statement_action` 165–166) does not validate prefix shape; API accepts `arn: "*"` (covered in tests).
- **Issue:** No check that the ARN belongs to the environment’s AWS account, matches the statement’s service, or is in the resource cache. Clients can add cross-account ARNs, `*`, or nonsensical strings that still become IAM `Resource` entries.
- **Impact:** Over-broad or surprising grants; bypass of the curated picker as a soft control.
- **Next:** Validate ARN parse + account id (+ service prefix); optionally restrict to cache/allowlisted ARNs unless break-glass.

### [MEDIUM] No uniqueness / locking on DRAFT creation

- **Location:** `get_or_create_draft` (89–106) — filter DRAFT then create, no `select_for_update` / unique partial index on `(app) WHERE status=draft`. Unique constraint exists only for `applying` (`models.py` 1181–1187).
- **Issue:** Concurrent opens can create multiple drafts; Apply targets one `request_id` while editors may diverge. Documented etag/concurrency control is deferred (`permissions_broker_design.md` 166–169, 222–223).
- **Impact:** Confused edits, lost updates, harder review of “the” draft; less likely direct escalation than #1 but weakens the approval story.
- **Next:** Partial unique constraint on one DRAFT per app (or per app+env); atomic get-or-create; add If-Unmodified-Since as planned.

### [LOW] Resource listing and refresh use customer AWS creds with only bearer/login gates

- **Location:** `get_resources_for_services` / `refresh_resources_cache` → `iam_utils.list_resources_for_services`; exposed on broker (`permissions_resources`, `permissions_refresh_resources`) and HumR editor refresh.
- **Issue:** Anyone who can open the draft path can trigger ListBuckets / ListTables / etc. in the customer account (and refresh clears cache). Not IAM privilege escalation of the task role, but account reconnaissance and assume-role load.
- **Impact:** Info disclosure of resource inventory; minor DoS on AWS APIs / CP workers.
- **Next:** Rate-limit refresh; require same submit ABAC as draft edit; narrow lister IAM on the HumR install role if possible.

## Sound design notes

1. **Bearer stays in the broker.** Env bearer and AWS assume-role credentials never enter the sandbox or browser; control_api is documented as pure transport (`permissions_control.py`, `humr_client.py`).
2. **Org-scoped target resolution.** Broker API resolves app via env bearer + ownership `ResourceTag`, not a naked client id (`broker_request_context.py` 60–79; `permissions_api._get_scoped_request` filters by `app=deployment.app`).
3. **ABAC on Apply (both UIs).** HumR HTML and JSON Apply call `environment:approve`; tests cover deny and sandbox platform-owner gate (`test_permissions_api.py`, `test_abac_views_security.py`).
4. **Sandbox shared-account gate.** `is_sandbox_approval_gated` blocks customer orgs from approving IAM changes that would land in HumR’s own sandbox account (`platform_owner.py` 28–35).
5. **Approve / claim concurrency hygiene.** `approve` uses `select_for_update` and refuses non-draft re-queue; worker claim uses `skip_locked`, excludes concurrent `applying` for the same app, and a DB unique constraint backs that invariant (`permissions_service.py` 220–239; `job_worker.py` 241–274; `models.py` 1181–1187).
6. **Tenant consistency fail-closed in the executor.** `assert_apr_consistent` refuses cross-org incoherent rows before `put_role_policy` (`tenant_consistency.py`, `permissions_apply_executor.py` 36–42).
7. **JSON statement mutate is draft-only.** `permissions_statement` returns 409 when status ≠ draft — the right pattern, not yet applied everywhere.
8. **Inline policy isolation.** Writes only the named `humr-app-permissions` inline policy on the app task role, not CDK/infra policies (`iam_utils.write_app_permissions_policy` 323–350).
9. **HumR session UI CSRF.** Django `@login_required` + global HTMX CSRF headers cover the CP HTML editor POSTs (unlike the broker browser path).

## Open questions / needs product judgment

1. **Is owner self-approval from the Hermes WebUI intentional for Personal Assistants?** If yes, document it as an exception to the submitter/approver split and still fix identity override + statement freeze. If no, Apply must bind to a distinct human approver (likely HumR CP only).
2. **Should wildcards / empty→`*` remain first-class?** The Hermes UI advertises “All resources (*).” That is convenient and dangerous; decide whether break-glass dual control is required.
3. **Which service prefixes and access levels are forever forbidden** on customer task roles (especially `iam` + “Permissions management”)?
4. **Phase-2 agent tools:** design says Apply stays human-only — should HUMR reject Apply unless a user-bound proof is present, regardless of broker path?
5. **Shared non-PA apps behind policy proxy:** when WebUI access is broader than “owner,” is the current broker Apply model acceptable at all?
