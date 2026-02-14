# Domain Model

## Core Concepts

- **Organization** — Top-level tenant. Users belong to organizations, and organizations own all other resources.
- **Workspace** — Governance and policy container. Groups apps and datastores for access control and organizational purposes. A "Default" workspace is auto-created when an Organization is created.
- **Repository** — A Git repository connected to an organization via GitHub App integration. Apps source their code from repositories.
- **App** — Compute workloads users deploy. Apps belong to a workspace and source from a repository.
- **Datastore** — Managed databases (Aurora in v1) provisioned and managed by the platform.
- **Environment** — Deployment target with its own VPC and ECS cluster. Account-scoped: multiple workspaces can deploy apps to the same environment.
- **Deployment** — Binds an app to an environment with a specific git ref. Tracks build and deploy status.


## High-Level Mental Model

- Organization has AWS Accounts and Git integrations.
- AWS Account has Environments (shared infrastructure: VPC, ECS cluster, shared ALB).
- Organization has Repositories (synced from GitHub).
- Workspace contains definitions (Apps, Datastores) — the "what" to deploy.
- App sources code from a Repository.
- Environment is where things run (AWS account + region + VPC + ECS cluster) — the "where".
- Deployment binds an App to an Environment and selects the git ref.
- Multiple workspaces can deploy to the same environment, sharing VPC and cluster while having isolated app resources (ECR, ECS service, secrets).


## Compute Substrate

- v1 supports ECS/Fargate only.
- ECS/Fargate is treated as implicit environment capacity.
- Users do not choose substrate at deployment time in v1.


## Entity Relationships

```
Organization
├── OrganizationMembership (user + role)
├── AWS Accounts
│   └── Environments
│       └── EnvironmentLogs
├── Git Integrations
│   └── Repositories
├── Workspaces
│   ├── Apps → Repository (source)
│   │   └── Deployments → Environment (target)
│   │       └── DeploymentLogs
│   └── Datastores
└── Conversations
    └── Messages
```


## Object Model

### Organization
- **id** — UUID primary key
- **name** — Display name
- **slug** — URL-safe unique identifier
- **created_at, updated_at** — Timestamps

Relationships:
- Has many AWS Accounts
- Has many Git Integrations
- Has many Repositories
- Has many Workspaces
- Has many Conversations
- Has many Apps (denormalized via Organization FK on App)

### OrganizationMembership
- **id** — UUID primary key
- **user** — FK to User
- **organization** — FK to Organization
- **role** — admin / member / viewer
- **created_at** — Timestamp

### AWS Account
- **id** — UUID primary key
- **organization** — FK to Organization
- **name** — User-friendly name (e.g., "Production", "Staging")
- **aws_account_id** — 12-digit AWS account ID (populated after verification)
- **external_id** — UUID for secure cross-account AssumeRole
- **role_arn** — IAM role ARN that DevOpsHero assumes
- **status** — pending / connected / error
- **status_message** — Error details if any
- **created_by** — FK to User
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Environments
- Unique constraint: (organization, name)

### GitProviderIntegration
Organization-level connection to a Git provider (GitHub, GitLab).
- **id** — UUID primary key
- **organization** — FK to Organization
- **provider** — github / gitlab
- **status** — pending / connected / error
- **installation_id** — GitHub App installation ID
- **access_token_encrypted** — Encrypted OAuth access token
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Repositories

### Repository
A Git repository connected via GitHub App integration.
- **id** — UUID primary key
- **organization** — FK to Organization
- **integration** — FK to GitProviderIntegration (null for local repos)
- **provider** — github / gitlab / local
- **external_id** — Provider's repository ID
- **name** — Repository name (e.g., "flask-api")
- **full_name** — Full repository name (e.g., "acme/flask-api")
- **default_branch** — Default branch (usually "main")
- **clone_url** — HTTPS clone URL or file:// for local
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Apps
- Unique constraint: (organization, full_name)

### Environment
Deployment target with shared infrastructure. Account-scoped.
- **id** — UUID primary key
- **aws_account** — FK to AWS Account
- **name** — Display name (default / staging / prod / custom)
- **slug** — URL-safe identifier
- **aws_region** — AWS region (e.g., us-east-1)
- **status** — pending / provisioning / ready / error
- **status_message** — Status details
- **vpc_stack_name** — CloudFormation stack name for VPC
- **cluster_stack_name** — CloudFormation stack name for ECS cluster
- **vpc_id** — VPC ID (populated after provisioning)
- **cluster_arn** — ECS cluster ARN (populated after provisioning)
- **shared_alb_hosted_zone** — Hosted zone for wildcard cert (e.g., "dev.example.com"). Empty = HTTP only.
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Deployments
- Has many EnvironmentLogs
- Unique constraint: (aws_account, slug)

A "default" environment is provisioned explicitly during setup (via `provision_environment`), not auto-created.

### Workspace
Governance and policy container.
- **id** — UUID primary key
- **organization** — FK to Organization
- **name** — Display name
- **slug** — URL-safe identifier
- **description** — Optional description
- **created_by** — FK to User
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Apps
- Has many Datastores
- Unique constraint: (organization, slug)

A "Default" workspace is auto-created when an Organization is created.

### App
A deployable application.
- **id** — UUID primary key
- **organization** — FK to Organization (denormalized for unique constraint)
- **workspace** — FK to Workspace
- **repository** — FK to Repository (required)
- **name** — Display name
- **slug** — URL-safe identifier, unique per organization
- **app_type** — web / worker / scheduled
- **build_strategy** — dockerfile / nixpacks / buildpack
- **repo_subpath** — Subdirectory within repository (for monorepos)
- **branch** — Git branch to deploy
- **dockerfile_path** — Path to Dockerfile if using dockerfile strategy
- **container_port** — Port the container listens on
- **cpu** — Fargate CPU units (256, 512, 1024, etc.)
- **memory** — Fargate memory in MiB
- **health_check_path** — HTTP path for health checks
- **health_check_command** — Command for non-HTTP health checks
- **environment_variables** — List of {name, value} objects
- **datastore** — FK to Datastore (optional binding)
- **app_secrets** — Dict mapping secret field names to values (null = auto-generate)
- **created_by** — FK to User
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Deployments
- Unique constraint: (organization, slug)

