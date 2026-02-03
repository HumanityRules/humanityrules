<beads>

This project uses [Beads (bd)](https://github.com/steveyegge/beads) for issue tracking.

## Core Rules

- Use `bd ready` to find available work
- Use `bd create` to track new issues/tasks/bugs
- Use `bd sync` at end of session to sync with git remote
- Git hooks auto-sync on commit/merge

## Quick Reference

```bash
bd prime                              # Load complete workflow context
bd ready                              # Show issues ready to work (no blockers)
bd list --status=open                 # List all open issues
bd create --title="..." --type=task  # Create new issue
bd update <id> --status=in_progress  # Claim work
bd close <id>                         # Mark complete
bd dep add <issue> <depends-on>       # Add dependency (issue depends on depends-on)
bd sync                               # Sync with git remote
```

## Workflow

1. Check for ready work: `bd ready`
2. Claim an issue: `bd update <id> --status=in_progress`
3. Do the work
4. Mark complete: `bd close <id>`
5. Sync: `bd sync` (or let git hooks handle it)

## Task Categories

Prefix beads task titles with a category to make the domain clear at a glance. Format: `Category: Description`

- **Onboarding** — User registration, organization creation, session handling
- **AgentChat** — AI conversation interface, streaming, message rendering, tool calls
- **Integrations** — External services: AWS accounts, GitHub, WorkOS
- **Deployment** — Everything in customer accounts: environments, apps, datastores, VPCs, ECS, ALB
- **DomainModel** — Core entities (workspaces, apps, datastores, environments), CRUD, relationships, refactors
- **UI** — Shared frontend: base templates, navigation, design system, shared components
- **ControlPlane** — DOH's own infrastructure (EFS, Aurora, CloudFront, ALB)
- **DevEx** — Developer tooling, scripts, CLI, local development
- **Bugfix** — Bug fixes and debugging sessions

**Examples:**
- `AgentChat: Fix markdown underscore rendering`
- `Deployment: ECS health check timeout tuning`
- `ControlPlane: Add EFS for session persistence`

## Task Completion

After closing each beads task, immediately commit the related changes:

1. `bd close <id>` - Close the task
2. `git add <files>` - Stage related changes
3. `git commit -m "<id>: <description>"` - Commit with task ID

This ensures atomic, traceable commits linked to specific work items.

## Commit Policy

- **Beads tasks** — Commit after completing each task (tasks flow sequentially from the queue)

</beads>