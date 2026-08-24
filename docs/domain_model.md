# Domain Model

**Purpose:** This document describes the core domain model and business logic so an LLM agent can understand how the platform works without reading the source code. It covers entities, relationships, authorization, and key behavioral flows. Operational models (logs, caches, usage tracking) are excluded.


## Core Concepts

- **Organization** — Top-level tenant. All resources, users, policies, and integrations are scoped to an organization.
- **Workspace** — Governance container for Apps. Used for access-control grouping. A "Default" workspace is auto-created with every new Organization.
- **Repository** — A Git repository connected to an organization via GitHub App integration.
- **App** — Identity, build, runtime configuration, **and live deployment state** for a deployable application. Belongs to a Workspace, deploys to exactly one Environment, and sources code from a Repository. Owns runtime configuration (cpu, memory, compute mode, per-container env vars and secrets) and the single in-flight job plus the last-known live state (`job_status`, `live_state`, `service_url`, …).
- **DeploymentRecord** — An append-only audit event for one App job attempt (deploy, teardown, or removal). Records the trail; never read operationally.
- **DeploymentLog** — Log lines produced by one App job attempt, keyed by `(app, attempt_id)`.
- **Environment** — Deployment target with its own VPC, ECS cluster, and shared ALB. Scoped to an AWS Account. Multiple Workspaces can deploy Apps to the same Environment. Apps live and die with their Environment: tearing down an Environment deletes its Apps.
- **AppPermissions** — The last-applied IAM policy baseline for an App.
- **AppPermissionRequest** — A request to modify IAM task-role policies for a deployed App, with approval workflow.
- **ABAC (Attribute-Based Access Control)** — Authorization system based on identity attributes, resource tags, and policies. Access is derived, not directly assigned.


## High-Level Mental Model

- Organization owns AWS Accounts, Git integrations, Workspaces, and ABAC configuration.
- AWS Account has Environments (shared infrastructure: VPC, ECS cluster, shared ALB).
- Workspace contains definitions (Apps) — the "what" to deploy.
- App sources code from a Repository, defines identity + build + runtime config, and points at the one Environment it deploys to. It also carries its own runtime state: the one in-flight job (`job_status`), what is actually running (`live_state`), and the outputs of the last successful deploy.
- Environment is where things run (AWS account + region + VPC + ECS cluster + shared ALB) — the "where."
- DeploymentRecord/DeploymentLog are the append-only audit trail and log lines for App job attempts, correlated by `attempt_id`.
- Multiple Workspaces can deploy to the same Environment, sharing VPC and cluster while having isolated app resources (ECR, ECS service, secrets).
- ABAC controls who can do what: identity attributes on users are matched against resource tags on Workspaces/Environments/Apps via Policies.
- AppPermissionRequests manage the IAM policies attached to an App's ECS task role, with a draft → approve → apply workflow.


## Entity Relationships

```
Organization
├── OrganizationMembership (user + role)
├── AWS Accounts
│   └── Environments
├── Git Integrations
│   └── Repositories
├── Workspaces
│   └── Apps → Repository (source), Environment (target)
│       ├── DeploymentRecords (audit events)
│       ├── DeploymentLogs (job log lines)
│       ├── AppPermissions
│       └── AppPermissionRequests
└── ABAC
    ├── IdentityAttributes → User
    ├── Groups
    │   ├── GroupMemberships → User
    │   └── GroupAttributes
    ├── ResourceTags → (Workspace | Environment | App)
    └── Policies
```


## Object Model

### User
Custom user model extending Django's AbstractUser. Links to an external identity provider.
- **workos_user_id** — WorkOS identity (for WorkOS SSO orgs)
- **oidc_sub** — OIDC subject identifier (for Okta/OIDC orgs)
- **current_organization** — FK to Organization (the org the user is currently working in)

