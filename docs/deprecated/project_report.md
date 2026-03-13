Generated on Feb 12 2026

# DevOps Hero — Project Report

> This document describes what DevOps Hero is, its architecture, and the set of features that have been implemented. It is written as a reference for LLM agents working on this codebase.

---

## 1. What Is DevOps Hero

DevOps Hero (abbreviated **DOH**) is a SaaS platform that deploys containerized applications to a customer's own AWS account via an AI-powered chat agent. The user describes what they want deployed in natural language; the agent analyzes the repository, provisions infrastructure, builds Docker images, and deploys to ECS Fargate — all inside the customer's VPC.

**Core value proposition:** Heroku-like simplicity, but the infrastructure lives in the customer's private AWS account, preserving enterprise security and compliance.

**Target users:** Full-stack engineers, data scientists, ML engineers, and business staff who can build apps but lack DevOps expertise.

---

## 2. Technology Stack

- **Backend:** Django 6.0 (async views, async ORM), Python 3.14, Uvicorn (ASGI)
- **Frontend:** Django templates, HTMX (SPA-like navigation via HTML partials), Tailwind CSS
- **AI:** Claude (Opus 4.6) via Claude Agent SDK, AWS Bedrock as LLM provider
- **Infrastructure-as-code:** AWS CDK (Python) for both control plane and customer deployments
- **Database:** Aurora PostgreSQL Serverless v2 (production), SQLite (development)
- **Auth:** WorkOS (SSO/OAuth), session-based Django auth
- **Git integration:** GitHub App (installation-based, webhook-driven)
- **Analytics:** PostHog (proxied through CloudFront to bypass ad blockers)
- **Package manager:** uv (all Python commands run via `uv run`)
- **Hosting:** ECS Fargate (ARM64/Graviton), CloudFront CDN, ALB

---

## 3. Domain Model

The data model is multi-tenant with Organization as the top-level boundary. All primary keys are UUID7 (sortable, time-ordered).

### Entity Hierarchy

```
Organization
├── OrganizationMembership (user + role: admin/member/viewer)
├── AWSAccount (cross-account IAM role, status: pending/connected/error)
│   └── Environment (VPC + ECS cluster + shared ALB)
│       ├── Deployment (app bound to environment at a git ref)
│       │   └── DeploymentLog (source: app/cdk/docker/system)
│       └── EnvironmentLog (source: system/cdk)
├── GitProviderIntegration (GitHub App installation)
│   └── Repository (synced from GitHub)
├── Workspace (governance container, "Default" auto-created)
│   ├── App (deployable unit, sources from Repository)
│   └── Datastore (Aurora MySQL/PostgreSQL definition)
├── Conversation (user ↔ agent chat session)
│   ├── Message (role: user/agent/system; content_type: text/markdown/code/tool_call/error/...)
│   └── LLMUsageLog (token counts, cost tracking, per-turn)
└── WaitlistSignup (landing page email capture)
```

### Key Relationships

- **App** belongs to a Workspace, sources code from a Repository, optionally binds a Datastore
- **Deployment** binds an App to an Environment with a specific git ref and tracks the full lifecycle (pending → building → pushing → deploying → starting → deployed/failed)
- **Conversation** holds optional context: workspace, repository, AWS account. These determine the conversation mode and available tools
- **App slug** is unique per organization (not globally) to keep AWS resource names short while preventing cross-tenant information leakage

### AWS Resource Naming Convention

- **Base infrastructure:** `devopshero-{env_slug}-*` (VPC, cluster, execution role)
- **App resources:** `doh-{env_slug}-{app_slug}-*` (target group, ECS service, task role)
- **ECR path:** `doh/{env_slug}/{app_slug}`
- **Secrets Manager:** `devopshero/{app_slug}/secrets`

---

## 4. Control Plane Infrastructure

DevOps Hero itself runs on AWS (us-east-1) across 9 CDK stacks:

