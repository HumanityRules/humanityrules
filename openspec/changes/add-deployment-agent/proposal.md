# Change: Add AI Deployment Agent

## Why

DevOps Hero has a working deployment engine (`infra_customer/`) but no way for users to trigger deployments from the UI. Users need an intelligent interface that guides them through deployment decisions without requiring deep AWS or container knowledge. An AI-powered agent can analyze repositories, recommend configurations, and orchestrate deployments through natural conversation.

## What Changes

- Add Django models for conversations, messages, workspaces, apps, datastores, and deployments
- Integrate Claude Agents SDK for agent orchestration with structured tool calling
- Implement agent tools: `inspect_repository`, `create_workspace`, `create_app`, `create_datastore`, `deploy_app`, `get_deployment_status`, `list_aws_accounts`, `ask_user`
- Build HTMX-based chat interface with SSE streaming for real-time updates
- Connect agent to existing `infra_customer/` deployment engine
- Support multiple message types: text, markdown, code, progress indicators, interactive choices, deployment logs

## Impact

- **New specs**: `deployment-agent` (this proposal)
- **Affected specs**: None (new capability)
- **Affected code**:
  - `devopshero_app/models.py` - New models: Conversation, Message, Workspace, App, Datastore, Deployment, DeploymentLog
  - `devopshero_app/services/agent/` - New agent service with Claude SDK integration
  - `devopshero_app/services/agent/tools/` - Agent tool implementations
  - `devopshero_app/views/chat.py` - Chat views for HTMX
  - `devopshero_app/templates/devopshero_app/chat/` - Chat templates and partials
  - `devopshero_app/urls.py` - Chat and agent webhook endpoints