### Organization
- **slug** — Globally unique URL-safe identifier
- **default_org_role** — Org-role automatically assigned to new members joining (default: "viewer")
- **auth_provider** — workos / oidc
- **oidc_issuer_url, oidc_client_id, oidc_client_secret** — OIDC configuration (used when auth_provider is "oidc")
- **bootstrap_admin_email** — Email of the first admin (used during org setup)

### OrganizationMembership
- **user** — FK to User
- **organization** — FK to Organization
- **role** — admin / member / viewer
- Unique constraint: (user, organization)

### AWS Account
Customer-owned AWS account connected via cross-account IAM role.
- **organization** — FK to Organization
- **name** — User-friendly name (e.g., "Production")
- **aws_account_id** — 12-digit AWS account ID (populated after verification)
- **external_id** — UUID for secure cross-account AssumeRole
- **role_arn** — IAM role ARN that HumanityRules assumes
- **status** — pending / connected / error
- Unique constraint: (organization, name)

### IntegrationGitProvider
Organization-level connection to a Git provider.
- **organization** — FK to Organization
- **provider** — github / gitlab
- **status** — pending / connected / error
- **installation_id** — GitHub App installation ID

### Repository
- **organization** — FK to Organization
- **integration** — FK to IntegrationGitProvider (null for local repos)
- **provider** — github / gitlab / local
- **name** — Repository name (e.g., "flask-api")
- **full_name** — Full name (e.g., "acme/flask-api")
- **default_branch** — Default branch (usually "main")
- **clone_url** — HTTPS clone URL or file:// for local
- Unique constraint: (organization, full_name)

### Environment
Deployment target with shared infrastructure. Account-scoped.
- **aws_account** — FK to AWS Account
- **name, slug** — Display name and URL-safe identifier
- **aws_region** — AWS region (e.g., us-east-1)
- **status** — draft / pending / provisioning / ready / error / discarded / teardown_pending / tearing_down
- **vpc_stack_name, cluster_stack_name** — CloudFormation stack names (set when provisioning starts)
- **vpc_id, cluster_arn** — AWS resource IDs (populated after provisioning)
- **shared_alb_hosted_zone** — Environment-owned hosted zone for the wildcard certificate and A alias (e.g., "dev.example.com"). Empty = HTTP only.
- Unique constraint: (aws_account, slug)

Status lifecycle: draft → pending → provisioning → ready. Error can occur from provisioning. Teardown: teardown_pending → tearing_down → (deleted). Discarded = abandoned draft.

### Workspace
Governance and policy container.
- **organization** — FK to Organization
- **name, slug** — Display name and URL-safe identifier
- **created_by** — FK to User
- Unique constraint: (organization, slug)

A "Default" workspace is auto-created via signal when an Organization is created.