- **CertStack** — Wildcard ACM certificate for `*.devopshero.ai` (DNS-validated via Route53)
- **VpcStack** — `10.0.0.0/16` VPC, 2 AZs, public/private subnets, 1 NAT Gateway
- **StorageStack** — Public S3 bucket (CloudFormation templates), private S3 bucket, ECR repo (`devopshero`), EFS filesystem (`doh-prod-claude-sessions` for persisting Claude Agent SDK sessions across container replacements)
- **LambdaStack** — `doh-prod-install-callback` Lambda (Python 3.12) that receives CloudFormation callbacks when customers connect their AWS accounts
- **ClusterStack** — ECS Fargate cluster, internet-facing ALB with HTTPS listener, task execution role, CloudWatch log group (30-day retention)
- **DatabaseStack** — Aurora Serverless v2 PostgreSQL 16.4 (0.5–4 ACUs, encrypted, 7-day backup retention, private subnets only)
- **AppStack** — ECS task definition (2048 CPU / 4096 MB, ARM64), two containers: migration init container (`migrate --noinput && ensure_superuser`) and app container (Uvicorn on port 8000). EFS mounted at `/home/appuser/.claude`. Task role can invoke Bedrock models and assume customer IAM roles (`arn:aws:iam::*:role/devopshero-*`). Zero-downtime rolling deployments (min 100%, max 200%)
- **CdnStack** — CloudFront distribution for `devopshero.ai` and `*.devopshero.ai`. Behaviors: default (no cache, all methods), `/static/*` (cached), `/doh-ph/*` (PostHog API proxy), `/doh-ph-static/*` (PostHog assets proxy). Route53 A records point to distribution
- **RedirectStack** — Redirects `devopshero.co` → `devopshero.ai`

### Docker Container

The production container (Python 3.14-slim) includes: Node.js 20 (for CDK/jsii), AWS CLI v2 (ARM64), SSM Session Manager Plugin (for remote builder SSH), git, rsync. Runs as non-root `appuser` (UID 1000, matching EFS access point).

---

## 5. AI Agent System

### Architecture

The agent is built on the **Claude Agent SDK** with a custom MCP (Model Context Protocol) server that exposes DevOps Hero's platform tools. The agent runs as a persistent background process per conversation, decoupled from HTTP request/response cycles.

### Components

- **`agent_service.py`** — High-level orchestration. Creates conversations, builds mode-specific system prompts (enriched with current AWS infrastructure state from the database), manages the MainAgent lifecycle
- **`agent_runner.py`** — Background agent manager. One `AgentRunner` per conversation stored in a global dict. Uses asyncio tasks and an event queue so clients can disconnect/reconnect without losing agent work. Implements double-checked locking to prevent duplicate runners
- **`agent_client.py`** — Configures the Claude Agent SDK. Selects backend (direct Anthropic API or AWS Bedrock). Configures sandbox settings, tool permissions, sub-agents, and MCP server
- **`mcp_tools.py`** — FastMCP server exposing 16 domain-specific tools to the agent
- **`sandbox.py`** — Per-conversation filesystem isolation. Each conversation gets `{CLAUDE_SANDBOX_DIR}/conv-{id}/src/` (cloned repo) and `conv-{id}/tmp/` (temp files)

### Conversation Modes

Each mode gets a tailored system prompt and model selection:

- **GENERAL** — Open-ended DevOps assistance. Context: workspace, AWS infrastructure
- **ENVIRONMENT_SETUP** — Guides user through provisioning a new environment (VPC + ECS cluster). Context: AWS account, existing environments
- **APP_DEPLOYMENT** — Deploys an application to an environment. Context: workspace, repository, AWS infrastructure

### Streaming

The chat view uses **Server-Sent Events (SSE)** at `/chat/<id>/stream/`. Event types: `thinking`, `start`, `text_delta`, `text_flush`, `tool_start`, `tool_result`, `complete`, `error`, `sse-close`. Keepalive pings every 15 seconds prevent CloudFront timeout.

