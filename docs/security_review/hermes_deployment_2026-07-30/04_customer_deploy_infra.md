# Area 4: Customer Env Provisioning & App Deploy Infra

## Scope

HumR-owned code and architecture for:

1. Cross-account AWS install (CloudFormation quick-create, install-callback Lambda, `/api/aws/install-account-callback`).
2. Environment base provisioning (VPC, ECS/EC2 capacity, shared ALB, wildcard DNS/TLS, EFS, builder).
3. Hermes AppTemplate → CDK app stack (policy proxy + hermes, env-bearer overlay, platform capabilities such as Bedrock).
4. Image build/push (content-addressed template images on the shared EC2 builder).
5. App / environment teardown and removal cleanup.
6. Multi-tenant consistency (org-scoped workers, sandbox slug/secret namespacing).

**In paths:** `humanityrules_app/services/infra_customer/`, job executors under `services/jobs/`, `seed_app_templates.py` (`HERMES_PERSONAL_TEMPLATE`), template deploy views/service, `infra_humanityrules/cf_install_template.json` + `install_callback_lambda.py`, models `AWSAccount` / `Environment` / `App` / `AppTemplate` / `EnvironmentBearerToken` / `SandboxSlugClaim`, and related docs.

**Out of scope:** TLS credential-injection internals, ABAC policy DSL, MCP tools, sandbox nono profile details (except deploy-time task-role / IMDS / network assumptions).

## Summary

Cross-account install uses ExternalId correctly in the AssumeRole trust policy, and the install Lambda allow-lists callback destinations so a tampered `ApiEndpoint` cannot exfiltrate `HUMR_API_SECRET_KEY`. Deploy-time isolation is intentionally layered (per-app task roles and secrets, EFS access points, dashless hostname labels, sandbox secret namespaces, worker tenant-consistency guards).

The dominant residual risk is **blast radius**: the customer install role is `AdministratorAccess`, so any HumR control-plane credential that can `sts:AssumeRole` with a known ExternalId is a full customer-account compromise. Secondary risks concentrate on (1) install-callback binding integrity, (2) shared-sandbox lateral movement (VPC SG + shared EC2 hosts + Hermes `SYS_ADMIN`), (3) teardown leaving Secrets Manager material behind, and (4) documented control-plane credential cleanup gaps on app removal.

## Findings

### [CRITICAL] Install role grants AdministratorAccess in the customer account

- **Location:** `infra_humanityrules/cf_install_template.json:59`
- **Issue:** The role customers create for HumR attaches the AWS managed policy `AdministratorAccess`. HumR then assumes `arn:aws:iam::{account}:role/humr-{external_id}` for every provision/deploy/teardown/permissions/EFS-browse path (`iam_utils.get_assumed_role_session`, `*:31–68`).
- **Impact:** Compromise of HumR CP task credentials (or any HumR principal allowed to call `sts:AssumeRole` with the ExternalId) yields unrestricted control of the customer account—not merely ECS/ECR/SM. Disconnect in the UI is record-only and leaves this role standing until the customer deletes the stack (`views/integrations/org_aws.py:190–191`, `:224–238`).
- **Next:** Replace AdminAccess with a least-privilege managed/customer policy covering only the APIs HumR actually uses (CFN/CDK bootstrap, ECS, ECR, EC2/ASG, EFS, SM, IAM for task roles, Route53/ACM, ELB, Logs, Bedrock logging helpers). Document residual actions. Add an explicit “revoke in AWS” checklist when disconnecting.

### [HIGH] Install Create callback rebinds any matching ExternalId without status or account checks

