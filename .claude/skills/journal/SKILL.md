---
name: journal
description: Add development journal entries to docs/journal.md. Use when user says /journal or /journal commit to document session work, decisions, and learnings.
---

# Development Journaling

Add entries to `docs/journal.md` documenting decisions, learnings, and architectural choices from the current session.

## Workflow

1. Review the conversation to extract:
   - Decisions made and their reasoning
   - Learnings, mistakes, corrections
   - Architectural choices
   - Non-obvious implementation details

2. Select ONE category from:
   - **Onboarding** — User registration, organization creation, session handling
   - **AgentChat** — AI conversation interface, streaming, message rendering, tool calls
   - **Integrations** — External services: AWS accounts, GitHub, WorkOS
   - **Deployment** — Everything in customer accounts: environments, apps, datastores, VPCs, ECS, ALB
   - **DomainModel** — Core entities (workspaces, apps, datastores, environments), CRUD, relationships, refactors
   - **UI** — Shared frontend: base templates, navigation, design system, shared components
   - **ControlPlane** — DOH's own infrastructure (EFS, Aurora, CloudFront, ALB)
   - **DevEx** — Developer tooling, scripts, CLI, local development
   - **Bugfix** — Bug fixes and debugging sessions

3. Write a thorough entry following the format below

4. Prepend the entry to `docs/journal.md` (latest on top)

5. If user said `/journal commit`, also commit all changes from the session

## Entry Format

```markdown
## YYYY-MM-DD HH:MM - [Category] Title

Describe the decision/change and WHY in sufficient detail for future reference.

**Key points:**
- Point with reasoning
- Point with reasoning
- ...
```

## Writing Guidelines

- **Be thorough** — Include enough detail to understand the context months later
- **Explain decisions** — Document what was decided and WHY, not just what changed
- **Capture learnings** — Document mistakes, corrections, and insights
- **Include technical details** — Specific configurations, error messages, solutions tried
- **No "Files Changed" sections** — Git tracks files; journal captures intent
- **Use current time** — Format: `YYYY-MM-DD HH:MM` (24-hour, local time)