### Session Persistence

Claude Agent SDK sessions are stored as JSONL files on EFS at `/home/appuser/.claude/projects/.../{session_id}.jsonl`. Sessions survive container replacements. Conversations can be **forked** by copying the source session file to a new conversation's sandbox.

### MCP Tools (16 total)

**AWS Account Management:**
- `initiate_aws_connection` — Creates pending AWSAccount, returns CloudFormation quick-create URL for the customer to deploy
- `list_aws_accounts` — Returns connected AWS accounts with status

**Environment Management:**
- `list_environments` — Lists environments in an AWS account with status
- `provision_environment` — Queues environment provisioning (VPC + ECS cluster + optional HTTPS). Returns immediately; agent polls status
- `get_environment_status` — Polls provisioning progress with recent logs
- `list_hosted_zones` — Lists Route53 domains for HTTPS configuration

**Application Deployment:**
- `list_apps` — Lists apps in workspace with active deployments
- `deploy_app` — Unified create-or-update deployment. Upsert semantics: matches by (organization, slug). Configures container port, CPU/memory, health checks, environment variables, secrets, database binding, subdomain. Handles subdomain conflict resolution. Returns deployment ID for polling
- `get_deployment_status` — Polls build/deploy progress with recent logs
- `teardown_deployment` — Destroys deployment infrastructure (ECS service, ECR repo, optional Aurora database)
- `test_docker_build` — Test-builds Dockerfile in sandbox without pushing to ECR

**Repository Operations:**
- `list_repositories` — Lists GitHub repositories synced to the organization
- `scan_repository` — Pattern-based analysis: detects language, framework, Dockerfile, port, health check path, database dependencies, .env variables
- `git_ops` — Full git operations using GitHub App credentials: status, diff, log, create_branch, stage_files, commit, push_branch, create_pull_request, update_pull_request, get_pull_request, get_remote_info

**Database Management:**
- `create_datastore` — Creates Aurora MySQL/PostgreSQL database (serverless v2 or provisioned)

**Utility:**
- `wait` — Sleep (debugging tool)

### Sub-Agents

- **`analyze-repository`** — Spawned by the main agent for deep LLM-powered repository analysis (beyond pattern matching)

### Usage Tracking

Every agent turn logs to `LLMUsageLog`: organization, user, conversation, model alias/ID, input/output tokens, cost (USD), duration, number of turns. Title generation is logged separately.

---

## 6. Deployment Pipeline

### Job Worker

A polling-based background job system (`job_worker.py`) runs inside the app container (when `DOH_RUN_JOB_WORKER=1`). Polls every 1 second. Claims jobs atomically using `select_for_update(skip_locked=True)`. Each job runs in its own daemon thread. Handles 4 job types: app deployment, environment provisioning, app teardown, environment teardown.

### App Deployment Flow

1. Agent calls `deploy_app` tool → creates App (or updates existing) + Deployment record with status PENDING
2. Job worker claims the deployment, sets status to BUILDING
3. Repository cloned to `{CLAUDE_SANDBOX_DIR}/deployment-{id}`
4. AWS session obtained via cross-account IAM role assumption (STS AssumeRole with external ID)
5. AppConfig built from Django models (ECR repo name, container config, database config, secrets)
6. CDK stacks synthesized and deployed: EcrStack → AuroraClusterStack (optional) → AppStack
7. Docker image built (locally in dev, on EC2 builder instance in prod) and pushed to ECR
8. ECS service started (desiredCount=1), monitored for stability (rolloutState=COMPLETED)
9. Service URL populated from CloudFormation outputs
10. Previous DEPLOYED deployments marked SUPERSEDED
11. Status set to DEPLOYED

### Environment Provisioning Flow