### App
Identity, build, and runtime configuration. Deploys to exactly one Environment, set at creation.
- **organization** — FK to Organization (denormalized for unique constraint)
- **workspace** — FK to Workspace
- **environment** — FK to Environment (PROTECT; the app is deleted when its environment is torn down)
- **repository** — FK to Repository (required; the build branch is the repository's default_branch)
- **name, slug** — Display name and URL-safe identifier; the slug is also the app's hostname label
- **build_strategy** — dockerfile / nixpacks / buildpack
- **repo_subpath** — Subdirectory within repository (for monorepos)
- **dockerfile_path** — Path to Dockerfile (if using dockerfile strategy)
- **container_port** — Port the container listens on
- **health_check_path** — HTTP path for health checks
- **health_check_command** — Command for non-HTTP health checks
- **cpu** — ECS task CPU units (256, 512, 1024, etc.)
- **memory** — ECS task memory in MiB
- **compute_mode** — fargate / ec2
- **containers** — Per-container materialized runtime values (env vars + secrets), one entry per template container
- Unique constraint: (organization, slug)

The App row also carries all runtime deployment state (there is no separate Deployment row):
- **job_status** — The single in-flight operation: idle / deploy_pending / deploying / teardown_pending / tearing_down / removal_pending / removing. The job worker claims the `*_pending` values; executors return the row to `idle` when the attempt settles. `job_in_flight` = anything but idle; `is_pending_removal` = removal_pending/removing.
- **live_state** — What is actually running in AWS: not_deployed / deployed / torn_down. Written only at deploy/teardown **success**, so a failed attempt never clobbers it.
- **may_have_infra** — A deploy attempt (even a failed one) may have created AWS resources. Set when a deploy is claimed, cleared only on teardown success. Gates whether teardown is offered and whether removal tears infra down before purging.
- **service_url, alb_dns, last_deployed_at** — Live-deploy outputs, written only at deploy success and cleared on teardown success.
- **last_attempt_id** — Correlation id shared by the current/latest attempt's DeploymentRecord events and DeploymentLog lines. Kept after the attempt settles; selects the log tab's content and anchors the failure banner.
- **last_attempt_error** — Failure message of the latest attempt; empty when it succeeded or none ran. Drives `display_status` and the failure banner.
- **claimed_by_run** — FK to JobWorkerRun that claimed the in-flight job (liveness input for stale-job detection).
- **display_status / display_status_label** — Derived UI pill vocabulary folding job_status, live_state, and last error (pending / deploying / … / succeeded / failed / torn_down / removing / "").

All of these transitions go through `services/jobs/app_job_service.py` (queue_deploy / queue_teardown / queue_removal / settle_deploy_success / settle_teardown_success / settle_failure), which writes the App fields and the paired DeploymentRecord event together.

### DeploymentRecord
Append-only audit event for one App job attempt. Insert-only; nothing operational reads this table (App fields are the runtime truth).
- **app** — FK to App
- **attempt_id** — Correlation id; rows sharing it describe one attempt (matches `App.last_attempt_id` for the latest attempt)
- **event_type** — deploy_started / deploy_succeeded / deploy_failed / teardown_started / teardown_succeeded / teardown_failed / removal_started / removal_failed (no removal_succeeded — a successful removal cascade-deletes the App row and its events)
- **git_ref** — Branch deployed by this attempt (deploy events only)
- **error** — Failure message (failure events only)
- **created_by** — FK to User (nullable)

### DeploymentLog
Log lines from one App job attempt, keyed by `(app, attempt_id)`. The app-detail log tab selects `attempt_id = app.last_attempt_id`.
- **app** — FK to App
- **attempt_id** — Correlation id (matches DeploymentRecord / `App.last_attempt_id`)
- **source** — app / cdk / docker / system
- **level** — debug / info / error
- **message, details** — Rendered line plus structured context


## ABAC Authorization System

Access control uses Attribute-Based Access Control. Access is never granted directly to a user on a resource — it is always derived through policies that match identity attributes against resource tags.

### Identity Attributes
Key-value pairs on a user, scoped to an organization. Sources:
- **System** — `authenticated:true` (automatic for any logged-in user)
- **Direct** — Assigned explicitly by an org admin (e.g., `org-role:admin`, `team:platform`)
- **Group-inherited** — Inherited from groups the user belongs to

### Groups
Attribute containers, org-scoped. Members inherit group attributes. A group has members (users) and attributes (key-value pairs). If group "Finance Team" has attribute `department:finance`, every member carries that attribute.

### Resource Tags
Key-value pairs on resources. Exactly one of (workspace, environment, app) FK is set per tag.
- **Direct tags** — Applied to the resource itself
- **Inherited tags** — Apps inherit their workspace's tags (source: "inherited:{WorkspaceName}")

Auto-created by signals: workspace-name, environment-name, and app-name tags are created when their respective resources are created.

### Policies
Rules that map identity conditions + resource conditions to allowed actions.
- **identity_conditions** — List of {key, value} dicts, AND-ed. `[{key: "*", value: "*"}]` = wildcard (any identity).
- **resource_conditions** — List of {key, value} dicts, AND-ed. `[{key: "*", value: "*"}]` = wildcard (any resource).
- **actions** — List of action strings. Prefix with "!" for deny.
- **resource_type** — workspace / environment / app
- **is_system** — Display-only flag for seed policies
- Unique constraint: (organization, name)

### Action Hierarchy
- `workspace:admin` implies `workspace:view` and `workspace:edit`
- `environment:admin` implies `environment:view`, `environment:deploy`, and `environment:approve`

### Available Actions
- **Workspace** — workspace:view, workspace:edit, workspace:admin
- **Environment** — environment:view, environment:deploy, environment:approve, environment:admin
- **App** — app:use

### Evaluation Algorithm
1. Load all org policies for the resource_type
2. Compute effective identity attributes (system + direct + group-inherited) as a set
3. Compute effective resource tags (direct + inherited for apps) as a set
4. For each policy: check if identity conditions AND resource conditions match
5. Collect grants and denials from matching policies
6. Expand grants via action hierarchy
7. Remove denied actions (deny-overrides)

### Bootstrapping
When a new organization is created, `bootstrap_organization` seeds:
1. `org-role=admin` attribute on the admin user
2. Nine seed policies granting admin/member/viewer org-roles access with wildcard resource conditions

New members receive the organization's `default_org_role` as an identity attribute. New apps get an open-access `app:use` policy and an `app-name` tag. New workspaces and environments get name tags.


## App Permissions Management

Manages IAM policies on an App's ECS task role — separate from ABAC platform access.

### AppPermissions
The last-applied IAM policy baseline for an App (one-to-one).
- **statements** — List of policy statement dicts (each has service, effect, access_levels, resources)
- Seeded from AWS on first access (reads existing IAM policy)

### AppPermissionRequest
A request to modify task-role permissions.
- **app** — FK to App
- **statements** — Proposed policy statements
- **description** — Human-authored rationale
- **status** — draft / approved_pending_apply / applying / applied / failed

Workflow: The user builds a draft in the permissions editor → user approves → job worker applies to IAM → on success, AppPermissions baseline is updated to match. Canceling resets the draft to the current baseline.


## Key Domain Behaviors

### Environment Provisioning Flow
1. The environment setup form creates an Environment record (status: pending)
2. Job worker claims pending environments, transitions to provisioning
3. CDK deploys base infrastructure: VPC, ECS cluster, shared ALB, and optional wildcard certificate plus A alias
4. On success: vpc_id and cluster_arn are synced from CloudFormation outputs, status → ready
5. On failure: status → error

### Deployment Flow
1. Deploying from a template creates the App (with its environment and materialized runtime config); queueing a deploy opens an attempt (`job_status` → deploy_pending, fresh `last_attempt_id`, deploy_started event)
2. Job worker claims deploy_pending apps once the app's environment is READY: `job_status` → deploying, `may_have_infra` → True, `claimed_by_run` stamped
3. Executor clones repository, builds AppConfig from the App + template, deploys via CDK (image_tag is minted per attempt inside the executor; git_ref is the repository's default branch)
4. CDK creates/updates: ECR repository, ECS task definition, ECS service, ALB target group, and listener rules
5. On success (`settle_deploy_success`): `job_status` → idle, `live_state` → deployed, `service_url`/`alb_dns`/`last_deployed_at` populated, deploy_succeeded event
6. On failure (`settle_failure`): `job_status` → idle, `last_attempt_error` set, deploy_failed event — `live_state`/`service_url` are left untouched, so a failed redeploy of a live app keeps serving

### Hostname Resolution
- The app serves at `https://{app_slug}.{hosted_zone}` when its environment has a hosted zone
- At app creation, a slug whose label an existing app already holds on the same hosted zone is rejected

### Teardown Flows
- **App teardown:** `job_status` teardown_pending → tearing_down; CDK deletes app stacks. On success (`settle_teardown_success`): `job_status` → idle, `live_state` → torn_down, `may_have_infra` cleared, `service_url`/`alb_dns` cleared. The App row survives, still pointing at its environment, and can redeploy.
- **App removal:** Queued via `app_job_service.queue_removal`, which takes no options and moves `job_status` → removal_pending → removing. It is admitted whenever the app is idle and its environment is ready, deployed or not. The removal executor always runs the whole sequence: tear down live infra inline when `may_have_infra`, purge persistent data and Secrets Manager entries (`purge_app_namespace_data`, shared with sandbox environment teardown), delete `app-name=`-scoped Policy rows, release the sandbox slug claim, and delete the App row (cascading DeploymentRecords, DeploymentLogs, permissions, tags). `IntegrationUserCredential` rows are per-user and deliberately survive — see `docs/app_removal_data_cleanup_audit.md`. A failed removal settles back to idle and is retryable, re-running the whole sequence. There is no removal_succeeded event — success deletes the row.
- **Environment teardown:** All apps torn down first (sequentially, stop on failure), then cluster/VPC CloudFormation stacks deleted, then the environment's App rows deleted (releasing sandbox slug claims), then the environment record deleted from database.

### Permissions Apply Flow
1. User approves AppPermissionRequest → status: approved_pending_apply
2. Job worker claims it, transitions to applying
3. Executor converts statements to IAM policy document, calls put_role_policy on the ECS task role
4. On success: AppPermissions baseline updated to match, request → applied
5. On failure: request → failed

### Job Worker
A polling-based background worker that claims pending jobs using `SELECT ... FOR UPDATE SKIP LOCKED` and spawns threads for execution. App jobs are claimed by `App.job_status` (one in-flight job per app by construction — the single field makes overlap impossible). Handles environment provisioning, app deployment, app teardown, app removal, environment teardown, permissions apply, and cost refresh. A stale-job reaper settles apps abandoned in an executing status (`deploying` / `tearing_down` / `removing`) back to idle via `settle_failure` when their owning worker run died or they stopped making progress; claimable (`*_pending`) statuses are left for a new worker to pick up.

### Signal-Driven Auto-Creation
- Organization created → "Default" workspace auto-created
- Workspace created → `workspace-name:{slug}` resource tag auto-created
- Environment created → `environment-name:{slug}` resource tag auto-created
- App created → `app-name:{slug}` resource tag + open-access `app:use` policy auto-created


## Domain Rules and Constraints

### Uniqueness
- **Organization.slug** — Globally unique
- **AWSAccount.name** — Unique per organization
- **Repository.full_name** — Unique per organization
- **Environment.slug** — Unique per AWS account
- **Workspace.slug** — Unique per organization
- **App.slug** — Unique per organization (enables short AWS resource names)
- **Policy.name** — Unique per organization

### App Slug Uniqueness Rationale
App slugs are unique per organization (not globally or per workspace) because:
1. Organizations are tenant boundaries — prevents cross-tenant information leakage
2. AWS resource names stay short: `humr/{env_slug}/{app_slug}` works since environments are per-account, accounts are per-org
3. Org-scoped uniqueness avoids global leakage: with a global constraint, a user could infer another org's app via slug postfixes (e.g., `-1`). Within an org, collisions only reveal info to members who already share access.

### Domain and URL Resolution
Apps use shared ALB with host-based routing:
- Environment has `shared_alb_hosted_zone` (e.g., `dev.example.com`)
- Environment's `*.{shared_alb_hosted_zone}` A alias sends every hostname in the zone to the shared ALB
- App gets domain `{app_slug}.{shared_alb_hosted_zone}` (e.g., `myapp.dev.example.com`)

### AWS Resource Naming
- **Base infrastructure** — `humr-{env_slug}-*` (VPC, cluster, execution role)
- **App resources** — `humr-{env_slug}-{app_slug}-*` (ALB target group, ECS service, task role)
- **ECR path** — `humr/{env_slug}/{app_slug}`
- **Secrets Manager** — `humr/{env_slug}/{app_slug}/secrets`

### Compute Substrate
- v1 supports ECS/Fargate only
- ECS/Fargate is treated as implicit environment capacity
- Users do not choose substrate at deployment time in v1
