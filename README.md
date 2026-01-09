# DevOps Hero

AI-powered deployment platform for internal tools. See `AGENTS.md` for full project documentation.

## Setup

```bash
# Clone and install
uv sync

# Copy environment variables
cp .env.example .env
# Edit .env with your WorkOS and AWS credentials

# Run migrations
uv run manage.py migrate
```

## Development

```bash
# Start Django + Tailwind (recommended)
uv run manage.py tailwind dev

# Or just Django (no live CSS rebuilding)
uv run manage.py runserver

# Migrations
uv run manage.py makemigrations
uv run manage.py migrate

# Django admin (useful for debugging auth)
uv run manage.py createsuperuser
# Then visit /admin/
```

## Bootstrap (historical)

How this project was created:

```bash
uv init .
uv add django==6.0
uv run django-admin startproject devopshero_site .
uv run manage.py startapp devopshero_app
uv run manage.py migrate
```

## OpenSpec

We use [OpenSpec](https://github.com/openspec-dev/openspec) for spec-driven development. Specs define what features do; proposals define what should change.

### Installation

```bash
npm install -g openspec-cli
openspec init .
openspec update .  # Updates instruction files
```

### Directory Structure

```
openspec/
├── AGENTS.md       # Instructions for AI assistants (managed by CLI)
├── project.md      # Project-specific constraints
├── specs/          # What IS built (truth)
│   ├── aws-account/spec.md
│   └── database-config/spec.md
└── changes/        # What SHOULD change (proposals)
    └── add-deployment-agent/
        ├── proposal.md
        ├── tasks.md
        ├── design.md
        └── specs/deployment-agent/spec.md
```

### Key Commands

```bash
# See what exists
openspec list --specs          # List implemented specs
openspec list                  # List pending changes
openspec show <name>           # View a spec or change

# Validate
openspec validate <name> --strict

# After implementing a change
openspec archive <change-id>   # Merges deltas into specs/
```

### Workflow

**For already-implemented features:**
1. Ask AI: "Create an OpenSpec for [feature]" — AI creates `openspec/specs/<capability>/spec.md`
2. Validate: `openspec validate <name> --type spec --strict`

**For new features:**
1. Ask AI: "Create a proposal for [feature]" or use `/openspec:proposal`
   - AI creates `openspec/changes/<change-id>/` with:
   - `proposal.md` — Why and what
   - `tasks.md` — Implementation checklist
   - `design.md` — Technical decisions (optional)
   - `specs/<capability>/spec.md` — Requirements with `## ADDED Requirements`
2. Validate: `openspec validate <change-id> --strict`
3. Review and approve the proposal
4. Ask AI: "Implement the proposal" or use `/openspec:apply`
5. After deployment: `openspec archive <change-id>`

### Spec Format

```markdown
# Capability Name

## Purpose
Brief description.

## Requirements

### Requirement: Clear Statement
The system SHALL do something.

#### Scenario: Success case
- **WHEN** user does X
- **THEN** system does Y
```

Every requirement needs at least one `#### Scenario:` with WHEN/THEN format.

### Quick Reference

- **See all specs** — `openspec list --specs`
- **See pending changes** — `openspec list`
- **Check if valid** — `openspec validate <name> --strict`
- **View details** — `openspec show <name>`
- **Ship a change** — `openspec archive <change-id>`