- **Location:** `humanityrules_app/views/integrations/org_aws.py:247–316`; model `AWSAccount.external_id` at `models.py:239–242` (no `unique=True`)
- **Issue:** On `request_type == "Create"`, the handler loads `AWSAccount` solely by `external_id` and unconditionally writes `aws_account_id`, `role_arn`, and `status=CONNECTED`. It does not require `status == PENDING`, does not verify `role_arn` matches `arn:aws:iam::{aws_account}:role/humr-{external_id}`, and does not reject a change of `aws_account_id` on an already-connected row. ExternalId is embedded in the browser quick-create URL (`models.py:300–314`).
- **Impact:** Anyone who obtains a victim org’s ExternalId (leaked connect URL, support paste, XSS, logs) can deploy the install template in *their* AWS account and rebind the victim’s HumR `AWSAccount` row to the attacker account. Subsequent HumR deploys, secrets, and env bearers land in the attacker’s account. Rebinding an already-connected customer silently redirects future ops.
- **Next:** Require `status == PENDING` (or an explicit reconnect flow). Enforce `role_arn` / account-id consistency with the ExternalId naming scheme. Add DB `unique=True` on `external_id` (sandbox rows share one ExternalId today via settings—carve those out or stop using `.get(external_id=)` for install). Prefer one-time connect nonces over long-lived ExternalId-in-URL as the only binder.

### [HIGH] Shared-sandbox apps share a flat VPC trust domain and often co-reside on EC2

- **Location:** `deploy_base.py:198–207` (default SG: all traffic from VPC CIDR, all outbound); `deploy_app.py:657–660` (tasks use that SG); `sandbox_service.py:16–24` (one shared cluster/base infra); Hermes template `seed_app_templates.py:230–237` (`SYS_ADMIN` + host bind mount under `/var/lib/humr/hermes-roots/{app_slug}`)
- **Issue:** Every sandbox org’s agents run in one account, one VPC, one ECS cluster, and pack onto shared EC2 nodes. The default task SG allows unrestricted east-west traffic inside the VPC. Hermes is granted `SYS_ADMIN` and a host path mount for the persistent root; `privileged` is blocked on Fargate but allowed on EC2 templates (`deploy_app.py:293–298`).
- **Impact:** A compromised agent (or escaped container) can scan/reach other orgs’ task ENIs, and with `SYS_ADMIN` + host mounts has a plausible path to the shared node’s other hermes-roots and to instance-level material. Org isolation in sandbox is primarily naming/slug/secret-namespace, not network or host isolation.
- **Next:** Product decision: treat sandbox as “trusted shared tenancy” (document clearly) or harden (per-app SGs with ALB-only ingress, drop `SYS_ADMIN` where possible, dedicate nodes / Fargate for multi-tenant, IMDSv2 hop limit / task-role-only story). At minimum, document the trust boundary for customers.

### [HIGH] Customer environment teardown does not delete Secrets Manager material

- **Location:** `environment_teardown_executor.py:97–105`; `deploy_base.teardown` (`deploy_base.py:802–853`) deletes only CFN stacks by `humr-{env_slug}-` prefix; secrets are created outside CDK in `secrets_utils.py`
- **Issue:** App secrets (`humr/{env}/{app}/secrets`), env shared-secrets (`humr/{env}/shared-secrets` or sandbox `humr/sandbox/{org}/shared-secrets`), and similar boto3-created entries are not deleted when a dedicated customer environment is torn down. Sandbox env teardown purges per-app namespaces (`purge_app_namespace_data`) but still does not remove the org’s shared-secrets bag. EFS uses `RemovalPolicy.RETAIN` (`deploy_base.py:673`).
- **Impact:** After teardown, raw `HUMR_ENV_BEARER` values and app secrets remain in the customer (or sandbox) account. DB `EnvironmentBearerToken` rows cascade away, so CP auth hashes disappear while live secrets persist—orphaned credentials and GDPR/retention exposure. Retained EFS may keep agent memory across “destroyed” envs if filesystem IDs are reused operationally.
- **Next:** On customer env teardown, `delete_secrets_matching_prefix` for `humr/{env_slug}/` (and sandbox org namespace on sandbox record delete). Decide EFS destroy-vs-retain explicitly; if retain, document recovery/orphan procedure.

