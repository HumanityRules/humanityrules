# Change: Add AI Deployment Agent

## Why

DevOps Hero has a working deployment engine (`infra_customer/`) but no way for users to trigger deployments from the UI. Users need an intelligent interface that guides them through deployment decisions without requiring deep AWS or container knowledge. An AI-powered agent can analyze repositories, recommend configurations, and orchestrate deployments through natural conversation.

## What Changes

- Add Django models for conversations, messages, workspaces, apps, datastores, and deployments
- Integrate Claude Agents SDK for agent orchestration with structured tool calling
- Implement agent tools: `inspect_repository`, `list_deployable_repos`, `list_aws_accounts`, `create_workspace`, `create_app`, `create_datastore`, `deploy_app`, `get_deployment_status`
- **Note:** `ask_user` tool was planned but deferred to the interactive choice system proposal
- Build HTMX-based chat interface with SSE streaming for real-time updates
- Support multiple message types: text, markdown, code, progress indicators, interactive choices, deployment logs

**Out of Scope (v1):**
- Connecting agent to existing `infra_customer/` deployment engine (tools will create records but not trigger real infrastructure)
- Git URL support (cloning from GitHub, etc.) - only local `file://` URLs supported
- Mobile responsive chat UI

## Impact

- **New specs**: `deployment-agent` (this proposal)
- **Affected specs**: None (new capability)
- **Affected code**:
  - `devopshero_app/models.py` - New models: Conversation, Message, Workspace, App, Datastore, Deployment, DeploymentLog
  - `devopshero_app/services/agent/` - New agent service with Claude SDK integration
  - `devopshero_app/services/agent/tools/` - Agent tool implementations (stubbed, no real deployments)
  - `devopshero_app/views/chat.py` - Chat views for HTMX
  - `devopshero_app/templates/devopshero_app/chat/` - Chat templates and partials
  - `devopshero_app/urls.py` - Chat endpoints