### Datastore
Managed database definition.
- **id** — UUID primary key
- **workspace** — FK to Workspace
- **name** — Display name
- **slug** — URL-safe identifier
- **engine** — aurora-mysql / aurora-postgresql
- **engine_version** — Engine version (uses default if blank)
- **deployment_mode** — aurora_serverless_v2 / aurora_provisioned
- **serverless_min_acu, serverless_max_acu** — ACU limits for serverless
- **provisioned_instance_class** — Instance class for provisioned mode
- **database_name** — Database name within the cluster
- **storage_encrypted** — Boolean (default true)
- **deletion_protection** — Boolean (default false)
- **backup_retention_days** — Integer (default 7)
- **status** — pending / creating / available / error / deleting
- **status_message** — Status details
- **cluster_arn** — Aurora cluster ARN (populated after creation)
- **cluster_endpoint** — Aurora endpoint (populated after creation)
- **credentials_secret_arn** — Secrets Manager ARN for credentials
- **connection_secret_arn** — Secrets Manager ARN for connection string
- **created_by** — FK to User
- **created_at, updated_at** — Timestamps

Relationships:
- Has many Apps (via binding)
- Unique constraint: (workspace, slug)

### Deployment
A deployment of an app to an environment.
- **id** — UUID primary key
- **app** — FK to App
- **environment** — FK to Environment (target)
- **git_ref** — Branch, tag, or commit SHA
- **git_commit_sha** — Resolved commit SHA
- **git_commit_message** — Commit message
- **image_tag** — Docker image tag
- **image_uri** — Full ECR image URI (set after push)
- **status** — pending / building / pushing / deploying / starting / deployed / failed / rolled_back / superseded / torn_down / teardown_pending / tearing_down
- **status_message** — Status details
- **started_at** — When deployment started
- **completed_at** — When deployment completed
- **service_url** — URL where the service is accessible
- **alb_dns** — ALB DNS name
- **created_by** — FK to User
- **created_at, updated_at** — Timestamps

Relationships:
- Has many DeploymentLogs
- Linked to Conversations via M2M

### DeploymentLog
Log entries from a deployment.
- **id** — UUID primary key
- **deployment** — FK to Deployment
- **source** — app / cdk / docker / system
- **level** — debug / info / error
- **message** — Log message text
- **details** — JSON: stack name, resource ARN, etc.
- **created_at** — Timestamp

### EnvironmentLog
Log entries from environment provisioning.
- **id** — UUID primary key
- **environment** — FK to Environment
- **source** — system / cdk
- **level** — debug / info / error
- **message** — Log message text
- **details** — JSON: stack name, resource ARN, etc.
- **created_at** — Timestamp


## Conversation and Agent

### Conversation
A conversation between a user and the AI deployment agent.
- **id** — UUID primary key
- **user** — FK to User
- **organization** — FK to Organization
- **context_workspace** — FK to Workspace (set via UI, optional)
- **context_repository** — FK to Repository (set via UI, optional)
- **status** — active / completed / abandoned
- **title** — Conversation title
- **session_id** — Claude Agent SDK session ID
- **deployments** — M2M to Deployment
- **created_at, updated_at** — Timestamps

Context is set via UI before conversation starts (user clicks "New Conversation" or "New App" from workspace page).

### Message
A single message in a conversation.
- **id** — UUID primary key
- **conversation** — FK to Conversation
- **role** — user / agent / system
- **content_type** — text / markdown / code / progress / choice / deployment_log / error / tool_call
- **content** — Message content (JSON for structured types)
- **metadata** — JSON: code language, choice options, log level, etc.
- **created_at** — Timestamp


## Domain Rules and Constraints

### Uniqueness
- **Organization.slug** — Globally unique
- **AWS Account.name** — Unique per organization
- **Repository.full_name** — Unique per organization
- **Environment.slug** — Unique per AWS account
- **Workspace.slug** — Unique per organization
- **App.slug** — Unique per organization (enables short resource names)
- **Datastore.slug** — Unique per workspace

### App Slug Uniqueness Rationale
App slugs are unique per organization (not globally or per workspace) because:
1. Organizations are tenant boundaries — prevents cross-tenant information leakage
2. AWS resource names stay short: `doh/{env.slug}/{app.slug}` works since environments are per-account, accounts are per-org
3. Org-scoped uniqueness avoids global leakage: with a global constraint, a user could infer another org’s app via slug postfixes (e.g., `-1`). Within an org, collisions only reveal info to members who already share access, so it is acceptable from a security viewpoint.

### Domain and URL Resolution
Apps use shared ALB with host-based routing. Domain is derived from environment:
- Environment has `shared_alb_hosted_zone` (e.g., `dev.example.com`)
- App gets domain `{app_slug}.{shared_alb_hosted_zone}` (e.g., `my-app.dev.example.com`)
- No per-app domain configuration needed

### AWS Resource Naming
- **Base infrastructure** — `devopshero-{env_slug}-*` (VPC, cluster, execution role)
- **App resources** — `doh-{env_slug}-{app_slug}-*` (ALB target group, ECS service, task role)
- **ECR path** — `doh/{env_slug}/{app_slug}`
- **Secrets Manager** — `devopshero/{app_slug}/secrets`