1. Agent calls `provision_environment` → creates Environment record with PENDING status
2. Job worker claims, sets status to PROVISIONING
3. CDK deploys: VpcStack (auto-selected /20 CIDR in 172.x range, 2 AZs, NAT gateway) → EcsClusterStack (Fargate cluster, shared ALB, optional HTTPS with wildcard cert) → BuilderStack (optional EC2 m8g.large for remote Docker builds)
4. CloudFormation outputs synced: VPC ID, cluster ARN
5. Status set to READY

### Teardown Flows

**App teardown:** Deletes CloudFormation app stack (ECS service, ALB rules, ECR repo, optional Aurora cluster). Status → TORN_DOWN.

**Environment teardown:** Sequentially tears down all deployments in the environment first (fail-fast on first error), then deletes cluster stack, builder stack, and VPC stack. Environment record deleted from database on success.

### Deployment Logging

Thread-local context + custom logging handlers capture logs from deployment/infrastructure modules and persist them to DeploymentLog or EnvironmentLog models. Each handler only processes logs matching its thread-local context, enabling concurrent job execution with isolated logging.

---

## 7. Customer AWS Infrastructure

When DevOps Hero deploys to a customer's AWS account, it provisions:

### Base Layer (per environment)

- **VPC:** /20 CIDR block (auto-selected to avoid overlaps), 2 AZs, public subnets (ALB), private subnets (Fargate tasks, databases), 1 NAT Gateway
- **ECS Cluster:** Fargate-only, Container Insights v2 enabled
- **Shared ALB:** HTTP listener (always), HTTPS listener (if hosted zone configured with wildcard cert)
- **Builder Instance** (optional, production only): EC2 m8g.large ARM64, private subnet, SSM access, auto-stop after 15min idle. Matches Fargate ARM64 target architecture

### App Layer (per deployment)

- **ECR Repository:** `doh/{env_slug}/{app_slug}`, lifecycle policy keeps last 10 images
- **ECS Service:** Fargate task with configurable CPU/memory, health checks, CloudWatch logging
- **Per-App Task Role:** Scoped to app's own secrets in Secrets Manager
- **ALB Listener Rule:** Host-based routing (`{subdomain}.{hosted_zone}`)
- **Route53 A Record:** Alias to ALB
- **Secrets Manager:** App secrets stored as JSON, injected as container environment variables
- **Aurora Cluster** (optional): MySQL or PostgreSQL, serverless v2 or provisioned, connection string injected via Secrets Manager

### Cross-Account Security

- Customer deploys a CloudFormation stack that creates an IAM role `devopshero-{external_id}` with a trust policy for DOH's AWS account (555553041615)
- External ID prevents confused deputy attacks
- DOH assumes the role via STS with 1-hour temporary credentials
- Lambda callback from CloudFormation notifies DOH backend with the role ARN

---

## 8. Web Application Features

### Authentication and Multi-Tenancy

- **WorkOS OAuth** for login. New users redirected to onboarding to create their organization
- **Organization switching** via POST endpoint. User's `current_organization` field determines tenant context
- **Role-based membership:** admin, member, viewer per organization
- **`@login_required`** on all protected routes

### HTMX SPA Architecture

All authenticated pages use an **app shell pattern**: a static outer frame (sidebar navigation, profile menu) wraps a `#main-content` div that swaps content via HTMX. Views detect `request.htmx` to serve either a partial (HTML fragment) or a full page (with shell wrapper). Client-side navigation highlighting updates via JS listeners on `htmx:pushedIntoHistory`.

### Pages and Routes (43 total)

**Public:**
- Landing page (`/`) with hero, problem statement, solution, features, personas, CTA sections
- Waitlist signup (`/waitlist/signup/`)
- Health check (`/health/`) for ALB probes

**Authentication:**
- Login (`/auth/login/`), callback (`/auth/callback/`), logout (`/auth/logout/`)
- Onboarding (`/onboarding/`) — create organization + user setup

