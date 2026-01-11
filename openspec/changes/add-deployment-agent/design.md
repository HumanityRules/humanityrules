# OpenSpec: DevOps Hero Agent

## Metadata
- Spec ID: DOH-AGENT-001
- Status: Draft
- Owner: DevOps Hero
- Last updated: 2026-01-08

## Summary

This specification defines the AI-powered deployment agent that sits at the center of DevOps Hero. The agent conducts intelligent conversations with users to understand their deployment needs, makes infrastructure decisions, and orchestrates deployments to their AWS accounts. Built on the Claude Agents SDK, it provides a beautiful, dynamic chat experience using HTMX.

## Current State

- A working CLI-based deployment engine exists in `infra_customer/` (~2500 lines)
- The deployer supports VPC, ECS Fargate, Aurora databases, ALB, HTTPS, Route53
- Django models exist for `User`, `Organization`, `OrganizationMembership`, `AWSAccount`
- The UI shell exists with HTMX navigation but all feature pages are stubs
- No agent implementation exists
- No integration between UI and deployment engine

## Goals

- Create an AI agent that guides users through the deployment process via natural conversation
- Integrate with Claude Agents SDK for robust agent orchestration
- Provide a beautiful, real-time chat interface using HTMX
- Build the data model and agent tools for deployment workflows
- Support progressive disclosure: simple deployments are simple, complex configs are available when needed

## Non-Goals

