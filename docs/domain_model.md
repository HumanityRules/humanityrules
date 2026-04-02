# Domain Model

**Purpose:** This document describes the core domain model and business logic so an LLM agent can understand how the platform works without reading the source code. It covers entities, relationships, authorization, and key behavioral flows. Operational models (logs, caches, usage tracking) are excluded.


## Core Concepts

- **Organization** — Top-level tenant. All resources, users, policies, and integrations are scoped to an organization.
- **Workspace** — Governance container for Apps and Datastores. Used for access-control grouping. A "Default" workspace is auto-created with every new Organization.
- **Repository** — A Git repository connected to an organization via GitHub App integration.
- **App** — Stable identity and build configuration for a deployable application. Belongs to a Workspace and sources code from a Repository.
- **DeploymentBlueprint** — Desired deployable state for one (App, Environment) pair. Owns runtime configuration: cpu, memory, env vars, secrets, datastore binding, subdomain.
- **Deployment** — An execution record for one attempt to apply a Blueprint. Tracks build, deploy, and teardown lifecycle.
- **Environment** — Deployment target with its own VPC, ECS cluster, and shared ALB. Scoped to an AWS Account. Multiple Workspaces can deploy Apps to the same Environment.
- **Datastore** — Managed database (Aurora) provisioned within an Environment and bound to an App via a Blueprint.
- **AppPermissions** — The last-applied IAM policy baseline for an (App, Environment) pair.
- **AppPermissionRequest** — A request to modify IAM task-role policies for a deployed App, with approval workflow.
- **ABAC (Attribute-Based Access Control)** — Authorization system based on identity attributes, resource tags, and policies. Access is derived, not directly assigned.


## High-Level Mental Model

- Organization owns AWS Accounts, Git integrations, Workspaces, and ABAC configuration.
- AWS Account has Environments (shared infrastructure: VPC, ECS cluster, shared ALB).
- Workspace contains definitions (Apps, Datastores) — the "what" to deploy.
- App sources code from a Repository and defines identity + build config.
- DeploymentBlueprint pairs an App with an Environment and configures runtime settings — the "desired state."
- Environment is where things run (AWS account + region + VPC + ECS cluster + shared ALB) — the "where."
- Deployment is an execution record for one attempt to apply a Blueprint.
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
│   ├── Apps → Repository (source)
│   │   ├── DeploymentBlueprints → Environment (target)
│   │   │   └── Deployments
│   │   ├── AppPermissions → Environment
│   │   └── AppPermissionRequests → Environment
│   └── Datastores
├── ABAC
│   ├── IdentityAttributes → User
│   ├── Groups
│   │   ├── GroupMemberships → User
│   │   └── GroupAttributes
│   ├── ResourceTags → (Workspace | Environment | App)
│   └── Policies
└── Conversations
    └── Messages
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
- **role_arn** — IAM role ARN that DevOpsHero assumes
- **status** — pending / connected / error
- Unique constraint: (organization, name)

### GitProviderIntegration
Organization-level connection to a Git provider.
- **organization** — FK to Organization
- **provider** — github / gitlab
- **status** — pending / connected / error
- **installation_id** — GitHub App installation ID

### Repository
- **organization** — FK to Organization
- **integration** — FK to GitProviderIntegration (null for local repos)
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
- **shared_alb_hosted_zone** — Hosted zone for wildcard cert (e.g., "dev.example.com"). Empty = HTTP only.
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
Stable identity and build configuration.
- **organization** — FK to Organization (denormalized for unique constraint)
- **workspace** — FK to Workspace
- **repository** — FK to Repository (required)
- **name, slug** — Display name and URL-safe identifier
- **app_type** — web / worker / scheduled
- **build_strategy** — dockerfile / nixpacks / buildpack
- **repo_subpath** — Subdirectory within repository (for monorepos)
- **branch** — Default git branch
- **dockerfile_path** — Path to Dockerfile (if using dockerfile strategy)
- **container_port** — Port the container listens on
- **health_check_path** — HTTP path for health checks
- **health_check_command** — Command for non-HTTP health checks
- Unique constraint: (organization, slug)

