# DevOps Hero - Current State

> **Purpose:** This document provides a comprehensive overview of the DevOps Hero project for agents or developers who don't have direct access to the codebase. It covers what has been built, what's planned, architectural decisions, and coding paradigms.
>
> **Last Updated:** 2026-01-13

---

## Executive Summary

DevOps Hero (DOH) is an AI-powered platform for deploying internal tools to company VPCs. The core loop is: user describes what they want to deploy → AI agent analyzes their repo → agent provisions infrastructure → user gets a URL.

**Current State:** The foundation is solid (auth, orgs, AWS account connection, AI chat interface with streaming). The deployment pipeline creates database records and simulates progress, but does NOT trigger real infrastructure yet. The CDK/CloudFormation code for real deployments exists and has been manually tested, but is not wired to the agent.

**Key Gap:** Connecting the agent tools to the actual CDK deployment engine is the main remaining work to close the MVP loop.

---

## Vision & Value Proposition

### The Problem
While AI has made building apps faster than ever, deploying them internally remains a bottleneck:
- IAM permissions require DevOps expertise
- SSO integration is complex
- VPC networking is mystical to most developers
- Compliance checks add weeks to timelines

### The Solution
An AI "co-pilot" that automates deployment and governance:
- **Automated Infrastructure:** Deploys to company VPCs without manual Terraform/YAML
- **AI-Assisted Security:** Configures IAM and approval chains
- **Built-in SDK:** SSO, RBAC, logging/metrics out of the box
- **Governance:** Approval flows for sensitive changes

### Target Persona
Data Scientists, ML Engineers, Full Stack developers who can build apps but struggle with deployment infrastructure. They represent:
- Maximum pain (deployment is mystical)
- Growing market (vibe coding trend)
- Clear success metric ("I have a URL")

---

## Architecture Overview

### Tech Stack

**Backend:**
- Django 6.0 (Python 3.14+) with native async ORM
- SQLite (dev) / Aurora MySQL (prod)
- WorkOS for SSO/SAML authentication
- Claude Agent SDK for AI chat

**Frontend:**
- HTMX for SPA-like navigation
- Tailwind CSS + Tailwind Plus Elements
- streaming-markdown library for real-time rendering

**Infrastructure as Code:**
- AWS CDK (Python) - primary deployment engine
- CloudFormation templates for customer account setup
- boto3 for secrets management and AWS operations

### File Structure

```
/devopshero/
├── devopshero_app/           # Main Django app
│   ├── models.py             # Core data models (19KB)
│   ├── views/                # HTMX views (14 modules)
│   ├── services/agent/       # AI agent (Claude SDK + MCP tools)
│   │   ├── agent_service.py  # Streaming orchestration
│   │   ├── agent_client.py   # Claude client init
│   │   ├── mcp_tools.py      # MCP server setup
│   │   ├── system_prompt.md  # Agent personality
│   │   └── tools/            # 9 tool implementations
│   ├── templates/            # Django templates
│   └── migrations/           # Database migrations
│
├── devopshero_site/          # Django project settings
│   ├── settings.py           # Config (WorkOS, Claude, etc.)
│   └── urls.py               # Root URL routing
│
├── infra_customer/           # CDK for customer deployments
│   ├── deploy.py             # CLI entry point
│   ├── deploy_base.py        # VPC + ECS cluster stacks
│   ├── deploy_app.py         # App stacks (ECR, ALB, ECS, Aurora)
│   ├── appconfig.py          # App configuration dataclass
│   ├── secrets_utils.py      # Secrets Manager helpers
│   ├── ecr_utils.py          # Docker build/push
│   ├── ecs_utils.py          # ECS monitoring
│   └── cdk.out/              # Generated CF templates
│
├── infra_devopshero/         # DOH platform infrastructure
│   ├── cf_install_template.json    # Customer CloudFormation
│   ├── install_callback_lambda.py  # Lambda for account verification
│   └── cf_*.json             # S3 bucket configs
│
├── deployable_repos/         # Test applications
│   ├── simple_dashboard/     # Streamlit MVP (Track 1)
│   └── db_portal/            # Phoenix/Elixir app (Track 2)
│
├── docs/
│   ├── journal.md            # Development journal (1600+ lines)
│   └── bolt_prompt.md        # Original product concept
│
├── openspec/                 # Specifications
│   ├── project.md            # Security constraints
│   ├── specs/                # Finalized capabilities
│   └── changes/              # Pending proposals
│
└── CLAUDE.md                 # Coding style guide + project context
```