- Replacing all UI with chat (settings, billing, team management remain traditional UI)
- Supporting non-AWS cloud providers in v1
- Multi-agent collaboration in v1
- Voice interface
- Mobile experience (desktop-first in v1)
- **Connecting to existing `infra_customer/` deployment engine in v1** (tools create records but don't trigger real infrastructure)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser                                     │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                     Chat Interface (HTMX)                          │ │
│  │  ┌──────────────────────────────────────────────────────────────┐  │ │
│  │  │ Agent: What would you like to deploy today?                  │  │ │
│  │  │ User: I have a Python Flask app at deployable_repos/myapp    │  │ │
│  │  │ Agent: I found a Flask app with requirements.txt...          │  │ │
│  │  │ [Progress: Deploying to us-east-1...]                        │  │ │
│  │  └──────────────────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTMX (SSE / Polling)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Django Backend                                   │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────┐  │
│  │   Chat Views    │───▶│  Agent Service  │    │  Deployment Engine  │  │
│  │   (HTMX)        │    │  (Claude SDK)   │    │  (future v2)        │  │
│  └─────────────────┘    └─────────────────┘    └─────────────────────┘  │
│           │                     │                        │              │
│           ▼                     ▼                        ▼              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      Django Models                               │   │
│  │  Conversation │ Workspace │ App │ Deployment │ DeploymentLog    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         External Services                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │  Claude API  │  │    GitHub    │  │  Customer    │                   │
│  │  (Anthropic) │  │   (git ops)  │  │  AWS Account │                   │
│  └──────────────┘  └──────────────┘  └──────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Functional Requirements

### FR-1: Conversation Management
- FR-1.1: Users can start new conversations from the dashboard
- FR-1.2: Conversations persist across sessions and can be resumed
- FR-1.3: Users can view conversation history organized by workspace

### FR-2: Agent Capabilities
- FR-2.1: Agent can inspect local repositories to detect app type and configuration
- FR-2.2: Agent can ask clarifying questions when information is ambiguous
- FR-2.3: Agent can propose infrastructure configurations based on app analysis
- FR-2.4: Agent can create deployment records (stubbed in v1, no real infrastructure)
- FR-2.5: Agent can stream simulated deployment progress back to the user

### FR-3: Chat Interface
- FR-3.1: Messages appear in real-time using HTMX streaming
- FR-3.2: Agent can render rich content: code blocks, tables, progress indicators
- FR-3.3: Agent can present interactive choices (buttons, selects) inline
- FR-3.4: User can type messages or click suggested actions
- FR-3.5: Interface shows typing indicators during agent processing

### FR-4: Workspace and App Creation
- FR-4.1: Agent can create workspaces through conversation
- FR-4.2: Agent can add apps to existing workspaces
- FR-4.3: Agent validates AWS account connectivity before deployment
- FR-4.4: Agent guides users to connect AWS account if none available

### FR-5: Deployment Execution (stubbed in v1)
- FR-5.1: Agent converts conversation context into App record
- FR-5.2: Agent creates Deployment record and simulates progress
- FR-5.3: Simulated progress updates stream to chat
- FR-5.4: Agent summarizes deployment outcome with actionable next steps

## Data Models

### Conversation

```python
class Conversation(models.Model):
    """A conversation between a user and the agent."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    id: UUIDField (primary key, uuid7)
    user: ForeignKey(User)
    organization: ForeignKey(Organization)
    workspace: ForeignKey(Workspace, nullable)  # Set once workspace is determined
    status: CharField(choices=Status)
    title: CharField  # Auto-generated from first message or agent summary
    created_at: DateTimeField
    updated_at: DateTimeField
```

### Message

```python
class Message(models.Model):
    """A single message in a conversation."""

    class Role(models.TextChoices):
        USER = "user", "User"
        AGENT = "agent", "Agent"
        SYSTEM = "system", "System"  # For deployment logs, errors

    class ContentType(models.TextChoices):
        TEXT = "text", "Text"
        MARKDOWN = "markdown", "Markdown"
        CODE = "code", "Code Block"
        PROGRESS = "progress", "Progress Indicator"
        CHOICE = "choice", "Interactive Choice"
        DEPLOYMENT_LOG = "deployment_log", "Deployment Log"
        ERROR = "error", "Error"

    id: UUIDField (primary key, uuid7)
    conversation: ForeignKey(Conversation)
    role: CharField(choices=Role)
    content_type: CharField(choices=ContentType)
    content: TextField  # JSON for structured types, plain text for text/markdown
    metadata: JSONField  # Extra data: code language, choice options, log level, etc.
    created_at: DateTimeField
```

### Workspace

```python
class Workspace(models.Model):
    """A workspace containing apps and configuration."""

    id: UUIDField (primary key, uuid7)
    organization: ForeignKey(Organization)
    name: CharField
    slug: SlugField (unique per organization)
    description: TextField
    primary_repo_url: URLField (nullable)  # Only file:// URLs in v1
    aws_account: ForeignKey(AWSAccount)  # Which AWS account to deploy to
    aws_region: CharField  # Target region for deployments
    created_by: ForeignKey(User)
    created_at: DateTimeField
    updated_at: DateTimeField

    class Meta:
        unique_together = [["organization", "slug"]]
```

### App

```python
class App(models.Model):
    """An application within a workspace."""

    class AppType(models.TextChoices):
        WEB = "web", "Web Service"
        WORKER = "worker", "Background Worker"
        SCHEDULED = "scheduled", "Scheduled Job"

    class BuildStrategy(models.TextChoices):
        DOCKERFILE = "dockerfile", "Dockerfile"
        NIXPACKS = "nixpacks", "Nixpacks (auto-detect)"
        BUILDPACK = "buildpack", "Cloud Native Buildpack"

    id: UUIDField (primary key, uuid7)
    workspace: ForeignKey(Workspace)
    name: CharField
    slug: SlugField (unique per workspace)
    app_type: CharField(choices=AppType)
    build_strategy: CharField(choices=BuildStrategy)

    # Source
    repo_url: URLField  # Only file:// URLs supported in v1; git URLs in future
    branch: CharField (default="main")
    dockerfile_path: CharField (default="Dockerfile", nullable)

    # Container configuration
    container_port: IntegerField
    cpu: IntegerField  # Fargate CPU units
    memory: IntegerField  # Fargate memory MiB
    health_check_path: CharField
    health_check_command: CharField (nullable)

    # Environment
    environment_variables: JSONField  # List of {name, value}

    # Domain
    domain_name: CharField (nullable)

    # Database binding
    datastore: ForeignKey(Datastore, nullable)

    created_by: ForeignKey(User)
    created_at: DateTimeField
    updated_at: DateTimeField

    class Meta:
        unique_together = [["workspace", "slug"]]
```

### Datastore

```python
class Datastore(models.Model):
    """A managed database within a workspace."""

    class Engine(models.TextChoices):
        AURORA_MYSQL = "aurora-mysql", "Aurora MySQL"
        AURORA_POSTGRESQL = "aurora-postgresql", "Aurora PostgreSQL"

    class DeploymentMode(models.TextChoices):
        SERVERLESS_V2 = "aurora_serverless_v2", "Aurora Serverless v2"
        PROVISIONED = "aurora_provisioned", "Aurora Provisioned"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CREATING = "creating", "Creating"
        AVAILABLE = "available", "Available"
        ERROR = "error", "Error"
        DELETING = "deleting", "Deleting"

    id: UUIDField (primary key, uuid7)
    workspace: ForeignKey(Workspace)
    name: CharField
    slug: SlugField (unique per workspace)

    # Engine configuration
    engine: CharField(choices=Engine)
    engine_version: CharField (nullable, uses default if null)

    # Deployment mode
    deployment_mode: CharField(choices=DeploymentMode)
    serverless_min_acu: FloatField (nullable)
    serverless_max_acu: FloatField (nullable)
    provisioned_instance_class: CharField (nullable)

    # Database
    database_name: CharField

    # Security
    storage_encrypted: BooleanField (default=True)
    deletion_protection: BooleanField (default=False)
    backup_retention_days: IntegerField (default=7)

    # Status
    status: CharField(choices=Status)
    status_message: TextField

    # AWS resources (populated after creation)
    cluster_arn: CharField (nullable)
    cluster_endpoint: CharField (nullable)
    credentials_secret_arn: CharField (nullable)
    connection_secret_arn: CharField (nullable)

    created_by: ForeignKey(User)
    created_at: DateTimeField
    updated_at: DateTimeField

    class Meta:
        unique_together = [["workspace", "slug"]]
```

### Deployment

```python
class Deployment(models.Model):
    """A deployment of an app to infrastructure."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        BUILDING = "building", "Building Image"
        PUSHING = "pushing", "Pushing to ECR"
        DEPLOYING = "deploying", "Deploying Infrastructure"
        STARTING = "starting", "Starting Service"
        RUNNING = "running", "Running"
        FAILED = "failed", "Failed"
        ROLLED_BACK = "rolled_back", "Rolled Back"

    id: UUIDField (primary key, uuid7)
    app: ForeignKey(App)
    conversation: ForeignKey(Conversation, nullable)  # Which conversation triggered this

    # Source
    git_ref: CharField  # Branch, tag, or commit SHA
    git_commit_sha: CharField  # Resolved commit SHA
    git_commit_message: CharField

    # Image
    image_tag: CharField
    image_uri: CharField (nullable)  # Set after push

    # Status
    status: CharField(choices=Status)
    status_message: TextField
    started_at: DateTimeField (nullable)
    completed_at: DateTimeField (nullable)

    # Outputs
    service_url: URLField (nullable)
    alb_dns: CharField (nullable)

    created_by: ForeignKey(User)
    created_at: DateTimeField
    updated_at: DateTimeField
```

### DeploymentLog

```python
class DeploymentLog(models.Model):
    """Log entries from a deployment."""

    class Level(models.TextChoices):
        DEBUG = "debug", "Debug"
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    class Phase(models.TextChoices):
        INIT = "init", "Initialization"
        BUILD = "build", "Docker Build"
        PUSH = "push", "ECR Push"
        SYNTH = "synth", "CDK Synthesis"
        DEPLOY = "deploy", "CDK Deploy"
        HEALTH = "health", "Health Check"
        COMPLETE = "complete", "Completion"

    id: UUIDField (primary key, uuid7)
    deployment: ForeignKey(Deployment)
    phase: CharField(choices=Phase)
    level: CharField(choices=Level)
    message: TextField
    details: JSONField (nullable)  # Structured data: stack name, resource ARN, etc.
    created_at: DateTimeField
```

## Agent Design

### Data Types

```python
@dataclass
class RepositoryAnalysis:
    """Result of analyzing a repository."""
    detected_framework: str | None  # flask, django, fastapi, nextjs, etc.
    detected_language: str | None  # python, javascript, go, etc.
    has_dockerfile: bool
    dockerfile_path: str | None
    has_requirements: bool
    has_package_json: bool
    suggested_port: int | None
    suggested_health_path: str | None
    environment_variables: list[str]  # Required env vars detected
    detected_database: str | None  # postgres, mysql, etc.
```

### Claude Agents SDK Integration

The agent is built using the Claude Agents SDK, which provides:
- Structured tool calling
- Conversation memory management
- Error handling and recovery
- Streaming responses

### Agent Tools

The agent has access to the following tools:

#### `inspect_repository`
Analyzes a local repository to detect app characteristics.

```python
@tool
def inspect_repository(repo_url: str, branch: str) -> RepositoryAnalysis:
    """
    Analyze a repository's contents.

    Args:
        repo_url: Repository URL. Only file:// URLs supported in v1.
                  Example: file:///app/deployable_repos/flask-app
                  Test repositories available in deployable_repos/
        branch: Branch to analyze.

    Returns:
        RepositoryAnalysis (see Data Types section)

    Note: Only file:// URLs supported in v1. Git URLs (https://, git://) out of scope.
    """
```

#### `create_workspace`
Creates a new workspace in the organization.

```python
@tool
def create_workspace(
    name: str,
    aws_account_id: str,
    aws_region: str,
    description: str,
    primary_repo_url: str | None,  # Only file:// URLs in v1
) -> Workspace:
    """Create a new workspace for deploying applications."""
```

#### `create_app`
Creates an app configuration within a workspace.

```python
@tool
def create_app(
    workspace_id: str,
    name: str,
    repo_url: str,  # Only file:// URLs in v1
    branch: str,
    app_type: str,
    build_strategy: str,
    container_port: int,
    cpu: int,
    memory: int,
    health_check_path: str,
    environment_variables: list[dict],
    domain_name: str | None,
) -> App:
    """Create an app configuration in a workspace."""
```

#### `create_datastore`
Creates a managed database in a workspace.

```python
@tool
def create_datastore(
    workspace_id: str,
    name: str,
    engine: str,
    database_name: str,
    deployment_mode: str,
    serverless_min_acu: float,
    serverless_max_acu: float,
) -> Datastore:
    """Create a managed database in the workspace."""
```

#### `deploy_app`
Creates a deployment record (stubbed in v1, does not trigger real infrastructure).

```python
@tool
def deploy_app(
    app_id: str,
    git_ref: str,
) -> Deployment:
    """
    Create a deployment record for an app.

    In v1 (stubbed):
    - Creates Deployment record with status "pending"
    - Simulates progress for demo purposes
    - Does NOT trigger real infrastructure

    Future v2 will trigger:
    1. Docker image build
    2. ECR push
    3. CDK infrastructure deployment
    4. ECS service update

    Returns a Deployment object with status tracking.
    """
```

#### `get_deployment_status`
Gets current status and logs of a deployment.

```python
@tool
def get_deployment_status(deployment_id: str) -> DeploymentStatus:
    """
    Get current deployment status and recent logs.

    Returns:
        DeploymentStatus containing:
        - status: str
        - phase: str
        - progress_percentage: int
        - recent_logs: list[str]
        - service_url: str | None
    """
```

#### `list_aws_accounts`
Lists connected AWS accounts for the organization.

```python
@tool
def list_aws_accounts() -> list[AWSAccountSummary]:
    """List AWS accounts connected to the organization."""
```

#### `ask_user`
Presents a question to the user with optional choices.

```python
@tool
def ask_user(
    question: str,
    choices: list[str] | None,
    allow_free_text: bool,
) -> str:
    """
    Ask the user a question and wait for their response.

    If choices are provided, render as buttons.
    If allow_free_text is True, also show text input.
    """
```

### Agent System Prompt

The system prompt is loaded from disk (`devopshero_app/services/agent/system_prompt.md`) to allow easy iteration during development.

```
You are the DevOps Hero deployment assistant. Your role is to help users deploy
applications to their AWS infrastructure with minimal friction.

## Your Personality
- Friendly but efficient - respect the user's time
- Confident in your recommendations but open to user preferences
- Proactive about potential issues (security, cost, reliability)
- Celebrate successes warmly

## Your Capabilities
- Analyze code repositories to understand application structure
- Recommend infrastructure configurations based on app requirements
- Create workspaces, apps, and databases
- Execute and monitor deployments
- Troubleshoot failed deployments

## Guidelines

### For New Users
1. Greet them and ask what they'd like to deploy
2. If they provide a repository URL, analyze it immediately
3. Present your findings and recommendations concisely
4. Ask only necessary questions - use sensible defaults
5. Confirm before deploying

### For Repository Analysis
When you detect:
- **Python + Flask/Django/FastAPI**: Recommend Nixpacks or Dockerfile
- **Node.js + Next.js/Express**: Recommend Nixpacks
- **Dockerfile present**: Use it, analyze for port/health check
- **Database imports**: Suggest adding a datastore

### For Infrastructure Decisions
- **CPU/Memory**: Start small (256 CPU, 512 MB) unless app indicates otherwise
- **Database**: Aurora Serverless v2 with 0.5-2 ACU for most cases
- **Region**: Use workspace default, confirm if deploying to new region

### For Deployments
- Stream progress updates to keep users informed
- If deployment fails, analyze logs and suggest fixes
- After success, provide the URL and suggest next steps

### Question Philosophy
Ask questions when:
- Multiple valid options exist and user preference matters
- Security implications require explicit consent
- Cost differences are significant

Don't ask when:
- Sensible defaults exist
- You can detect the answer from the repository
- The question is too technical for the user's apparent skill level
```

## Chat Interface Specification

### Visual Design

The chat interface occupies the main content area and consists of:

```
┌─────────────────────────────────────────────────────────────┐
│ ┌─────────────────────────────────────────────────────────┐ │
│ │  Conversation Title                           [Actions] │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │                                                         │ │
│ │  ┌─────────────────────────────────────────────────┐   │ │
│ │  │ 🤖 Agent message with markdown support          │   │ │
│ │  └─────────────────────────────────────────────────┘   │ │
│ │                                                         │ │
│ │         ┌─────────────────────────────────────────┐    │ │
│ │         │                        User message  👤 │    │ │
│ │         └─────────────────────────────────────────┘    │ │
│ │                                                         │ │
│ │  ┌─────────────────────────────────────────────────┐   │ │
│ │  │ 🤖 I found a Flask app! Here's what I detect:  │   │ │
│ │  │                                                 │   │ │
│ │  │ • Framework: Flask                              │   │ │
│ │  │ • Python: 3.11                                  │   │ │
│ │  │ • Port: 5000                                    │   │ │
│ │  │                                                 │   │ │
│ │  │ ┌──────────┐ ┌──────────────┐ ┌────────────┐   │   │ │
│ │  │ │ Deploy   │ │ Add Database │ │ Customize  │   │   │ │
│ │  │ └──────────┘ └──────────────┘ └────────────┘   │   │ │
│ │  └─────────────────────────────────────────────────┘   │ │
│ │                                                         │ │
│ │  ┌─────────────────────────────────────────────────┐   │ │
│ │  │ ⏳ Deploying...                                 │   │ │
│ │  │ ████████████░░░░░░░░ 60%                        │   │ │
│ │  │ Building Docker image...                        │   │ │
│ │  └─────────────────────────────────────────────────┘   │ │
│ │                                                         │ │
│ └─────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ [Message input                                    ] [➤] │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Message Types and Rendering

#### Text Message (role=agent, content_type=text)
Simple text, rendered as-is with proper typography.

#### Markdown Message (role=agent, content_type=markdown)
Full GitHub-flavored markdown support including:
- Headers, bold, italic
- Code blocks with syntax highlighting
- Lists (ordered and unordered)
- Tables
- Links

#### Code Block (role=agent, content_type=code)
```json
{
  "content": "def hello():\n    return 'world'",
  "metadata": {
    "language": "python",
    "filename": "app.py"
  }
}
```
Rendered with syntax highlighting, copy button, and optional filename header.

#### Progress Indicator (role=system, content_type=progress)
```json
{
  "content": "Deploying infrastructure...",
  "metadata": {
    "phase": "deploy",
    "percentage": 60,
    "started_at": "2026-01-08T10:00:00Z"
  }
}
```
Rendered as animated progress bar with phase label and elapsed time.

#### Interactive Choice (role=agent, content_type=choice)
```json
{
  "content": "How would you like to proceed?",
  "metadata": {
    "choices": [
      {"id": "deploy", "label": "Deploy Now", "primary": true},
      {"id": "database", "label": "Add Database"},
      {"id": "customize", "label": "Customize Settings"}
    ],
    "allow_text": true
  }
}
```
Rendered as buttons below the message. Clicking a button sends a message on behalf of the user.

#### Deployment Log (role=system, content_type=deployment_log)
```json
{
  "content": "Successfully created ECS service",
  "metadata": {
    "level": "info",
    "phase": "deploy",
    "deployment_id": "uuid",
    "resource": "devopshero-myapp-service"
  }
}
```
Rendered in a collapsible log section with level-appropriate styling (green for info, yellow for warning, red for error).

#### Error (role=system, content_type=error)
```json
{
  "content": "Deployment failed: Docker build error",
  "metadata": {
    "error_code": "BUILD_FAILED",
    "details": "Exit code 1 at step 'pip install'",
    "recoverable": true,
    "suggested_action": "Check your requirements.txt for invalid packages"
  }
}
```
Rendered with red styling, error details in collapsible section, and suggested action highlighted.

### HTMX Implementation

#### Message Streaming

New messages stream in via Server-Sent Events (SSE):

```html
<div id="messages"
     hx-ext="sse"
     sse-connect="/chat/{{ conversation.id }}/stream/"
     sse-swap="message">
  <!-- Messages rendered here -->
</div>
```

Each SSE event contains a rendered message partial:

```python
# views/chat.py
def message_stream(request, conversation_id):
    def event_generator():
        for message in get_new_messages(conversation_id, last_id):
            html = render_to_string("partials/_message.html", {"message": message})
            yield f"event: message\ndata: {html}\n\n"

    return StreamingHttpResponse(
        event_generator(),
        content_type="text/event-stream"
    )
```

#### Sending Messages

```html
<form hx-post="/chat/{{ conversation.id }}/send/"
      hx-target="#messages"
      hx-swap="beforeend"
      hx-on::after-request="this.reset()">
  <input type="text" name="message" placeholder="Type a message..."
         class="flex-1 rounded-lg border-gray-300 dark:border-gray-600
                dark:bg-gray-800 focus:ring-indigo-500">
  <button type="submit"
          class="ml-2 px-4 py-2 bg-indigo-600 text-white rounded-lg
                 hover:bg-indigo-500">
    Send
  </button>
</form>
```

#### Choice Buttons

```html
<div class="flex flex-wrap gap-2 mt-3">
  {% for choice in message.metadata.choices %}
  <button hx-post="/chat/{{ conversation.id }}/send/"
          hx-vals='{"message": "{{ choice.label }}", "choice_id": "{{ choice.id }}"}'
          hx-target="#messages"
          hx-swap="beforeend"
          class="px-4 py-2 rounded-lg
                 {% if choice.primary %}bg-indigo-600 text-white{% else %}bg-gray-200 dark:bg-gray-700{% endif %}">
    {{ choice.label }}
  </button>
  {% endfor %}
</div>
```

#### Typing Indicator

```html
<div id="typing-indicator"
     class="hidden"
     hx-trigger="agent-thinking from:body"
     hx-swap="outerHTML">
  <div class="flex items-center space-x-2 text-gray-500">
    <div class="flex space-x-1">
      <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce"></div>
      <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0.1s"></div>
      <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0.2s"></div>
    </div>
    <span>Agent is thinking...</span>
  </div>
</div>
```

## User Flows

### Flow 1: First Deployment (Happy Path)

```
1. User clicks "New Deployment" on dashboard
   → Creates new Conversation
   → Redirects to /chat/{conversation_id}/

2. Agent: "Hi! I'm here to help you deploy. What would you like to deploy today?
           You can provide a path to your application or describe it."

3. User: "file:///app/deployable_repos/flask-app"

4. Agent: [Calls inspect_repository tool]
   "I found a Flask application! Here's what I detected:

   • Framework: Flask 2.3
   • Python: 3.11 (from Dockerfile)
   • Port: 5000
   • Health check: /health

   I'll deploy this with:
   • 0.25 vCPU, 512 MB memory
   • HTTPS enabled
   • Auto-scaling ready

   [Deploy Now] [Add Database] [Customize]"

5. User: clicks [Deploy Now]

6. Agent: [Calls list_aws_accounts]
   "Which AWS account should I deploy to?

   [Production (266117665083)] [Staging (123456789012)]"

7. User: clicks [Production]

8. Agent: [Calls create_workspace, create_app, deploy_app]
   "Starting deployment...

   ⏳ Building Docker image...
   ████████░░░░░░░░░░░░ 40%"

9. [Progress updates stream in via SSE]
   "✅ Docker image built
    ⏳ Pushing to ECR...
    ████████████░░░░░░░░ 60%"

10. [Deployment completes]
    Agent: "🎉 Deployed successfully!

    Your app is live at: https://flask-app.devopshero.app

    What's next?
    • [View Logs] - See application output
    • [Add Custom Domain] - Use your own domain
    • [Add Database] - Connect a database
    • [Deploy Another] - Start a new deployment"
```

### Flow 2: Deployment with Database

```
1-4. [Same as Flow 1]

5. User: clicks [Add Database]

6. Agent: "What type of database do you need?

   [PostgreSQL] [MySQL]"

7. User: clicks [PostgreSQL]

8. Agent: "I'll create an Aurora PostgreSQL Serverless v2 database.

   This will:
   • Scale from 0.5 to 2 ACUs (pay only for what you use)
   • Include automatic backups (7 days)
   • Inject DATABASE_URL into your app

   Database name: flask_app_prod

   [Create & Deploy] [Customize Database]"

9. User: clicks [Create & Deploy]

10. Agent: [Calls create_datastore, create_app, deploy_app]
    "Starting deployment with database...

    ⏳ Creating Aurora cluster (this takes 5-10 minutes)...
    ████░░░░░░░░░░░░░░░░ 20%"

11-12. [Similar to Flow 1, completes with database info]
```

### Flow 3: No AWS Account Connected

```
1-3. [Same as Flow 1]

4. Agent: [Calls list_aws_accounts, returns empty]
   "I'd love to deploy that for you, but I don't see any AWS accounts connected
   to your organization yet.

   To deploy, you'll need to connect an AWS account. This takes about 2 minutes.

   [Connect AWS Account] [Learn More]"

5. User: clicks [Connect AWS Account]

6. Agent: "I'll help you connect your AWS account.

   What would you like to name this account? (e.g., 'Production', 'Staging')

   [Production] [Staging] [Development] or type a custom name"

7. User: "Production"

8. Agent: [Creates AWSAccount record]
   "Great! Click the button below to open AWS CloudFormation.
   It will create a secure role that allows DevOps Hero to deploy to your account.

   [Open AWS Authorization Page]

   Once you've created the stack, come back here and I'll verify the connection."

9. [User completes CloudFormation in new tab, returns]

10. Agent: [Polls for status change]
    "✅ AWS account connected successfully!

    Now, let's deploy that Flask app. Which region should I use?

    [us-east-1 (N. Virginia)] [us-west-2 (Oregon)] [eu-west-1 (Ireland)]"

11. [Continues with normal deployment flow]
```

## API Endpoints

### Chat Endpoints

```
GET  /chat/                          # List conversations
POST /chat/new/                      # Create new conversation
GET  /chat/{id}/                     # View conversation (full page or HTMX partial)
POST /chat/{id}/send/                # Send message (HTMX)
GET  /chat/{id}/stream/              # SSE stream for new messages
GET  /chat/{id}/messages/            # Get messages (pagination, HTMX partial)
POST /chat/{id}/close/               # Mark conversation as completed
```

## Security Considerations

### Agent Tool Permissions
- Agent tools operate with the permissions of the requesting user
- Workspace/App creation respects organization membership
- Deployments verify AWS account belongs to user's organization
- No cross-organization data access

### Conversation Privacy
- Conversations belong to a user within an organization
- Organization admins can view conversations in their org (future)
- Conversation content is not used for model training

### Secret Handling
- Agent never logs or displays secrets
- Database credentials injected via Secrets Manager, never shown in chat
- Repository analysis clones to temporary directory, deleted after analysis

## Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Add Django models: Workspace, App, Datastore, Deployment, DeploymentLog, Conversation, Message
- [ ] Create basic chat view with HTMX message sending
- [ ] Implement SSE message streaming
- [ ] Create message partials for all content types

### Phase 2: Agent Core (Week 2)
- [ ] Integrate Claude Agents SDK
- [ ] Implement agent tools: inspect_repository, list_aws_accounts
- [ ] Connect agent to chat view
- [ ] Implement typing indicator and streaming responses

### Phase 3: Deployment Flow (Week 3)
- [ ] Implement create_workspace, create_app, create_datastore tools
- [ ] Implement deploy_app tool with background task
- [ ] Create deployment log streaming to chat
- [ ] Build progress indicator rendering

### Phase 4: Polish (Week 4)
- [ ] Error handling and recovery suggestions
- [ ] Conversation history and resume
- [ ] Interactive choice buttons

## Design Decisions

- Conversations do NOT auto-close after successful deployment (users can continue for follow-up, scaling, troubleshooting)
- One deployment at a time per conversation (no concurrent deployments)
- Deployments continue server-side regardless of client state; status is shown when user returns

## Appendix A: Message Partial Templates

See `templates/devopshero_app/chat/` for implementation.

## Appendix B: Agent Tool Schemas

See `devopshero_app/services/agent/tools/` for Claude Agents SDK tool definitions.