### [HIGH] App removal leaves IntegrationUserCredential rows (known gap)

- **Location:** Documented in `docs/app_removal_data_cleanup_audit.md`; executor `app_remove_executor.py:121–176` deletes policies (optional), releases sandbox slug, deletes `App`—no `IntegrationUserCredential` query
- **Issue:** Integration credentials are keyed by `(owner_user, environment, app_slug, …)` with slug as a plain field, not an FK. Removal never deletes them.
- **Impact:** OAuth refresh tokens / bot tokens survive “remove app.” Recreating the same slug under the same env can resurrect integrations (“phantom reconnect”). GDPR-shaped retention failure.
- **Next:** Unconditional delete of matching `IntegrationUserCredential` rows in `run_removal` (as recommended in the audit); add regression test.

### [MEDIUM] Shared task execution role can read all `humr/*` secrets in the account/region

- **Location:** `deploy_base.py:336–346`
- **Issue:** The env-wide ECS task execution role is granted `secretsmanager:GetSecretValue` on `arn:…:secret:humr/*`. Per-app *task* roles correctly narrow to their own secret ARN (and env shared-secrets when needed) (`deploy_app.py:222–240`).
- **Impact:** Defense-in-depth gap: any principal that can `iam:PassRole` this execution role and register a task definition can inject arbitrary `humr/*` secrets into a container at start. With AdminAccess install this is already reachable; after least-privilege hardening it becomes a sharper edge.
- **Next:** Scope execution-role SM reads to `humr/{env_slug}/*` (and sandbox org namespaces), or use per-app execution roles.

### [MEDIUM] Env bearer is env-scoped and injected into every bearer-needing container

- **Location:** `secrets_utils.ensure_env_bearer_token_exists` (`secrets_utils.py:229–294`); overlay in `deploy_app.py:400–422`; Hermes `requires_env_bearer: True` in `seed_app_templates.py:254`
- **Issue:** One raw token per environment (per-org namespaced in sandbox—good) authenticates *any* env-resident caller to CP APIs that accept the env bearer (PDP, integrations broker, etc.). All apps in a dedicated customer env that opt into the overlay share that token.
- **Impact:** Compromise of one app’s task (or its injected env) yields the bearer for the whole env’s CP surface, not just that app. Sandbox mitigates cross-org collision via `humr/sandbox/{org-slug}/shared-secrets` (`secrets_utils.py:37–52`).
- **Next:** Consider per-app bearers (or audience-bound tokens) for high-risk CP routes; keep env-wide only where truly shared. Confirm every bearer-authenticated CP endpoint enforces app/org scope from request context, not “valid bearer ⇒ trusted.”

### [MEDIUM] ECS Exec is always enabled on customer app services

- **Location:** `deploy_app.py:660` (`enable_execute_command=True`); ops commands `humr_app_shell` / `humr_app_exec` / `humr_efs_browse`
- **Issue:** Every deployed app service enables ECS Exec. Combined with AdminAccess (or future `ecs:ExecuteCommand` + SSM), HumR operators can obtain interactive shells in customer workloads.
- **Impact:** Expected for platform ops pre-beta; in enterprise terms it is a standing break-glass channel into customer memory, tokens in-process, and host mounts. Audit coverage depends on CloudTrail/SSM session logs outside this code.
- **Next:** Gate Exec behind org/env flag or break-glass role; ensure session logging; document customer-facing disclosure.

### [MEDIUM] Bedrock platform capability grants broad `Resource: *`

- **Location:** `deploy_app.py:34–54`, `:241–245`; gated by org `platform_capabilities` via `app_config_builder.effective_platform_capabilities`
- **Issue:** When `bedrock-runtime` is granted, the app task role receives many Bedrock invoke/list/get actions on `*`.
- **Impact:** A compromised agent in a Bedrock-enabled org can invoke any Bedrock model/resource the account allows in-region (cost, data egress to model providers, guardrail bypass if not separately enforced).
- **Next:** Narrow resources to approved inference profile ARNs / foundation-model IDs when product has a fixed allow-list; keep capability gate (already a strength).

