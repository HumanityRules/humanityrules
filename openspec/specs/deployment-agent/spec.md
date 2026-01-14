# deployment-agent Specification

## Purpose
TBD - created by archiving change add-deployment-agent. Update Purpose after archive.
## Requirements
### Requirement: Conversation Creation and Management

The system SHALL provide conversation management for user-agent interactions.

#### Scenario: Create new conversation
- **WHEN** user clicks "New Deployment" on the dashboard
- **THEN** a new Conversation is created with the user's organization
- **AND** the user is redirected to the chat interface

#### Scenario: Resume existing conversation
- **WHEN** user navigates to an existing conversation
- **THEN** the full message history is displayed
- **AND** the user can continue the conversation

#### Scenario: Conversation persistence
- **WHEN** a conversation is created
- **THEN** it persists across browser sessions
- **AND** it is accessible from the conversation history

#### Scenario: Conversation belongs to organization
- **WHEN** a conversation is created
- **THEN** it is scoped to the user's current organization
- **AND** users cannot access conversations from other organizations

---

### Requirement: Repository Inspection Tool

The system SHALL provide an `inspect_repository` tool that analyzes repositories via URL.

#### Scenario: Analyze repository contents
- **WHEN** the agent calls `inspect_repository` with a file:// URL
- **THEN** the tool reads the repository from the filesystem
- **AND** the tool detects framework (flask, django, fastapi, nextjs, etc.)
- **AND** the tool detects language (python, javascript, go, etc.)
- **AND** the tool identifies if a Dockerfile is present

#### Scenario: Detect container configuration
- **WHEN** a Dockerfile is present
- **THEN** the tool extracts the exposed port
- **AND** the tool suggests a health check path

#### Scenario: Detect environment requirements
- **WHEN** the repository is analyzed
- **THEN** the tool identifies required environment variables
- **AND** the tool detects database dependencies (postgres, mysql, etc.)