### DeploymentBlueprint
Desired deployable state for one (App, Environment) pair.
- **app** — FK to App
- **environment** — FK to Environment
- **status** — draft / deploying / failed / active / discarded
- **branch** — Branch override (blank = use repository's default_branch)
- **cpu** — Fargate CPU units (256, 512, 1024, etc.)
- **memory** — Fargate memory in MiB
- **environment_variables** — List of {name, value} objects
- **app_secrets** — Dict mapping secret field names to values (null value = auto-generate a random value)
- **datastore** — FK to Datastore (optional binding)
- **subdomain** — Route53 subdomain override (blank = use app slug, auto-suffixed if conflict)

Status lifecycle: draft → deploying → active (on success) / failed. Discarded after teardown.

### Datastore
Managed Aurora database definition.
- **workspace** — FK to Workspace
- **name, slug** — Display name and URL-safe identifier
- **engine** — aurora-mysql / aurora-postgresql
- **deployment_mode** — aurora_serverless_v2 / aurora_provisioned
- **serverless_min_acu, serverless_max_acu** — ACU limits for serverless mode
- **provisioned_instance_class** — Instance class for provisioned mode
- **database_name** — Database name within the cluster
- **storage_encrypted, deletion_protection, backup_retention_days** — Security/backup settings
- **status** — pending / creating / available / error / deleting
- **cluster_arn, cluster_endpoint, credentials_secret_arn, connection_secret_arn** — AWS outputs
- Unique constraint: (workspace, slug)

### Deployment
An execution record for one attempt to apply a Blueprint.
- **blueprint** — FK to DeploymentBlueprint
- **app** — FK to App
- **environment** — FK to Environment
- **git_ref** — Branch, tag, or commit SHA
- **git_commit_sha** — Resolved commit SHA
- **image_tag** — Docker image tag (generated: `{app_slug}-{short_ref}-{timestamp}`)
- **image_uri** — Full ECR image URI (set after push)
- **subdomain** — Effective subdomain for this deployment (resolved from blueprint at deployment time)
- **status** — pending / building / pushing / deploying / starting / succeeded / failed / rolled_back / torn_down / teardown_pending / tearing_down
- **service_url** — URL where the deployed service is accessible
- **alb_dns** — ALB DNS name
- **started_at, completed_at** — Timing
- Linked to Conversations via M2M

Status lifecycle: pending → building → pushing → deploying → starting → succeeded / failed. Teardown: teardown_pending → tearing_down → torn_down.


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
The last-applied IAM policy baseline for an (App, Environment) pair.
- **statements** — List of policy statement dicts (each has service, effect, access_levels, resources)
- Unique constraint: (app, environment)
- Seeded from AWS on first access (reads existing IAM policy)

### AppPermissionRequest
A request to modify task-role permissions.
- **app** — FK to App
- **environment** — FK to Environment
- **statements** — Proposed policy statements
- **description** — Human/agent-authored rationale
- **status** — draft / approved_pending_apply / applying / applied / failed

Workflow: The permissions agent helps the user build a draft → user approves → job worker applies to IAM → on success, AppPermissions baseline is updated to match. Canceling resets the draft to the current baseline.


## Conversation and Agent

### Conversation
A conversation between a user and the AI deployment agent.
- **user** — FK to User
- **organization** — FK to Organization
- **status** — active / completed / abandoned
- **mode** — general / environment_setup / app_deployment / permissions
- **session_id** — Claude Agent SDK session ID
- **deployments** — M2M to Deployment

Context FKs (set via UI before conversation starts, enriched by agent tools during):
- **context_workspace** — Workspace scope
- **context_repository** — Repository scope
- **context_aws_account** — AWS Account scope (for environment setup)
- **context_environment** — Environment scope (for environment setup)
- **context_app** — App scope (set by save_app tool)
- **context_deployment_blueprint** — Blueprint scope (set by save_blueprint tool)
- **context_app_permission_request** — Permission request scope (for permissions mode)

Mode determines system prompt, available tools, and model selection.

### Message
A single message in a conversation.
- **role** — user / agent / system
- **content_type** — text / markdown / code / progress / choice / deployment_log / error / tool_call / system_trigger
- **content** — Message content (JSON for structured types, plain text for text/markdown)
- **metadata** — JSON: code language, choice options, log level, etc.


## Key Domain Behaviors

### Environment Provisioning Flow
1. Agent creates an Environment record (status: draft or pending)
2. Job worker claims pending environments, transitions to provisioning
3. CDK deploys base infrastructure: VPC, ECS cluster, shared ALB (with optional wildcard cert)
4. On success: vpc_id and cluster_arn are synced from CloudFormation outputs, status → ready
5. On failure: status → error

### Deployment Flow
1. Agent tool `deploy_blueprint` creates a Deployment record (status: pending) and sets blueprint to deploying
2. Job worker claims pending deployments, transitions to building
3. Executor clones repository, builds AppConfig from blueprint, deploys via CDK
4. CDK creates/updates: ECR repository, ECS task definition, ECS service, ALB target group, Route53 records
5. On success: deployment status → succeeded, blueprint → active, service_url populated
6. On failure: both deployment and blueprint → failed

### Effective Values Resolution
When deploying, several values are resolved from the blueprint + app + environment:
- **Branch** — Blueprint's branch override, falling back to repository's default_branch
- **Subdomain** — Blueprint's explicit subdomain, or app slug with automatic conflict-aware suffixing (`{app_slug}-{env_slug}` if `{app_slug}` conflicts with another active deployment on the same hosted zone)
- **URL** — `https://{subdomain}.{hosted_zone}` when the environment has a hosted zone

### Teardown Flows
- **App teardown:** Deployment → teardown_pending → tearing_down → torn_down. CDK deletes app stacks. Blueprint → discarded.
- **Environment teardown:** All deployments torn down first (sequentially, stop on failure), then cluster/VPC CloudFormation stacks deleted, then environment record deleted from database.

### Permissions Apply Flow
1. User approves AppPermissionRequest → status: approved_pending_apply
2. Job worker claims it, transitions to applying
3. Executor converts statements to IAM policy document, calls put_role_policy on the ECS task role
4. On success: AppPermissions baseline updated to match, request → applied
5. On failure: request → failed

### Job Worker
A polling-based background worker that claims pending jobs using `SELECT ... FOR UPDATE SKIP LOCKED` and spawns threads for execution. Handles five job types: environment provisioning, app deployment, app teardown, environment teardown, permissions apply.

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
- **Datastore.slug** — Unique per workspace
- **Policy.name** — Unique per organization

### App Slug Uniqueness Rationale
App slugs are unique per organization (not globally or per workspace) because:
1. Organizations are tenant boundaries — prevents cross-tenant information leakage
2. AWS resource names stay short: `doh/{env_slug}/{app_slug}` works since environments are per-account, accounts are per-org
3. Org-scoped uniqueness avoids global leakage: with a global constraint, a user could infer another org's app via slug postfixes (e.g., `-1`). Within an org, collisions only reveal info to members who already share access.

### Domain and URL Resolution
Apps use shared ALB with host-based routing:
- Environment has `shared_alb_hosted_zone` (e.g., `dev.example.com`)
- App gets domain `{subdomain}.{shared_alb_hosted_zone}` (e.g., `my-app.dev.example.com`)
- Subdomain defaults to app slug, auto-suffixed with `-{env_slug}` if another app on the same hosted zone already uses that subdomain

### AWS Resource Naming
- **Base infrastructure** — `devopshero-{env_slug}-*` (VPC, cluster, execution role)
- **App resources** — `doh-{env_slug}-{app_slug}-*` (ALB target group, ECS service, task role)
- **ECR path** — `doh/{env_slug}/{app_slug}`
- **Secrets Manager** — `devopshero/{app_slug}/secrets`

### Compute Substrate
- v1 supports ECS/Fargate only
- ECS/Fargate is treated as implicit environment capacity
- Users do not choose substrate at deployment time in v1