**Main Navigation:**
- Dashboard (`/dashboard/`) — lists apps and datastores
- Workspaces (`/workspaces/`, `/workspaces/<slug>/`) — list and detail with apps, datastores, conversations
- Environments (`/environments/`, `/environments/<slug>/`) — list and detail with deployments
- Security (`/security/`)

**App Management:**
- App detail (`/apps/<slug>/`) — configuration and deployment history
- Deployment status polling (`/apps/<slug>/deployments/<id>/status/`) — returns updated HTML row
- Teardown confirmation modal and execution

**Settings (tabbed):**
- Organization, Members, AWS Accounts (add/list), Billing, Git Integrations (connect/sync)

**Chat (AI Agent):**
- Conversation list and creation (`/chat/`, `/chat/new/`)
- Conversation view (`/chat/<id>/`)
- Send message (`/chat/<id>/send/`)
- SSE stream (`/chat/<id>/stream/`)
- Message history (`/chat/<id>/messages/`)
- Close conversation (`/chat/<id>/close/`)
- Fork conversation (`/chat/<id>/fork/`)
- Quick-deploy shortcuts (`/chat/app_deploy/<workspace>/<repo>/`)

**Integrations:**
- GitHub App OAuth flow (`/github/connect`, `/github/callback`)
- GitHub webhook receiver (`/api/github/webhook`) — CSRF exempt, HMAC-SHA256 signature verified
- AWS account callback (`/api/aws/install-account-callback`) — Bearer token auth

### Chat UI

The chat interface displays multiple message types: text, markdown, code blocks, tool call executions (with parameters and results), deployment logs, progress indicators, error messages, and choice prompts. Streaming events render in real-time with a thinking indicator during agent processing.

---

## 9. GitHub Integration

- **GitHub App** installation-based auth (not personal access tokens)
- **Webhook events:** `push`, `installation`, `installation_repositories` — auto-syncs repository list
- **Repository operations** use GitHub App installation tokens, managed by DOH (never expose user credentials)
- **Git operations** available to the agent: clone, branch, stage, commit, push, create/update PRs

---

## 10. Management Commands

- `run_job_worker` — Start the background job worker
- `doh_control` — Administrative control commands
- `doh_query` — Database query commands
- `doh_raw` — Raw/debug commands
- `ensure_superuser` — Create superuser (used in migration init container)
- `seed_local_repos` — Seed database with local test repositories

---

## 11. Coding Conventions

These are enforced project-wide and documented in `AGENTS.md`:

- **No default parameter values** — all function parameters are mandatory
- **Keyword arguments** at call sites for literals and multi-param calls
- **Module-qualified imports** for local modules (`import module` then `module.function()`)
- **Direct imports** for type hints (no `TYPE_CHECKING` unless circular)
- **Single-line signatures** when under 140 characters
- **Single-line docstrings** for simple functions
- **`logger.error()`** instead of `logger.warning()` (no warning level used)
- **Descriptive file names** (not generic `service.py`, `client.py`)
- **Django 6.0 async ORM** (`await queryset.afirst()`, `await instance.asave()`) — no `sync_to_async` wrappers
- **Django 6.0 template partials** (`{% partialdef %}` / `{% partial %}`) preferred over `{% include %}`
- **`uv run`** for all Python commands

---

## 12. What Is NOT Yet Implemented

Based on the design document (`docs/deployment_agent_design.md`) and codebase analysis, these planned features are not yet built:

- **Deployment plan UI** — Structured plan generation with cost estimates, rendered for user review and approval before execution
- **CDK code generation** — Agent-generated CDK Python code (currently uses predefined CDK constructs, not dynamically generated code)
- **Nixpacks / Buildpack build strategies** — Only Dockerfile is operational
- **Enterprise approval workflows** — Multi-approver chains, audit trails
- **Power user CDK export** — Exporting generated infrastructure code to user's repo
- **Worker and scheduled app types** — Model supports them, but deployment logic is web-only
- **Rollback mechanics** — Not explicitly designed
- **Cost estimation** — Planned as part of deployment plan