### Core Data Models

**Organization Layer:**
- `User` - Custom user model with WorkOS integration, UUID primary key
- `Organization` - Top-level tenant
- `OrganizationMembership` - RBAC roles (admin/member/viewer)
- `AWSAccount` - Connected AWS accounts (PENDING/CONNECTED/ERROR status)

**Deployment Layer:**
- `Workspace` - Groups apps, links to AWS account + region
- `App` - Compute workload config (build strategy, container settings, env vars)
- `Datastore` - Aurora database config (serverless v2 / provisioned)
- `Deployment` - Binds app to environment, tracks deployment phases
- `DeploymentLog` - Audit trail with phases and status

**Conversation Layer:**
- `Conversation` - Chat session with the AI agent
- `Message` - Individual messages (TEXT, MARKDOWN, CODE, TOOL_CALL, ERROR, etc.)
- `ToolCall` - Records agent tool invocations

---

## Implementation Status

### Fully Functional

**1. Authentication & Onboarding**
- WorkOS integration for SSO/OAuth2
- First-time onboarding creates Organization + User + Membership
- Multi-organization support with switching
- Session-based login/logout

**2. AWS Account Connection**
- CloudFormation wizard generates secure installation URLs
- Lambda callback verifies stack deployment
- External ID validation prevents confused deputy attacks
- Status transitions: PENDING → CONNECTED

**3. AI Chat System**
- Claude Agent SDK with dual backend (Anthropic API or AWS Bedrock)
- Real-time SSE streaming with proper message ordering
- Markdown rendering during streaming
- Tool call visualization (parameters, results, duration)
- Conversation persistence across sessions
- Up-arrow history recall in chat input

**4. Repository Analysis**
- Framework detection (Flask, Django, FastAPI, Next.js, Phoenix, etc.)
- Language detection (Python, JavaScript, Go, Rust, Elixir, Ruby, Java)
- Dockerfile detection with port/health check extraction
- Environment variable detection from .env.example
- Database dependency detection

**5. HTMX Navigation**
- SPA-like navigation with `#main-content` swaps
- Browser history support (back/forward)
- Client-side nav highlighting
- Nested tabs (Settings subsections)

### Stubbed / Records-Only

**1. Deployment Pipeline (THE KEY GAP)**
- `deploy_app()` creates Deployment records but does NOT trigger real infrastructure
- Simulates progress through phases (init, build, push, synth, deploy, health, complete)
- Status stays PENDING unless manually advanced
- Comments indicate "v1 stubbed", "v2 will connect to infra_customer/"

**2. Datastore Provisioning**
- Creates Datastore model records
- No actual Aurora cluster creation
- Cluster ARN/endpoint fields remain empty

**3. UI Pages**
- Apps, Workspaces, Datastores pages render minimal content
- Dashboard shows context but limited functionality
- No forms for app/datastore creation in UI (only via agent chat)

### Infrastructure (Tested, Not Wired)

The following infrastructure code exists and has been manually tested but is NOT connected to the agent:

**VPC + ECS Cluster (`deploy_base.py`):**
- VPC with NAT Gateway (private subnets for security)
- Auto-CIDR selection (172.16-31.x.x, conflict detection)
- ECS cluster with Container Insights

**Application Deployment (`deploy_app.py`):**
- ECR repository per app
- ALB with HTTPS (ACM + Route53)
- ECS Fargate service with rolling deployments
- Aurora Serverless v2 (MySQL/PostgreSQL)
- Per-app task role isolation (each app can only read its own secrets)

