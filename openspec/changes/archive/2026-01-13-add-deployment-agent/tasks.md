# Implementation Tasks

## Phase 1: Foundation

- [x] 1.1 Add Django models: Workspace, App, Datastore, Deployment, DeploymentLog
- [x] 1.2 Add Django models: Conversation, Message (title field is placeholder - no auto-generation in v1)
- [x] 1.3 Create database migrations for all new models
- [x] 1.4 Create basic chat view with HTMX message sending
- [x] 1.5 Implement SSE endpoint for message streaming
- [x] 1.6 Create message partials for all content types (text, markdown, code, progress, choice, deployment_log, error)

## Phase 2: Agent Core

- [x] 2.1 Integrate Claude Agents SDK as dependency
- [x] 2.2 Implement agent service with conversation context management
- [x] 2.3 Implement `inspect_repository` tool (file:// URLs only in v1)
- [x] 2.4 Implement `list_aws_accounts` tool
- [x] 2.5 Implement `ask_user` tool for interactive prompts
- [x] 2.6 Connect agent to chat view with typing indicator
- [x] 2.7 Implement streaming responses from agent to chat

## Phase 3: Deployment Flow

- [x] 3.1 Implement `create_workspace` tool
- [x] 3.2 Implement `create_app` tool
- [x] 3.3 Implement `create_datastore` tool
- [x] 3.4 Implement `deploy_app` tool (stubbed - creates records, simulates progress, no real infrastructure)
- [x] 3.5 Implement `get_deployment_status` tool
- [x] 3.6 Build progress indicator rendering with phase tracking

## Phase 4: Polish

- [~] 4.1 Add error handling and agent recovery suggestions — **Deferred**: Basic error handling exists; advanced recovery suggestions moved to future work
- [~] 4.2 Implement conversation history view and resume functionality — **Deferred**: Conversation persistence works; history UI deferred
- [~] 4.3 Wire up interactive choice buttons to agent — **Deferred**: Visuals exist (1.6), agent integration moved to separate proposal
