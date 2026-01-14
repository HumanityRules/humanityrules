# Deployment Agent Design

> Design document for the AI-powered deployment agent that guides users through deploying applications to AWS.

---

## Implementation Steps

*Ordered roughly by implementation sequence.*

---

### 1. Reference Apps to Deploy

**What to build:** Test applications that exercise the deployment pipeline.

**Note:** Prefer reference apps that *do not* include a `Dockerfile` initially, so step #3 (Dockerfile Generation) is exercised in real usage. If an app includes a `Dockerfile`, it should be intentional (to test “use existing Dockerfile” behavior).

**Apps to create:**
- **Django with PostgreSQL** — primary test case, most common pattern for target users
- **FastAPI** — stateless, simpler, no database
- **Next.js** — Node ecosystem, tests JavaScript detection
- **Slack bot** — different workload type (worker, not web)
- **Simple Elixir/Phoenix app** — cleaner alternative to db_portal

**Additional enterprise reference apps to create:**
- **ML model serving API** — FastAPI/Flask inference endpoint; model pulled from S3
- **Background job processor** — SQS consumer worker; optional S3 read/write for artifacts
- **GraphQL API** — Apollo Server (Node) or Strawberry (Python) to test GraphQL detection
- **Internal admin dashboard** — common internal tool pattern; minimal admin + reporting view
- **Scheduled task runner** — EventBridge-triggered “cron” style task (run → finish → exit)
- **WebSocket/real-time app (DynamoDB-backed)** — store connection state/message history in DynamoDB
- **File processing service** — S3 upload/download + S3 event-triggered processing

**Already have:**
- `simple_dashboard` (Streamlit, no database)
- `db_portal` (Phoenix/Elixir with MySQL — messy, taken from friend's repo)

---

### 2. Repository Analysis (Sub-Agent)

**What to build:** LLM-powered repository analysis replacing procedural pattern matching.

**Architecture:**
- Main agent spawns analysis sub-agent
- Sub-agent receives: file list, key file contents (requirements.txt, Dockerfile, .env.example, package.json, etc.)

**Structured output:**
- `description` — human-readable summary of the application
- `framework`, `language`, `detected_services` — structured facts
- `caveats` — warnings, concerns, potential issues
- `questions_to_ask` — things needing user clarification before deployment

**Optionality:** Multiple specialized sub-agents (security analyzer, cost analyzer, compatibility checker). Future enhancement.

---

### 3. Dockerfile Generation

**What to build:** LLM-powered Dockerfile generation when none exists.

**Flow:**
1. If Dockerfile exists → use it
2. If no Dockerfile → LLM generates one based on detected framework/language

**Optionality:** Suggest improvements to existing Dockerfiles (outdated base image, missing best practices). Nice-to-have.

---

### 4. CDK Construct Library

**What to build:** Library of high-level, composable infrastructure primitives.

**Contents:**
- **Low-level primitives:** VPC, ECS cluster, Aurora, ALB, ECR
- **High-level compositions:** `DjangoWithAurora`, `StreamlitApp`, `FargateService`, `WorkerWithSQS`
- **Examples:** 100s of usage examples for agent context

**Purpose:** Provides vocabulary for agent, reduces hallucinations, encodes tested patterns.

---

### 5. Deployment Plan

**What to build:** Plan generation, rendering, and approval UI.

**Plan structure (JSON):**
- App metadata (name, source, framework, port)
- Infrastructure specs (VPC, ECS, Aurora, ALB configuration)
- Estimated monthly cost (min/max with breakdown)
- Caveats (from repo analysis)

**UI flow:**
1. Agent generates plan from repo analysis
2. Plan rendered as human-readable in chat
3. User can comment, ask questions, request changes
4. User approves → proceed to CDK generation

**Key distinction:** Plan is "what" (intent). CDK code is "how" (implementation).

---

### 6. CDK Code Generation

**What to build:** Agent-driven CDK Python code generation with validation.

**How it works:**
- Agent has construct library + examples in context
- Agent chooses abstraction level (constructs, raw CDK, or mix)
- Generated code is validated before execution

**Validation pipeline:**
1. Agent generates CDK code
2. Lint and syntax check
3. CDK synth (dry-run)
4. If errors → agent sees errors, troubleshoots, regenerates
5. If valid → shown to user for optional review
6. User confirms → execution begins

---

### 7. Deployment Execution & UI

**What to build:** Async deployment pipeline with dedicated UI components.

**Components:**
- **Log panel** — Separate from chat, streams real-time deployment events. Collapsible. Technical detail lives here.
- **Notification queue** — Toast notifications as milestones occur (VPC created, Aurora ready, health checks passing).
- **Chat panel** — Stays conversational, not cluttered with logs.

**Agent integration:**
- When user sends message during deployment, current log state injected into agent context
- Agent can answer questions about deployment progress
- On completion: agent gets full log, provides summary/next steps/error analysis

**Optionality:** Auto-inject log summaries for proactive agent commentary. Future enhancement.

---

### 8. Agent States & Dynamic System Prompt

**What to build:** State-aware system prompt that adapts to user context.

**States:**
- **No AWS Account** — Guide user to connect
- **Account Connected, No Deployments** — First deployment onboarding
- **Has Deployments** — Management mode (redeploy, scale, troubleshoot, teardown)

**Prompt includes:**
- Organization name
- Connected AWS accounts (name, region, status)
- Existing deployments (names, status, URLs)
- State-appropriate guidance and tone

**Optionality:** User persona detection/selection (data scientist vs. DevOps engineer). Future enhancement.

---

### 9. AWS Account Connection via Agent

**What to build:** Conversational flow for connecting AWS accounts.

**Flow:**
1. Agent detects no AWS account connected
2. Offers to guide through connection
3. Generates CloudFormation quick-create link
4. User deploys stack in AWS Console
5. Callback confirms connection
6. Agent acknowledges, ready to proceed

---

## Design Principles

*Philosophy and strategy guiding implementation decisions.*

---

### Infrastructure Isolation

Each deployment gets fresh, isolated infrastructure:
- New VPC (auto-selected CIDR)
- New ECS cluster
- New Aurora cluster (if needed)
- New ALB

**Rationale:** Simplifies v1. No "which cluster?" questions, no resource conflicts, clean teardown (delete VPC = delete everything).

**Future:** Sharing infrastructure between deployments (databases, VPC peering, private link to existing resources).

---

### User Confirmation Required

Nothing executes without explicit user approval.

- Deployment plan shown and approved before CDK generation
- CDK code available for review before execution
- User must confirm to start deployment

**Future:** Enterprise approval workflows (multiple approvers, audit trail).

---

### Simple Defaults, Optional Customization

**Domains:** Default to `{app-name}-{hash}.devopshero.io` (we control zone, zero setup). Custom domains via Route53 optional if user has hosted zones.

**Resources:** Sensible defaults for CPU, memory, database sizing. User can adjust in plan review.

**Build:** Always containerize. User never chooses Nixpacks vs Docker.

---

### Agent Abstraction Choice

Agent selects appropriate abstraction level for infrastructure code:
- High-level constructs only (simple cases)
- Raw CDK (edge cases, custom requirements)
- Mix of both (common)

Agent is guided by examples but has flexibility to compose solutions.

---

## Out of Scope

- **Power User Export** — CDK code export to user's GitHub repo
- **Shared Infrastructure** — Reusing VPC/ECS/databases across deployments
- **Environments** — dev/staging/prod separation
- **GitHub/GitLab Integration** — Parallel workstream; local file:// URLs for now
- **Rollback Mechanics** — Must work, but not explicitly designing