**Manual Deployment Commands:**
```bash
# Base layer
uv run python infra_customer/deploy.py --base

# App deployment
uv run python infra_customer/deploy.py --app simple-dashboard
uv run python infra_customer/deploy.py --app db-portal --image-tag v1.2.3
```

---

## Key Paradigms & Patterns

### Python Style Guide (from CLAUDE.md)

**1. No Default Parameters**
```python
# Bad
def connect(url, retries=3): ...

# Good
def connect(url, retries): ...
```

**2. Keyword Arguments for Calls**
```python
# Bad
connect_to_db("localhost", 3, 30)

# Good
connect_to_db(url="localhost", retries=3, timeout=30)
```

**3. Module-Qualified Imports for Local Modules**
```python
# Bad
from iam_utils import get_assumed_role_session

# Good
import iam_utils
session = iam_utils.get_assumed_role_session(...)
```

**4. Django 6.0 Async ORM**
```python
# Bad (outdated)
await sync_to_async(User.objects.get)(id=user_id)

# Good (native async)
await User.objects.aget(id=user_id)
await instance.asave()
async for obj in queryset:
```

**5. Single-Line Signatures Under 140 Chars**
```python
# Good
async def handle_event(message: SDKEvent, ctx: Context) -> AsyncGenerator[Event, None]:
```

**6. Descriptive File Names**
```
# Bad: service.py, client.py, list.html
# Good: agent_service.py, agent_client.py, chat_list.html
```

### HTMX Navigation Pattern

Views handle both HTMX requests (return partial) and full page loads (return app_shell):

```python
@login_required
def dashboard(request):
    context = get_app_shell_context(current_page="dashboard")

    if request.htmx:
        return render(request, "devopshero_app/dashboard.html", context)

    context["content_url"] = "/dashboard/"
    return render(request, "devopshero_app/app_shell.html", context)
```

### Agent Streaming Architecture

The SSE endpoint runs the agent directly and yields events:

- **`start`** — Begin new message container
- **`text_delta`** — Chunk of text to append
- **`text_flush`** — Finalize text before tool execution
- **`thinking`** — Show "Thinking..." indicator
- **`tool_start`** — Tool execution beginning
- **`tool_result`** — Tool completed with result
- **`complete`** — Agent finished responding
- **`error`** — Something went wrong

### Security Constraints (from openspec/project.md)

- External ID required for all cross-account AssumeRole
- Per-app secret isolation (task roles scoped to own secrets)
- Secrets never in CloudFormation templates
- Private subnets for all workloads

---

## Current Task Pipeline

### Open Issues (from beads)

**Priority 1:**
- `devopshero-bzm` — Onboarding: Form validation and session expiration handling

**Priority 2:**
- `devopshero-twi` — AWSAccounts: Core features (verify job, email, create/delete flows)
- `devopshero-7wa` — CustomerInstall: Least privilege role and resource tagging
- `devopshero-1q8` — Onboarding: Technical debt (slug race condition, tests)
- `devopshero-3hx` — Onboarding: Invitation and WorkOS sync flows
- `devopshero-4mr` — Onboarding: UX improvements (loading states, slug preview)

**Priority 3:**
- `devopshero-f10` — AWSAccounts: AWS Organizations support
- `devopshero-ava` — Onboarding: Growth features (analytics, welcome email, tour)
- `devopshero-k4r` — AgentChat: Create proposal for interactive choice system
- `devopshero-mlb` — AgentChat: Fix markdown underscore rendering

### Completed Work (Recent)

- Chat input history (up arrow recall)
- Streaming markdown rendering
- Message ordering fixes
- Tool call rendering with full details
- Thinking indicator unification
- Agent spec finalization

---

## Strategic Considerations

### The MVP Gap

