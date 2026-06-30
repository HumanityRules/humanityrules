# Org-scoped resource naming — implementation plan & audit

**Companion to [`docs/resource_naming_convention.md`](resource_naming_convention.md)** (the design/rationale). Read that first for the *why*. This document is the *what and where*: the locked naming scheme, the complete rename-site inventory (the design doc's site list was an explicit "starting point, not exhaustive" — this is the exhaustive version), the corrections to the design doc that the audit found, the silent-failure couplings, and the implementation order.

This was produced by a six-way parallel audit of the codebase. It is written to be picked up cold by a fresh session.

---

## 0. One-paragraph orientation

Every AWS resource that HumR creates in a customer/sandbox account must carry the slug of the **org that owns it**, so one account can host many orgs without name collisions and with per-tenant attribution. Today resources are named `humr-<env>-<app>-*` (dash names) and `humr/<env>/<app>/...` (slash names) — these collide the moment two orgs share an account (the shared sandbox). The fix: `humr-<org>-<env>-<app>-*` / `humr/<org>/<env>/<app>/...`, with **shared base infra owned by the platform org** (`settings.HUMR_PLATFORM_OWNER_ORG_SLUG`, default `"humanity-rules"`), a handful of **length-bound names** using a short hash, **four tags on every resource**, the **`SandboxSlugClaim` reservation table retired**, and the **shared-secrets namespace de-special-cased**.

Naming gives collision-freedom and attribution. It is **not** tenant isolation (network/host/IAM isolation is explicitly out of scope — see the design doc's last section).

---

## 1. The canonical naming scheme (locked)

### 1.1 Readable names (the majority — no length pressure)

- **Dash names** (CFN stacks, ECS service/task-def family, IAM roles, certs, CfnOutput export names):
  `humr-<org>-<env>-<app>-<suffix>`
- **Slash names** (Secrets Manager, ECR repos):
  `humr/<org>/<env>/<app>/<suffix>`
- **EFS access-point path:** `/deployments/<org>/<app>/<subpath>` (env is implicit — the filesystem *is* that env's).
- **EC2 host bind-mount:** `/var/lib/humr/hermes-roots/<org>/<app>` (env implicit).
- **DNS:** dedicated account `<app>.<customer-domain>` (unchanged); shared sandbox `<app>.<org>.<sandbox-zone>`.

### 1.2 Length-bound names use a readable prefix + short hash (DECISION)

A few AWS names have limits too tight to hold `org+env+app` readably. **Decision (locked this session): keep a readable portion — the app slug — alongside the hash, sized per-limit, so names stay identifiable in the AWS console.** Full attribution always lives in the tags (§1.3), so the hash only needs to be collision-stable, not human-readable.

`rid` is a stable hash of the immutable identity tuple:

```python
RID_LEN = 12  # 48-bit; collision probability ~1e-4 even at ~300k (org,env,app) tuples.
              # Do NOT shrink to buy readable chars: 40-bit is ~4% at that scale.

def app_rid(org_slug: str, env_slug: str, app_slug: str) -> str:
    return hashlib.sha256(f"{org_slug}/{env_slug}/{app_slug}".encode()).hexdigest()[:RID_LEN]

def bounded_app_name(app_slug: str, rid: str, *, limit: int, suffix: str = "") -> str:
    """`humr-<app>-<rid><suffix>`, with <app> trimmed to fit `limit`. App-first for
    at-a-glance identification and console grouping; rstrip avoids a trailing/double dash."""
    head, tail = "humr-", f"-{rid}{suffix}"
    app_part = app_slug[: max(0, limit - len(head) - len(tail))].rstrip("-")
    return f"{head}{app_part}{tail}"
```

Resulting names (example: org=`course-hero`, env=`sandbox`, app=`course-hero-slack`, rid=`9f3a2b7c1d4e`):

- **IAM task role** (limit 64): `bounded_app_name(app, rid, limit=64, suffix="-task-role")`
  → `humr-course-hero-slack-9f3a2b7c1d4e-task-role` — **full app slug fits.**
- **Aurora cluster identifier** (limit 63): `bounded_app_name(app, rid, limit=63, suffix="-aurora")`
  → `humr-course-hero-slack-9f3a2b7c1d4e-aurora` — **full app slug fits.**
- **Target group** (limit 32): `bounded_app_name(app, rid, limit=32)`
  → `humr-course-hero-sl-9f3a2b7c1d4e` — app trimmed to ~14 chars (limit is tight; unavoidable).
- **Shared ALB name** (limit 32, base infra — see §2 correction #1): keyed by `(platform_org, env)`, not app, so the readable token is the **env** (short, few of them). Recommended `humr-<env>-<rid_base>` where `rid_base = app_rid(platform_org_slug, env_slug, "")` or equivalent. Final form is an open decision (§11).

**Org and env drop out of the readable portion of length-bound names** (no room at 32) but remain in the `rid` input and in the `Org`/`Env` tags.

**Sanitization:** app slug is a Django `SlugField` (lowercase alphanumeric + hyphen), so the truncated prefix is already charset-valid; `.rstrip("-")` prevents a trailing/double hyphen (matters for Aurora, which forbids consecutive/trailing hyphens, and target groups, which forbid trailing hyphens). All four names begin with `humr` (a letter), satisfying Aurora's "must start with a letter" rule.

### 1.3 Tags (required on EVERY resource)

`Org=<org_slug>`, `Env=<env_slug>`, `App=<app_slug>`, `humr:rid=<rid>`.

These make the hashed names identifiable and enable per-tenant grouping/cost allocation independent of the name. **Today most resources carry only `App` (sometimes `Env`); many carry none at all** (see §5.1 — the entire `deploy_base.py` resource set is untagged except one builder instance). The resources whose names become opaque hashes (target group, task role, Aurora cluster + its SG/subnet group) are exactly the ones currently **untagged**, so tagging them is no longer optional — it is the only non-name way to identify them.

Tag-key convention: capitalized plain keys (`Org`, `Env`, `App`); lowercase namespaced key (`humr:rid`). Note the existing builder tag uses lowercase `humr:environment` — that should become `Env` (§5.1, and it is a coupled lookup — §4).

### 1.4 Ownership: tenant org vs platform org (the distinction that's easy to get wrong)

- **Per-app resources** are owned by the **tenant org** that owns the app → `org_slug = app.organization.slug`.
- **Shared base infra** (VPC, cluster, EFS filesystem, builder, shared ALB, the ECS log group `/humr/<env>/ecs`, the task-execution-role, the capacity provider, the ASG) is owned by the **platform org** → `settings.HUMR_PLATFORM_OWNER_ORG_SLUG`. This is a **fixed settings constant**, never a per-call lookup, and never `is_platform_owner_org(...)` (that helper answers a different yes/no question). In a dedicated single-tenant account the platform org *is* the tenant, so prefixes coincide.
- **Account-wide singletons** that are not per-org at all (the Bedrock invocation-logging log group/role; the per-account assume-role) stay as-is — see §10.

The trap: some platform-owned infra is named from inside `deploy_app.py` (the "app" file) — e.g. the policy-proxy ECR repo and the shared-ALB import prefix. Those must use `platform_org_slug`, even though the surrounding `deploy()` is threading the tenant `org_slug` for everything else. See §5.1.

---

## 2. Corrections to the original design doc

The design doc is "intended design, not a frozen spec" and invites push-back. The audit found three things to fix:

1. **There is a fourth length-bound name the doc's "only three" misses.** `deploy_base.py` `load_balancer_name=f"humr-{env_slug}-shared"[:32]` (the shared ALB) is already truncated to 32. Adding the platform-org prefix overflows it (e.g. `humr-` + `humanity-rules` + `-sandbox-shared` ≫ 32). It needs the bounded-name treatment (§1.2) or an explicit exception. Note base infra is per-`(account, env)`, and there is one platform org per account, so base-infra names don't strictly *need* the org segment for collision-freedom (env is already unique per account) — only for uniformity/attribution; the tags already attribute it. This makes "keep `humr-<env>-shared`, tag `Org=<platform-org>`" a legitimate pragmatic option for the ALB. Decide in §11.

2. **The policy-proxy ECR repo is platform-owned shared infra, not per-app.** `policy_proxy_ecr_repo_name()` lives in `deploy_app.py` and returns `humr/<env>/policy-proxy` — one repo per env, shared by every app/org in the env. It must become `humr/<platform-org>/<env>/policy-proxy`, using `platform_org_slug` not the tenant org. It also has **no natural `App` tag value** (open decision §11).

3. **`bedrock_logging_utils.py` must be explicitly excluded.** Its `/humr/bedrock-invocations` log group and `humr-bedrock-logging-role` are account+Region-level singletons (one level above platform-org infra) with no per-env/per-org variant possible. Leave a one-line "intentionally not renamed" comment so the next reader doesn't "fix" it.

---

## 3. Centralize the naming FIRST (load-bearing prerequisite)

Today resource names are scattered f-strings, and several are **independently re-derived in multiple files that must agree**. Build one naming module first, then change call sites to use it. At minimum it should expose:

- `app_resource_prefix(org, env, app) -> "humr-<org>-<env>-<app>"`
- `base_resource_prefix(platform_org, env) -> "humr-<platform_org>-<env>"`
- `app_rid(org, env, app)` and `bounded_app_name(...)` (§1.2)
- `task_role_name(org, env, app)`, `target_group_name(org, env, app)`, `aurora_cluster_id(org, env, app)`, `shared_alb_name(platform_org, env)`
- `app_secret_name(org, env, app)`, `shared_secrets_name(org, env)`, `ecr_repo_name(org, env, app, container)`, `policy_proxy_ecr_repo_name(platform_org, env)`
- `efs_deployment_path(org, app)`, `hermes_root_host_path(org, app)`
- the tag dict `resource_tags(org, env, app, rid)`

This matters most for the **task-role name**, which is reconstructed in ~6 places (§4.1). A single helper makes drift impossible; hand-rolled f-strings that truncate differently break silently.

The main plumbing work is **threading `org_slug` into the naming inputs** — `AppConfig` carries `app_name` but not the org today. See §9. Consider adding an `org_slug` field to `AppConfig` so it self-carries everything `deploy_app.py` needs, rather than threading a parallel parameter through every function (it's already passed to nearly every CDK stack constructor).

---

## 4. Silent-failure couplings (the dangerous part)

These are sets of sites that build the *same* name independently. A missed/divergent rename here does **not** raise — it quietly targets the wrong resource. Verify each set produces byte-identical strings (ideally by all calling the §3 helper).

### 4.1 Task-role name — reconstructed in ~6 sites; mismatches fail silently

- **Authoritative (creates the role):** `deploy_app.py:678` `role_name=f"{resource_prefix}-task-role"[:64]`; the IAM editor read/write path `iam_utils.py:208` and `iam_utils.py:330` (same construction).
- **Read-side reconstructions that must match:**
  - `services/cost/bedrock.py:166` — builds the role name to filter CloudWatch Logs for Bedrock cost attribution via a substring `like` match. **A mismatch returns zero rows, not an error** → cost silently goes to zero for every app.
  - `services/agent/tools/lookup_access_denied_events.py:143` — same substring-match shape for CloudTrail AccessDenied lookup → silently "no events found."
  - `services/agent/agent_build_prompt.py:158` — interpolates the role name into the permissions-mode system prompt (informational; wrong name misleads the agent/user, not silent data loss).
- Today all use naive `[:64]` truncation. Under the bounded-name scheme they must all call `task_role_name(org, env, app)`.

### 4.2 EFS deployment path — silent orphan-data risk

`deploy_app.py:728` creates the access point at `/deployments/<app>/<subpath>`; `services/jobs/app_remove_executor.py:415` does `rm -rf <mount>/deployments/<app>` on removal. The cleanup treats "directory absent" as success, so a path mismatch **leaves tenant data on EFS forever while reporting success.** Both must become `/deployments/<org>/<app>`.

### 4.3 Base-infra teardown by prefix — silent "deleted nothing"

`deploy_base.py:781` scans stacks by prefix `f"humr-{env_slug}-"` for teardown. If the stacks are renamed to the platform-org prefix but this scan isn't, it matches nothing and reports success having deleted nothing. Must become `f"humr-<platform_org>-{env}-"`.

### 4.4 Builder discovery by tag — silent "no builder found"

`deploy_base.py:597` tags the builder instance `humr:environment=<env>`; `services/infra_customer/ec2_builder_utils.py:21-30` discovers it with filter `tag:humr:environment`. If the tag key changes (to `Env`, per §1.3) but the filter isn't updated in lockstep, `get_builder_instance_id` returns `None` and **every remote Docker build breaks** (`RuntimeError: No builder instance found`). Change both together.

### 4.5 Host-mount path — three-way agreement (this one fails LOUD)

The hermes-root host path is a format-string template defined once and `.format()`-ed twice:
- `management/commands/seed_app_templates.py:287` defines `"/var/lib/humr/hermes-roots/{app_slug}"` (DB-seeded into the template) → add `{org_slug}`.
- `services/jobs/app_config_builder.py:143` `.format(app_slug=, env_slug=)` (deploy time) → add `org_slug=`.
- `services/jobs/app_remove_executor.py:520` (`_resolve_host_paths`) `.format(app_slug=, env_slug=)` (removal time) → add `org_slug=`.
If the template gains `{org_slug}` but a `.format()` call doesn't pass it, Python raises `KeyError` — a loud failure, not silent. Still must keep all three in sync. (Re-seed via `seed_app_templates` after the template change, or recreate the DB per rollout.)

### 4.6 ECR repo name — three-way agreement (fails loud via CFN)

`app_config_builder.py:172` (deploy-time creation) ↔ `app_deployment_teardown_executor.py:50,54` (teardown cleanup) ↔ `deploy_app.py` ECR stack. All → `humr/<org>/<env>/<app>-<container>`. A mismatch makes CFN fail to find/empty the repo (loud).

---

## 5. Rename-site inventory by area

Notation: `file:line` — current → intended — note. "**(missed)**" = not in the design doc's starting list. Line numbers are from the audit snapshot; verify as the code may have shifted.

### 5.1 `services/infra_customer/` (in the doc's list; here exhaustively)

**`deploy_app.py`** (the bulk):
- `:206` / `:249` `humr/{env}/{prebuilt_ecr_repo}` → `humr/<org>/<env>/...` (image existence check + pulled image URI; change together).
- `:69-71` `policy_proxy_ecr_repo_name` `humr/{env}/policy-proxy` → **platform-org** `humr/<platform-org>/<env>/policy-proxy`; call sites `:254`, `:315`, `:1490`; export `:334`. (Correction #2.)
- `:287-289` EcrStack tags (`App`, `Container`) → add `Org`/`Env`/`humr:rid` (constructor doesn't receive `env_slug` today — add it).
- `:294-295`, `:478-482`, `:1152-1154`, `:1230`, `:1283` CfnOutput `export_name`s built from `resource_prefix` → inherit the org segment (export names are account-global — must be unique).
- `:328-329` PolicyProxyEcrStack tags (`Env`, `Component`) → add `Org` (platform); no natural `App` (§11).
- Aurora `AuroraClusterStack`: `:440` `cluster_identifier[:63]` → **bounded name** `aurora_cluster_id(...)`; `:441` credentials secret + `:468` connection secret → `humr/<org>/<env>/<app>/aurora/...`; `:475-476` tags → all four; the security group (`:384`) and subnet group (`:396`) currently get **no tags** — add them.
- `:524-526` `cert_stack_name` `humr-{env}-{app}-cert` → `humr-<org>-<env>-<app>-cert`; call sites `:1332`, `:1436`, `:1506`, `:1651`.
- `:587-588` CertStack tags; `:593` export.
- `:678` `role_name=f"{resource_prefix}-task-role"[:64]` → **bounded** `task_role_name(...)` (see §4.1); the task role and target group are currently **untagged** — add all four tags.
- `:685` IAM policy `resources=[... secret:humr/{env}/{app}/* ]` → `humr/<org>/<env>/<app>/*` (ARN-in-policy; must match the secret path the role actually reads — §4 / `secrets_utils:90`).
- `:728` EFS access-point `path=/deployments/{app}/{subpath}` → `/deployments/<org>/<app>/<subpath>` (§4.2).
- `:759-761` `AppSecret` reference `humr/{env}/{app}/secrets` → org (must match `secrets_utils:90` and the `:685` ARN).
- `:792-816` `family=resource_prefix[:255]` → inherits prefix (255 is generous, no hash needed).
- `:1064` `target_group_name[:32]` → **bounded** `target_group_name(...)`.
- `:1149-1150` service/task-def tags (`App`) → all four.
- `:1181` `_setup_shared_alb_routing` `prefix=f"humr-{env}"` → **platform-org** (used for shared-ALB imports `:1183/:1244/:1247`); note `:1230/:1275/:1283` in the same method use the **app** prefix — both prefixes appear here; comment them distinctly.
- `:1324` `resource_prefix=f"humr-{env}-{app}"` → **the central app prefix**, add org (`deploy()` already receives `environment`, so `org = environment.aws_account.organization.slug` is derivable; an explicit `org_slug` param is cleaner).
- `:1336-1337` `vpc_stack_name`/`cluster_stack_name` → **platform-org** (duplicated at `:224` and `deploy_base.py:705-706` — §4-style agreement); `:1409/:1476` policy-proxy-ecr stack name → platform-org; `:1347-1352` operator error-message literal mentions the cluster name (cosmetic, keep accurate).
- `:1566` `get_app_urls(...)` → pass `org_slug` (see cloudformation_utils).
- `:221-230` `_environment_has_ec2_capacity_provider` `:224` cluster name → platform-org.
- `:1603` `teardown()` `resource_prefix` → org (signature is string-only `env_slug`+`app_name` — **add `org_slug` param**; caller has the objects); `:1651` `teardown_cert_stack` likewise.

**`deploy_base.py`** (entirely base infra → platform-org; module has no org awareness today — introduce `settings.HUMR_PLATFORM_OWNER_ORG_SLUG`):
- `:32-34` `ec2_capacity_provider_name`; call sites `:148`, `:319`, and `deploy_app.py:225,1348`.
- `:78` `import_environment_infrastructure` `prefix` drives ~a dozen `Fn.import_value` (lines 81-141) → platform-org; **add `platform_org_slug` param** (callers `deploy_app.py:360/669` know the app org but must pass the *platform* org here).
- VpcStack `:186` vpc-name, `:199` default-sg, `:208` prefix → 9 exports; **no tags anywhere** — add the four.
- EcsClusterStack `:242` prefix → `:244` cluster, `:256` instance role, `:266` instance sg, `:290` launch template, `:309` ASG, `:330` task-execution-role, `:343` log group `/humr/{env}/ecs`, `:356` shared-alb-sg, `:369` **ALB name `[:32]` ← 4th length-bound (§2 #1)**; `:443-454` eleven exports; **entirely untagged** — add the four to all.
- BuilderStack `:529` prefix → `:534` builder-role, `:566` builder-sg, `:575` builder instance; `:559` IAM `repository/humr/*` wildcard still matches post-rename (no change; note it grants cross-org ECR push — an isolation concern, out of scope); `:597` `Tags.of(instance).add("humr:environment", env)` → `Env` + add `Org` — **coupled to ec2_builder_utils (§4.4)**; `:600-601` exports.
- EfsStack `:623` prefix → `:628` efs-sg, `:640` filesystem name; `:649-650` exports; untagged — add the four.
- `:662` `get_or_create_vpc_cidr` vpc stack name (discovery) → platform-org.
- `:705-708` `deploy()` vpc/cluster/builder/efs stack names → platform-org; **thread `platform_org_slug`** (simplest: read settings inside the module).
- `:781` `teardown()` prefix scan → platform-org (§4.3).

**`iam_utils.py`:** `:59` `humr-{external_id}` assume-role → **NOT a site** (account-level, keyed by external_id; stays). `:207-208` / `:329-330` task-role name (§4.1) → bounded helper; both functions already receive `environment`+`app` objects, so `org = app.organization.slug` is free.

**`secrets_utils.py`:** `:37-47` `env_shared_secrets_namespace` → **DELETE the carve-out** (§7); `:50-52` `shared_secrets_secret_name(env)` → `humr/<org>/<env>/shared-secrets` (has `env` object); `:90` `ensure_app_secrets_exist` secret name → org (**add `org_slug` param** — string signature; one of the §4 app-secret trio); `:249` env-bearer secret (inherits); `:142/:281` descriptions (cosmetic); `delete_secrets_matching_prefix` is parameterized (callers update the prefix).

**`example_apps.py`:** `:45-47` `_build_ecr_repo_name` → org; `:19-36` `get_simple_dashboard_config(env_slug)` → **add `org_slug` param** (and `get_app_config`); `:46` docstring asserts the now-false "no collision" rationale — fix the comment.

**`cloudformation_utils.py`:** `:263` `get_app_urls` `stack_name=f"humr-{env}-{app}-app"` → org (**add `org_slug` param**); `list_stacks_by_prefix` is generic (callers pass the updated prefix).

**`ec2_builder_utils.py` (missed):** `:21-30` tag-lookup filter — coupled to `deploy_base.py:597` (§4.4).

**`appconfig.py`:** docstrings `:108` (`/deployments/<app>/`), `:28/:39/:164/:297` (`humr/{env}/...`) → update to org scheme; consider adding an `org_slug: str` field (§3).

**Zero-change (read in full, names supplied by callers):** `ecr_utils.py`, `acm_utils.py`, `route53_utils.py`, `vpc_utils.py`, `ecs_utils.py`, `cdk_utils.py`. **Exclude by design:** `bedrock_logging_utils.py` (§2 #3). **Low-priority:** `ecs_task_cleanup.py:322-330` operator-CLI defaults (`--prefix`/`--cluster` are overridable at runtime; update defaults for convenience only).

### 5.2 `services/jobs/`

**`app_remove_executor.py`** (doc-listed): `:189` secret prefix → org; `:281` efs-remover-role / `:282` task-family → org (HumR-internal scratch; could be platform — §11); `:283` cluster, `:288` task-execution-role, `:290` log group, `:349` vpc stack, `:350` efs stack, `:543` ASG → **platform-org**; `:415` `/deployments/{app}` cleanup → org (§4.2 silent risk); `:507-530` host-mount `.format()` → add `org_slug` (§4.5); `:204` `release_sandbox_app_slug(...)` → **DELETE** (§6). Add `"organization"` to the `App` `select_related`.

**`app_deployment_teardown_executor.py`** (doc-listed): `:50` / `:54` are **ECR repo names** (not secrets) → `humr/<org>/<env>/<app>...` (§4.6). Add `app__organization` to `select_related`.

**`app_config_builder.py` (missed):** `:143` host-mount `.format()` + `org_slug` (§4.5); `:172` ecr_repo_name + org. `org = blueprint.app.organization.slug` — thread an `org_slug` param into `_build_container_config`.

**`environment_provisioning_executor.py` (missed):** `:38`/`:39` vpc/cluster stack-name fallbacks → platform-org. Caveat: `environment.vpc_stack_name`/`cluster_stack_name` are **stored DB fields**; a row populated under the old scheme keeps the old name (the `or` only uses the f-string when empty). Fine if the env is recreated (rollout); otherwise needs a data migration. Also: `deploy_base.deploy(...)` call here must pass `platform_org_slug`.

**`app_deployment_executor.py` (missed):** `:151` `subdomain = deployment.subdomain or app.slug` → shared-sandbox default becomes `<app>.<org>` (conditional on `aws_account.is_humr_sandbox`; dedicated accounts unchanged — §11). The real populator is `deployment_blueprint_effective_values.py` (§5.5).

**`app_deployment_debug_simulator.py` (missed):** `:17` subdomain default (same as above); `:26` debug ALB DNS string (cosmetic/internal-only).

**`environment_teardown_executor.py` (missed):** no direct names, but `deploy_base.teardown(env_slug)` needs `platform_org_slug` threaded. The sandbox-skip logic (`:138-146`, "never delete shared base stacks") is unaffected but is the multi-tenant invariant the scheme depends on — sanity-check it still holds.

**`permissions_apply_executor.py` (missed):** delegates to `iam_utils.write_app_permissions_policy` (which has the objects → easy fix). Flags that today's `[:64]` is naive truncation → must become the bounded `task_role_name` (§4.1), more important once org is added (naive truncation risks cross-org collision).

**Not sites:** `tenant_consistency.py` (and note: `assert_app_owns_environment` already proves `app.organization_id == environment.aws_account.organization_id` at runtime — the invariant the naming inputs rely on; not DB-enforced), `job_worker.py` (its `label` is worktree job-claiming, unrelated to org).

### 5.3 `management/commands/` — the most-missed area (none were in the doc)

> `humr_hermes_migrate.py` has been **deleted** (no longer used) — its many sites and its two destructive (`rm -rf`/`mv -f`) org-agnostic paths are gone. Do not look for it.

**Operational (target live AWS; break silently post-refactor):**
- `humr_app_exec.py:244` cluster → platform-org; `:245` service → org (**org is free** — `App` already fetched).
- `humr_app_logs.py:170/171/172` cluster (platform) / service (org, free) / log group (platform).
- `humr_app_shell.py:220/221` cluster (platform) / service (org, free).
- `humr_node_shell.py:45/49/80` ASG + cluster (platform) and per-app service — **fully org-agnostic with no `--org` flag and no `App` lookup**; needs a new flag/lookup path.
- `humr_efs_browse.py:61-145` cluster/vpc/efs/exec-role/log-group → platform-org; `:62-63` ephemeral efs-browser role/family (platform-lean — §11); `:13`/`:104` stale `/deployments/<app>` help text → `/deployments/<org>/<app>`.
- `humr_control.py`: `:65`/`:69` help text describing old `humr/{env}/{app}/*` + `/deployments/{app}` (the actual delete logic delegates to `AppRemovalJob`, which already has `organization`); `:640-641` restart-task cluster (platform) / service (org, **free** — already `select_related("organization")`); `:349/:470/:626` bare `App.objects.get(slug=...)` assume global-slug-uniqueness — verify against the per-org constraint (§8) and consider an `--org` disambiguator.
- `humr_bootstrap_sandbox.py:12` documents the `humr-{external_id}` assume-role (**stays** account-level); `:56` cluster-stack check → platform-org; must import settings + pass `platform_org_slug` into `deploy_base.deploy(...)`.

**Secrets / build:**
- `humr_secrets.py`: `:17-34` docstrings teach the old prefix + the **carve-out being deleted** (§7) → rewrite to uniform `humr/<org>/<env>/...`; `:212-282` `shared-*` subcommands resolve the name via the carve-out and don't thread `--org` into name construction; duplicates account-resolution logic with `_aws_account_resolver` (§5.3 resolver note); `purge-deleted`/`list` operate account-wide with no prefix filter (more dangerous in a shared account — flag).
- `humr_build_prebuilt_image.py:4-7/:43/:72` `humr/{env}/{ecr_repo}` → needs an org segment, but there is **no natural org source** (no `--app`, `--ecr-repo` is arbitrary) → **open decision** (§11): add `--org`, or treat prebuilt images as platform-owned.

**Seed / demo:**
- `seed_app_templates.py:287` host-mount template string (priority — §4.5); `:221` stale policy-proxy comment.
- `seed_prepare_demo.py:187-188` fake ECR image URI → org (org in scope via `MERIDIAN_SLUG`); `:126` fake secret ARN (lower priority).
- `seed_local_app.py` creates `App` via `.create()` (bypasses `full_clean`) with no slug-length guard — minor, local-dev only; its get-or-create stub is already immutability-safe.

**`_aws_account_resolver.py` (the centralization chokepoint):** `ResolvedAwsTarget` has **no `organization` field** and `get_aws_account` never returns an `Organization`; raw mode (`--account`/`--env-slug` with no DB) is structurally org-blind. Post-refactor nearly every per-app command needs org context, so this should grow org resolution (and a raw-mode `--org-slug`), or each command keeps reinventing it (two already have). Also note: commands currently resolve org via `aws_account.organization` (the *account* owner), which can diverge from the *app* owner once one account hosts many orgs — prefer `app.organization` for per-app names.

**Fully SAFE (read in full):** `humr_query`, `humr_add_shared_key`, `humr_reset_org_abac`, `humr_seed_env_bearer`, `generate_env_session_jwt_keypair`, `run_job_worker`, `ensure_superuser`, `seed_local_repos`, `seed_test_groups`, `seed_test_users`, `seed_test_apps` (naming axis), `setup_google_oauth_client`, `setup_oidc_org`, `setup_x_oauth_client`.

### 5.4 `services/agent/` + `services/cost/` (missed)

- `cost/bedrock.py:166`, `agent/tools/lookup_access_denied_events.py:143`, `agent/agent_build_prompt.py:158` — task-role reconstruction (§4.1).
- `agent/tools/save_environment.py:23` `f"humr-{slug}-{stack_kind}"` (vpc/cluster) → **platform-org** base-infra stack names (function has no org; read settings).
- `agent/tools/query_app_logs.py:203` log group `/humr/{env}/ecs` → platform-org.
- `agent/repo_analysis/repo_analyzer_system_prompt.md:48,148` and `repo_analysis_schema.py:63` — illustrative `humr/{env}/{app}/secrets` example text → update to `humr/<org>/<env>/<app>/secrets` for consistency (cosmetic; LLM prompt/schema, no functional break).
- `mcp_tools.py`, other `cost/*` — scanned clean.

### 5.5 `services/deployment_blueprint_effective_values.py` (missed; unowned, untested)

`_build_conflict_query` (~`:43-57`) auto-suffixes/rejects subdomains by querying conflicts filtered **only** by `environment__shared_alb_hosted_zone`, with **no org filter**. This is the same cross-org collision surface `SandboxSlugClaim` patches at the slug layer. Under org-scoped DNS (`<app>.<org>.<zone>`, per-org zones) this check becomes both unnecessary and wrong (false-positives across orgs). It is the real populator of `deployment.subdomain` (the §5.2 fallbacks are only last-resort). No test covers cross-org behavior, so the suite won't catch it. **Needs an owner and a decision** before shared-sandbox rollout.

### 5.6 Tests

- `tests/test_sandbox.py` — `TestSandboxAppNameCollision` (≈`:105-193`) tests `SandboxSlugClaim` → **delete**; but **relocate** the 4 surviving classes (`TestSandboxProvisioning`, `TestSandboxNotConfigured`, `TestSandboxTeardownGuard`, `TestSandboxEnvironmentTeardownUI`) — they test sandbox provisioning/teardown, unaffected by the refactor.
- `tests/test_app_deployment_executor.py:174-201` `test_deploy_blueprint_rejects_cross_org_sandbox_slug` → delete.
- `tests/test_env_bearer_token_secrets.py` — many `"humr/staging/shared-secrets"` → `humr/<org>/staging/shared-secrets`; `TestSharedSecretsNamespace` (`:199-261`) tests the carve-out → **delete** (replace with a uniform-path test); `TestEnsureAppSecretsExist` secret paths → org (and the test must supply the new `org_slug` param to `ensure_app_secrets_exist`).
- `tests/test_environment_setup_flow.py:51-52` asserts base-infra `humr-default-vpc`/`-cluster` → platform-org-prefixed (needs `@override_settings(HUMR_PLATFORM_OWNER_ORG_SLUG=...)`; the test's own org is irrelevant to the expected name).
- `tests/test_ec2_cpu_reservation.py:39`, `test_bedrock_platform_capabilities.py:42`, `test_env_bearer_overlay.py:43` — `resource_prefix="humr-staging-my-app"` and sibling `ecr_repo_name="humr/staging/..."` literals → insert org segment.
- **Length-bound names:** no test asserts the task-role/Aurora/target-group string today; if any new test does, the expected value is the **bounded** form (readable-app + rid), not a fully-readable string.
- **Not in scope (customer-side ARNs):** `test_org_aws_accounts.py:37`; `test_bedrock_logging_utils.py:11` (unsure — confirm whether that role is HumR-constructed or customer-side before deciding).

### 5.7 UI templates (display the path to users — must stay accurate)

- `templates/humanityrules_app/apps/_app_remove_confirm_modal.html:57` `humr/{env}/{{ app.slug }}/secrets` → add org; `:67` `/deployments/{{ app.slug }}` → `/deployments/{{ app.organization.slug }}/{{ app.slug }}`.
- `templates/humanityrules_app/deploy/template_deploy_form.html:186` `humr/{env}/{{ c.ecr_repo }}:{{ c.version }}` → add org segment (confirm which context var carries the org).
- Note: the literal `{env}` in these is a pre-existing un-interpolated placeholder; don't half-fix (add org but leave `{env}` literal inconsistently).
- `models.py` docstrings — see §8.

### 5.8 Out-of-repo docs (consistency only)

`template_repos/hermes_agent/Dockerfile:226` comment (`/var/lib/humr/hermes-roots/{slug}`) and `template_repos/policy_proxy/README.md:49` (`humr/{env}/policy-proxy`) — update for consistency; the README also documents the per-env-shared (→ platform-owned) nature of the policy-proxy repo.

---

## 6. Retire `SandboxSlugClaim` (closure checklist)

Once resources are org-namespaced, a cross-org slug collision is impossible, so the reservation table is dead weight. Remove **all** of:

- `models.py:895-918` `class SandboxSlugClaim` → delete.
- `migrations/0012_sandboxslugclaim.py` — current highest migration is `0012`. Add `0013` with `DeleteModel('SandboxSlugClaim')` (+ the §8 `AlterField`s). The design doc says "remove the model and its migration"; deleting the `0012` file outright is only safe if it has never been applied to a persisted DB — given the pre-beta "fresh DB / recreate envs" rollout that's plausible, but the standard-safe path is a `0013` reversal. **Decision in §11.**
- `services/sandbox_service.py` — drop `SandboxSlugClaim` from the import (`:12`); delete `aclaim_sandbox_app_slug` (`:63-95`) and `release_sandbox_app_slug` (`:98-100`); delete the now-dead `_sandbox_slug_taken_message` (`:56-60`); **rewrite the module docstring** (`:1-6`) — it asserts the "single global slug namespace across every org's sandbox" premise that the refactor falsifies. Keep `is_sandbox_configured`/`ensure_org_sandbox`/`HUMR_SANDBOX_ENV_SLUG` (orthogonal).
- `services/app_templates/template_deploy_service.py:168-175` — remove the `aclaim_sandbox_app_slug` call + its explanatory comment. **Then check `App.objects.acreate` (~`:202`):** today the claim's pre-check is what prevents a conflicting insert; once it's gone, decide whether to add `IntegrityError` handling (the per-org `unique_app_slug_per_org` constraint will now be the thing that fires).
- `services/agent/tools/deploy_blueprint.py:71-75` — remove the call (clean 5-line deletion).
- `services/jobs/app_remove_executor.py:204` — remove the call + comment (§5.2).
- Tests: `test_sandbox.py` + `test_app_deployment_executor.py` (§5.6).

---

## 7. De-special-case the shared-secrets namespace (closure checklist)

The per-org sandbox carve-out was a one-off fix for the secret names; under uniform org-naming it's no longer special — `humr/<org>/<env>/shared-secrets` like everything else.

- `secrets_utils.py:37-47` `env_shared_secrets_namespace` (the `if aws_account.is_humr_sandbox: return f"sandbox/{org}"` branch) → delete; fold into `shared_secrets_secret_name` (`:50-52`) as the unconditional `humr/<org>/<env>/shared-secrets`.
- `tests/test_env_bearer_token_secrets.py` `TestSharedSecretsNamespace` (`:199-261`) → delete; replace with a uniform-path assertion (same path for sandbox and dedicated).
- Docstring/help text describing the carve-out: `models.py:1814-1816` (EnvironmentBearerToken), `management/commands/humr_secrets.py:31-34`, `management/commands/humr_control.py:65`.

---

## 8. Slug constraints (immutable + length-capped)

Slugs appear in every resource name, so they must become immutable and length-capped. A mutable display name covers UX renaming.

- `Organization.slug` (`models.py:77`): `SlugField(max_length=255, unique=True)` → `max_length=30`. Globally unique already ✓.
- `App.slug` (`models.py:827`): `SlugField(max_length=255)`; uniqueness is the `UniqueConstraint(["organization","slug"], name="unique_app_slug_per_org")` at `:884-889` → **keep the constraint**, set `max_length=30`.
- **Immutability:** Django has no declarative "immutable field." Follow the existing precedent — `User.save()` (`models.py:42-55`) raises on a changed `username`. Add an analogous `save()` guard on `Organization` and `App` for `slug` when `pk` is set. (This is model code, **not** a migration.)
- **Display name:** confirm/add `App.display_name` / `Organization.display_name` for renaming UX (check current fields).
- Pre-migration: check no existing `org`/`app` slug exceeds 30 chars (seed/fixtures/any live data) before tightening.
- Owning-org access path: `app.organization.slug` (direct denormalized FK, `models.py:803-808`, one hop). Env's owning org (for platform-org infra it's irrelevant — use settings): `environment.aws_account.organization.slug`.

Docstrings in `models.py` carrying old paths: `:705` (ECR `humr/{env}/` namespace), `:735` (`/deployments/<app>/<subpath>`), `:1579` (AppRemovalJob `/deployments/{app}`), `:1814-1816` (shared-secrets carve-out — §7).

---

## 9. Threading `org_slug` / `platform_org_slug` (plumbing guide)

The audit confirmed: in nearly every site, the needed value is one attribute access away.

- **Per-app names** need `org_slug = app.organization.slug`. Every executor/command that loads an `App` has it (add `"organization"` to `select_related` to avoid N+1). Functions that today take bare `env_slug: str`/`app_name: str` (most of `deploy_app.py`'s helpers, `secrets_utils.ensure_app_secrets_exist`, `cloudformation_utils.get_app_urls`, `example_apps.*`) must gain an explicit `org_slug: str` param — or carry it on `AppConfig` (§3).
- **Base/platform infra** needs `platform_org_slug = settings.HUMR_PLATFORM_OWNER_ORG_SLUG` (confirmed at `humanityrules_site/settings.py:242`, default `"humanity-rules"`). It never varies per call — read it inside `deploy_base.py` (and `save_environment.py`, `query_app_logs.py`, the operational commands) rather than threading through every caller. **Not** `is_platform_owner_org(...)` (`views/integrations/platform_owner.py:17`, a bool predicate, wrong tool).
- The two values **differ** when a tenant deploys an app (tenant org for app resources, platform org for the shared ALB/policy-proxy it touches) — don't cross them (§1.4 trap).
- `_aws_account_resolver.py` is the natural place to centralize org resolution for commands (§5.3).

---

## 10. Explicitly out of scope / confirmed no-change

- **Per-account assume-role** `humr-<external_id>` (`iam_utils.py:59`; created by CDK `infra_humanityrules/stacks/sandbox_stack.py:45`). Keyed by `AWSAccount.external_id` (per-account secret), not org/env/app. One per account, used to deploy *all* orgs into that account → correctly account-level. Stays.
- **Bedrock invocation logging** (`bedrock_logging_utils.py`) — account+Region singleton (§2 #3).
- **`infra_humanityrules/`** (HumR's own control-plane CDK) — no org/env/app resource naming beyond the assume-role above.
- **Zero-change infra utils:** `ecr_utils`, `acm_utils`, `route53_utils`, `vpc_utils`, `ecs_utils`, `cdk_utils` (names supplied by callers).
- **Django URL routes** (`urls.py` `apps/<slug>/deployments/...`) — web routes, not AWS/FS names.
- **`local/`, vendored Hermes** — local/working artifacts.

---

## 11. Open decisions (need a human call before/while implementing)

1. **Shared ALB name (4th length-bound):** bounded `humr-<env>-<rid>`, or pragmatic exception `humr-<env>-shared` + `Org` tag (base infra is per-account-per-env, so env is already unique)? (§2 #1)
2. **Tagging resources with no natural `App`:** policy-proxy ECR repo, prebuilt images — omit `App`, use a sentinel, or tag with the consuming scope? (§5.1, §5.3)
3. **Subdomain default:** org-scope only when `aws_account.is_humr_sandbox`, or always? And who owns/fixes the org-unaware conflict query in `deployment_blueprint_effective_values.py`? (§5.5)
4. **`humr_build_prebuilt_image` org source:** add `--org`, or treat prebuilt images as platform-owned? (§5.3)
5. **Ephemeral tool roles** (`efs-browser-role`/family; any future stager) — platform-org-owned, or tenant-org-owned (they touch one tenant's data)? (§5.1, §5.3)
6. **Migration mechanics for `SandboxSlugClaim`:** delete `0012` outright (clean-slate, pre-beta) vs. add a `0013` reversal (standard-safe). (§6)
7. **`App.slug` global vs per-org uniqueness assumption** in the bare `App.objects.get(slug=...)` command lookups — add `--org` disambiguation? (§5.3 `humr_control`)
8. **`_aws_account_resolver` centralization** — in scope for this refactor or separate? (§5.3)

---

## 12. Suggested implementation order

1. **Naming module (§3) + the `bounded_app_name`/`app_rid` helpers (§1.2)** — nothing else is safe until the coupled sites share one source of truth.
2. **Models (§8): slug `max_length`, immutability guards, display name; drop `SandboxSlugClaim`; new migration.** Decide §11.6 first.
3. **`AppConfig` org field (§3) + thread `org_slug`/`platform_org_slug` (§9).**
4. **`services/infra_customer/` (§5.1)** — the resource-creation ground truth, including the four length-bound names and full tagging.
5. **`services/jobs/` (§5.2)** — must match #4 exactly (the §4 couplings).
6. **`services/agent/` + `services/cost/` read-side reconstructions (§5.4, §4.1)** — switch to the helper.
7. **Retire `SandboxSlugClaim` call sites (§6) + de-special-case secrets (§7).**
8. **Management commands (§5.3)** — thread org, fix the operational commands, rewrite help text.
9. **`deployment_blueprint_effective_values.py` (§5.5)** — after the DNS decision (§11.3).
10. **Tests (§5.6) + UI templates (§5.7) + doc comments (§5.8).**

Verify after #4-#6 with a grep for any surviving literal: `rg 'humr-\{|humr/\{|f"humr-|f"humr/|-task-role"\[:|hermes-roots/\{app|deployments/\{app'`.

---

## 13. Rollout

Pre-beta: redeploy everything. The HumR sandbox environment is torn down and recreated (the few existing HAs have no value), which sidesteps the stored-stack-name caveat (§5.2 `environment_provisioning_executor`) and the migration-deletion question (§11.6). After changing the seed template (§4.5), recreate the DB or re-run `seed_app_templates`. Watch the env-bearer token sync during cutover (recreating the sandbox is exactly the event class that has previously wedged deploys via DB-hash vs Secrets-Manager drift).
