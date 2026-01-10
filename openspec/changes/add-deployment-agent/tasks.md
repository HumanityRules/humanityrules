# Implementation Tasks

## Phase 1: Foundation

- [ ] 1.1 Add Django models: Workspace, App, Datastore, Deployment, DeploymentLog
- [ ] 1.2 Add Django models: Conversation, Message
- [ ] 1.3 Create database migrations for all new models
- [ ] 1.4 Create basic chat view with HTMX message sending
- [ ] 1.5 Implement SSE endpoint for message streaming
- [ ] 1.6 Create message partials for all content types (text, markdown, code, progress, choice, deployment_log, error)

## Phase 2: Agent Core

- [ ] 2.1 Integrate Claude Agents SDK as dependency
- [ ] 2.2 Implement agent service with conversation context management
- [ ] 2.3 Implement `inspect_repository` tool (file:// URLs only in v1)
- [ ] 2.4 Implement `list_aws_accounts` tool
- [ ] 2.5 Implement `ask_user` tool for interactive prompts
- [ ] 2.6 Connect agent to chat view with typing indicator
- [ ] 2.7 Implement streaming responses from agent to chat

## Phase 3: Deployment Flow

- [ ] 3.1 Implement `create_workspace` tool
- [ ] 3.2 Implement `create_app` tool
- [ ] 3.3 Implement `create_datastore` tool
- [ ] 3.4 Implement `deploy_app` tool (stubbed - creates records, simulates progress, no real infrastructure)
- [ ] 3.5 Implement `get_deployment_status` tool
- [ ] 3.6 Build progress indicator rendering with phase tracking

## Phase 4: Polish

- [ ] 4.1 Add error handling and agent recovery suggestions
- [ ] 4.2 Implement conversation history view and resume functionality
- [ ] 4.3 Add interactive choice buttons with HTMX actions
- [ ] 4.4 Make chat UI mobile responsive
- [ ] 4.5 Write end-to-end tests for critical flows