The core value proposition requires:
1. User describes what to deploy ✅
2. Agent analyzes repo ✅
3. Agent creates workspace/app/datastore records ✅
4. **Agent triggers real CDK deployment** ❌ (stubbed)
5. User gets working URL ❌ (blocked by #4)

**Closing this gap is THE critical path to MVP.**

### Technical Debt

**IAM Permissions:** Customer role currently has `AdministratorAccess`. Need to reduce to least-privilege once capabilities are finalized.

**CDK Bootstrap:** Customers need CDK bootstrap in their account. Options:
1. Run `cdk bootstrap` programmatically after they connect
2. Include bootstrap in customer CloudFormation
3. Two-stack customer setup

**Repository Access:** `inspect_repository` only works with `file://` URLs. Need git clone/download for real repos.

### Feature Roadmap (from docs/journal.md)

Track 2 milestones toward deploying enterprise apps:
- **M1** ✅ Basic deploy (Streamlit)
- **M2** ✅ Environment variables
- **M3** ✅ Secrets injection
- **M4** ✅ Managed Aurora
- **M5** ✅ Custom domain + TLS
- **M6** Pending: SSO integration (Okta SAML)
- **M7** Pending: Private VPC networking (current architecture supports this)
- **M8** Pending: Background workers

---

## File Reference Guide

### Entry Points

- **`manage.py`** — Django CLI
- **`infra_customer/deploy.py`** — CDK deployment CLI
- **`Procfile.tailwind`** — Honcho processes (Django + Tailwind)

### Key Configuration

- **`devopshero_site/settings.py`** — Django settings, WorkOS, Claude config
- **`CLAUDE.md`** — Coding style guide (symlinked as AGENTS.md)
- **`.env`** — Environment variables (WorkOS, AWS, Anthropic keys)

### Agent System

- **`services/agent/agent_service.py`** — Main orchestration, streaming
- **`services/agent/agent_client.py`** — Claude client initialization
- **`services/agent/mcp_tools.py`** — MCP server and tool registration
- **`services/agent/system_prompt.md`** — Agent personality and guidelines
- **`services/agent/tools/`** — Individual tool implementations (9 tools)

### Infrastructure

- **`infra_customer/deploy_base.py`** — VPC + ECS cluster CDK stacks
- **`infra_customer/deploy_app.py`** — App CDK stacks (ECR, ALB, ECS, Aurora)
- **`infra_customer/appconfig.py`** — AppConfig dataclass
- **`infra_devopshero/cf_install_template.json`** — Customer CloudFormation

### Documentation

- **`docs/journal.md`** — Development journal (1600+ lines of decisions)
- **`openspec/specs/`** — Finalized capability specifications
- **`devopshero_app/views/AGENTS.md`** — HTMX navigation patterns

---

## Quick Start for New Contributors

1. **Understand the domain:** Read `docs/bolt_prompt.md` for product vision
2. **Review architecture:** Skim this document and `CLAUDE.md`
3. **Check journal:** `docs/journal.md` explains why decisions were made
4. **Find work:** `bd ready` shows available tasks
5. **Follow patterns:** HTMX views, async ORM, keyword arguments

### Development Commands

```bash
# Start dev server
honcho start -f Procfile.tailwind

# Run Django commands
uv run python manage.py migrate
uv run python manage.py createsuperuser

# Deploy infrastructure (manual testing)
uv run python infra_customer/deploy.py --base
uv run python infra_customer/deploy.py --app simple-dashboard

# Beads task management
bd ready          # Find available work
bd show <id>      # View task details
bd update <id> --status=in_progress
bd close <id>
bd sync           # Push to git
```

---

## Summary

DevOps Hero is well-architected with solid foundations in auth, organization management, AWS account connection, and AI chat infrastructure. The agent can analyze repos and create deployment records, but the critical gap is wiring the agent tools to the CDK deployment engine that already exists and has been tested.

The codebase follows consistent patterns (async Django 6.0, HTMX, explicit keyword arguments) and has thorough documentation in the development journal. The beads task system tracks remaining work, with onboarding polish and AWS account features as current priorities.

**The path to MVP:** Connect `deploy_app()` tool to `infra_customer/deploy.py` and let the agent trigger real deployments.