### [MEDIUM] ALB listener rule priority uses salted `hash()`

- **Location:** `deploy_app.py:141–144`, used at `:715`
- **Issue:** `_compute_listener_rule_priority` uses Python’s `hash(app_name)`, which is randomized per process unless `PYTHONHASHSEED` is fixed. Priorities are therefore non-deterministic across workers and can collide within the 1000–41000 band.
- **Impact:** Redeploys may churn listener rule priorities; two apps can collide and fail CFN updates (availability / stuck routes). Not a direct auth bypass given dashless slugs (see strengths), but it weakens routing stability on the shared ALB—especially sandbox.
- **Next:** Replace with a stable digest (e.g. truncated SHA-256 of subdomain) and collision detection at deploy time.

### [MEDIUM] Public install template integrity and confused-deputy surface

- **Location:** Template URL `https://humr-public.s3.us-east-1.amazonaws.com/cf_install_template.json` (`models.py:309`); trust principal `arn:aws:iam::555553041615:root` + ExternalId (`cf_install_template.json:45–55`); Lambda `ServiceToken` hardcoded (`cf_install_template.json:66`)
- **Issue:** Customers install whatever object is currently at that public S3 URL. Trust is to the HumR account root (any HumR principal that can AssumeRole with ExternalId), which is normal but wide. ApiEndpoint allow-listing in the Lambda (`install_callback_lambda.py:20–37`) correctly blocks secret exfiltration via a tampered callback URL—good.
- **Impact:** Compromise of the public bucket/object (or a malicious template update) could change the role policy or callback behavior for new installs. Account-root trust amplifies any HumR-side STS credential leak when ExternalIds are known.
- **Next:** Pin template by versioned object + checksum shown in UI; prefer a dedicated HumR deploy role ARN in the trust policy instead of `:root`; keep ApiEndpoint allow-list.

### [MEDIUM] Ephemeral maintenance roles grant EFS ClientRootAccess on the whole filesystem

- **Location:** `ephemeral_task_role.efs_access_policy` (`ephemeral_task_role.py:78–90`); used by app-removal EFS purge and `humr_efs_browse`
- **Issue:** Short-lived roles intentionally allow `elasticfilesystem:ClientRootAccess` on the env filesystem ARN (no access-point condition), unlike app task roles which condition on access-point ARNs (`deploy_app.py:275–283`).
- **Impact:** Correct for cleanup/browse, but a leaked ephemeral role (or race before `finally` delete) can read/write any app’s EFS paths. Orphans are tagged but not automatically reaped.
- **Next:** Janitor for `humr:purpose`-tagged orphan roles; consider access-point-scoped cleanup where path is known.

### [LOW] Install callback auth uses non-constant-time string compare; empty secret failure mode

- **Location:** `org_aws.py:251–254` (`auth_header != expected_token`)
- **Issue:** Bearer comparison is not `hmac.compare_digest`. If `HUMR_API_SECRET_KEY` were ever empty/misconfigured, behavior depends on deployment wiring (`lambda_stack.py` injects from env).
- **Impact:** Practical exploitability of timing is low on TLS; misconfiguration risk is higher than timing.
- **Next:** Use `hmac.compare_digest`; refuse to start/callback when secret is missing/empty.

### [LOW] Permissions editor can list broad account resources via AdminAccess session

- **Location:** `iam_utils.list_resources_for_services` (`iam_utils.py:100–141`) lists S3/SQS/DDB/SM/SNS/KMS across the account
- **Issue:** UI resource pickers enumerate customer-account resources using the same assumed AdminAccess role.
- **Impact:** Any HumR user authorized to open the permissions UI for an env learns account-wide resource inventory (secrets names, bucket names, etc.).
- **Next:** After least-privilege install role, scope list APIs; consider ABAC on who may list.