#### Scenario: URL validation
- **WHEN** an unsupported URL scheme is provided (e.g., https://, git://)
- **THEN** the tool returns an error indicating only file:// URLs are supported in v1

#### Scenario: Invalid path
- **WHEN** a file:// URL points to a non-existent path
- **THEN** the tool returns an error with a clear message

---

### Requirement: Repository Discovery Tool

The system SHALL provide a `list_deployable_repos` tool for discovering available repositories.

#### Scenario: List available repositories
- **WHEN** the agent calls `list_deployable_repos`
- **THEN** the tool returns repository names and file:// URLs
- **AND** the URLs can be passed to `inspect_repository`

---

### Requirement: AWS Account Discovery Tool

The system SHALL provide a `list_aws_accounts` tool for discovering available AWS accounts.

#### Scenario: List connected accounts
- **WHEN** the agent calls `list_aws_accounts`
- **THEN** the tool returns all AWS accounts connected to the user's organization
- **AND** each account includes ID, name, status, and region

---

### Requirement: Workspace Creation Tool

The system SHALL provide a `create_workspace` tool for organizing deployments.

#### Scenario: Create workspace
- **WHEN** the agent calls `create_workspace` with name, AWS account, and region
- **THEN** a Workspace is created in the user's organization
- **AND** the workspace is linked to the specified AWS account

#### Scenario: Workspace validation
- **WHEN** creating a workspace
- **THEN** the AWS account must belong to the user's organization
- **AND** the workspace name must be unique within the organization

---

### Requirement: App Creation Tool

The system SHALL provide a `create_app` tool for configuring applications.

#### Scenario: Create app configuration
- **WHEN** the agent calls `create_app` with workspace, repo URL, and build settings
- **THEN** an App is created within the specified workspace
- **AND** the app captures container configuration (port, CPU, memory, health check)

#### Scenario: App with environment variables
- **WHEN** creating an app with environment variables
- **THEN** the environment variables are stored with the app
- **AND** they are injected into the container at runtime

#### Scenario: App with database binding
- **WHEN** creating an app with a datastore reference
- **THEN** the app is linked to the specified datastore
- **AND** database connection details are injected at deployment

---

### Requirement: Datastore Creation Tool

The system SHALL provide a `create_datastore` tool for provisioning managed databases.

#### Scenario: Create Aurora Serverless database
- **WHEN** the agent calls `create_datastore` with PostgreSQL or MySQL engine
- **THEN** a Datastore is created in the specified workspace
- **AND** the configuration uses Aurora Serverless v2 by default

#### Scenario: Configure ACU scaling
- **WHEN** creating a datastore with min and max ACU
- **THEN** the Aurora cluster scales between those bounds
- **AND** the default range is 0.5 to 2.0 ACU

---

### Requirement: Deployment Execution Tool

The system SHALL provide a `deploy_app` tool that creates deployment records (stubbed in v1).

#### Scenario: Trigger deployment
- **WHEN** the agent calls `deploy_app` with an app ID
- **THEN** a Deployment record is created with status "pending"
- **AND** the deployment simulates progress through phases (no real infrastructure in v1)

#### Scenario: Deployment phases (simulated)
- **WHEN** a deployment executes
- **THEN** it progresses through simulated phases: init, build, push, synth, deploy, health, complete
- **AND** each phase transition updates the Deployment status

#### Scenario: Link deployment to conversation
- **WHEN** a deployment is triggered from a conversation
- **THEN** the Deployment is linked to that Conversation
- **AND** simulated deployment progress appears in the chat

---

### Requirement: Deployment Status Tracking

The system SHALL provide a `get_deployment_status` tool for monitoring deployments.

#### Scenario: Query deployment status
- **WHEN** the agent calls `get_deployment_status` with a deployment ID
- **THEN** the current status and phase are returned
- **AND** recent log entries are included
- **AND** the service URL is included if deployment succeeded

#### Scenario: Progress percentage
- **WHEN** querying deployment status
- **THEN** a progress percentage is calculated from the current phase
- **AND** the percentage reflects overall deployment progress

---

### Requirement: Message Streaming

The system SHALL stream messages to the chat interface in real-time.

#### Scenario: SSE connection
- **WHEN** user opens a conversation
- **THEN** an SSE connection is established to the stream endpoint
- **AND** new messages are pushed as they are created

#### Scenario: Agent response streaming
- **WHEN** the agent generates a response
- **THEN** the response streams to the chat as it is produced
- **AND** a typing indicator is shown while the agent is processing

#### Scenario: Deployment progress streaming
- **WHEN** a deployment is in progress
- **THEN** simulated progress updates stream to the chat
- **AND** progress is rendered with appropriate phase styling

---

### Requirement: Chat Interface Rendering

The system SHALL render messages with appropriate formatting based on content type.

#### Scenario: Render markdown content
- **WHEN** a message has content_type "markdown"
- **THEN** it is rendered with GitHub-flavored markdown support
- **AND** code blocks have syntax highlighting

#### Scenario: Render progress indicator
- **WHEN** a message has content_type "progress"
- **THEN** it displays a progress bar with percentage
- **AND** it shows the current phase label

#### Scenario: Render interactive choices (visual only)
- **WHEN** a message has content_type "choice"
- **THEN** buttons are rendered for each choice
- **NOTE** Full agent integration for choice responses is planned for a future iteration

#### Scenario: Render error messages
- **WHEN** a message has content_type "error"
- **THEN** it is rendered with error styling
- **AND** suggested actions are highlighted if provided

#### Scenario: Render tool call
- **WHEN** a message has content_type "tool_call"
- **THEN** it displays the tool name, parameters, and result
- **AND** execution status and duration are shown

---

### Requirement: Agent Tool Permissions

The system SHALL enforce per-user permissions on agent tool operations.

#### Scenario: Tools operate as requesting user
- **WHEN** an agent tool creates or modifies resources
- **THEN** the operation uses the permissions of the requesting user
- **AND** the created_by field references the requesting user

#### Scenario: Organization boundary enforcement
- **WHEN** an agent tool accesses workspaces, apps, or AWS accounts
- **THEN** only resources in the user's current organization are accessible
- **AND** cross-organization access is denied

#### Scenario: AWS account association
- **WHEN** creating a workspace with an AWS account
- **THEN** the account must belong to the user's organization
- **AND** the workspace records the target AWS account for future deployment

---

### Requirement: Secret Handling

The system SHALL protect sensitive data from exposure in conversations.

#### Scenario: Database credentials never displayed
- **WHEN** a datastore is created
- **THEN** database credentials are stored only in Secrets Manager
- **AND** credentials are never shown in the chat interface

#### Scenario: Repository access
- **WHEN** repository inspection completes
- **THEN** only the analysis results are stored
- **AND** no repository file contents are persisted in the database

#### Scenario: Environment variable masking
- **WHEN** displaying app configuration in chat
- **THEN** values of secret environment variables are masked
- **AND** only variable names are shown

### Requirement: Repository Analysis Sub-Agent

The system SHALL provide repository analysis via an LLM sub-agent that examines repositories and produces structured findings.

This sub-agent is a new capability and does not replace the existing `inspect_repository` tool.

#### Scenario: Sub-agent receives local file URL only
- **WHEN** repository analysis is triggered
- **THEN** the sub-agent receives only a file:// URL pointing to a local directory
- **AND** the sub-agent inspects the repository at that path (no cloning needed)

#### Scenario: Sub-agent uses a different tool set than the main agent
- **WHEN** the sub-agent analyzes a repository
- **THEN** it is configured with a tool allow-list appropriate for repository inspection
- **AND** the allow-list includes Bash for script execution and Read/LS/Glob/Grep for file inspection
- **AND** it does not rely on the main agent's MCP tool allow-list

#### Scenario: Sub-agent is isolated from Django models and streaming
- **WHEN** the sub-agent runs
- **THEN** it does not access Django models
- **AND** it does not perform chat streaming or message persistence
- **AND** it returns results directly to the caller (test harness or main agent)

#### Scenario: Sub-agent is invoked via SDK native sub-agent mechanism
- **WHEN** repository analysis is executed
- **THEN** the caller invokes the sub-agent via the Claude Agent SDK's native sub-agent support (agents + Task tool)
- **AND** the sub-agent produces the final structured JSON result

#### Scenario: Detect framework and language
- **WHEN** the sub-agent analyzes a repository
- **THEN** it detects the primary language (python, node, elixir, go, etc.)
- **AND** it detects the framework (django, fastapi, nextjs, phoenix, etc.)
- **AND** each claim includes evidence referencing repository paths and excerpts

#### Scenario: Detect service configuration
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies the service type (web, worker, scheduled_task)
- **AND** it determines the run command if it can be inferred from evidence
- **AND** it detects the port and health check path (for web services) if it can be inferred from evidence
- **AND** unknown or ambiguous fields are returned as null with caveats

#### Scenario: Detect infrastructure dependencies
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies required datastores (postgres, redis, mysql, etc.) when evidenced
- **AND** it identifies AWS service dependencies (s3, sqs, dynamodb, etc.) when evidenced

#### Scenario: Extract environment variables
- **WHEN** the sub-agent analyzes a repository
- **THEN** it identifies environment variables from .env.example or config patterns
- **AND** it separates required from optional when it can be inferred from evidence
- **AND** it documents the purpose of each variable when it can be inferred from evidence

#### Scenario: Produce structured output with evidence
- **WHEN** analysis completes
- **THEN** the sub-agent returns JSON with description, language, framework, service config, dependencies, env vars, caveats, and evidence
- **AND** evidence is included for major claims (framework, entrypoint/run command, port/health path, dependencies)

#### Scenario: User reviews findings via conversation loop
- **WHEN** analysis findings are presented to the user
- **THEN** the user can accept implicitly by proceeding, or provide corrections in natural language
- **AND** if corrections are provided, the main agent re-spawns the sub-agent with the new instructions

#### Scenario: One repo = one app
- **WHEN** the sub-agent analyzes a repository
- **THEN** it assumes exactly one deployable application per repository
- **AND** monorepo detection and selection is not supported