## Sound design notes

1. **ExternalId in AssumeRole trust** — Install template requires `sts:ExternalId` matching the parameter (`cf_install_template.json:49–54`); assume path always passes it (`iam_utils.py:63–67`). Classic confused-deputy mitigation for the role trust itself.
2. **Install Lambda ApiEndpoint allow-list** — Prevents redirecting `HUMR_API_SECRET_KEY` to attacker hosts (`install_callback_lambda.py:20–37`, `lambda_stack.py` default allow-list).
3. **Bearer storage** — Raw env bearer only in customer SM; CP stores SHA-256 only (`EnvironmentBearerToken`, `secrets_utils.py:229–238`). Secret wins over DB on drift (shared-account / multi-CP safety).
4. **Sandbox secret namespacing** — `humr/sandbox/{org-slug}/shared-secrets` avoids cross-org bearer collision on the shared account (`secrets_utils.py:37–52`; tests in `test_env_bearer_token_secrets.py`).
5. **SandboxSlugClaim** — Global unique slug reservation prevents cross-org resource-name races on `humr-sandbox-{slug}-*` (`models.py:902–912`, `sandbox_service.py:73–100`).
6. **Per-app task role + secret ARN scoping** — Apps do not get blanket SM read; EFS mounts use access points + IAM condition on AP ARNs (`deploy_app.py:222–283`).
7. **Dashless app hostname labels** — `app_slugs` allow only `[a-z0-9]+`, so agent roots cannot collide with `*-{agent}` webapp host patterns on the shared ALB (`app_slugs.py:18–21`; routing in `deploy_app.py:722–741`). This closes a class of host-header shadowing bugs.
8. **Content-addressed images** — Tree-hash tags, build-on-miss, ECR scan-on-push, advisory lock against thundering herds (`template_images.py`); build dirs isolated by `build_id` on the shared builder.
9. **Worker tenant-consistency guards** — `tenant_consistency.assert_app_owns_environment` before deploy/remove (`app_deployment_executor.py:54–58`, `app_remove_executor.py:135–139`).
10. **Template deploy ABAC** — Environment/workspace filtered by `environment:deploy` / `workspace:edit` before deploy (`views/template_deploy.py:179–187`).
11. **Platform capability gating** — Bedrock IAM and `HUMR_PLATFORM_CAPABILITIES` come from one org entitlement function (`app_config_builder.effective_platform_capabilities`).
12. **TLS on shared ALB** — Wildcard cert + HTTPS listener default 404; HTTP redirects to HTTPS when zone configured (`deploy_base.py:384–453`). Policy-proxy apps require hosted zone (`deploy_app.py:852–856`).
13. **Ephemeral maintenance roles** — Unique per invocation, deleted in `finally`, tagged for orphan identification (`ephemeral_task_role.py` module docstring).

## Open questions / needs product judgment

1. **Is shared-sandbox cross-org isolation a hard security requirement, or an accepted soft tenancy?** Networking + `SYS_ADMIN` + shared nodes currently imply the latter.
2. **Timeline to retire AdministratorAccess** — Blocking for any paying customer who cannot accept a HumR-owned admin role in their account.
3. **Env-wide vs per-app control-plane bearers** — Especially as more CP APIs grow behind the env bearer.
4. **ECS Exec default-on** — Disclose, gate, or restrict to HumR break-glass principals only?
5. **Environment teardown data retention** — Should SM secrets and EFS always be destroyed with the env, or is retain-by-default intentional for recovery?
6. **Reconnect / account-move story** — Today Create overwrite is the only path; a deliberate reconnect UX would remove the need for silent rebind.
7. **Install template distribution** — Version pinning and customer-visible checksum before GA.
