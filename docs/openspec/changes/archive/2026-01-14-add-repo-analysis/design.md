# Repository Analysis — Design

## Context

The deployment agent needs to examine user repositories and determine:
- What language/framework the app uses
- How to build and run it
- What infrastructure it needs (database, S3, queues, etc.)
- What environment variables are required

This analysis drives all downstream steps (Dockerfile generation, deployment planning, CDK code generation).

## Goals

- Accurate detection of language, framework, entrypoint, and dependencies
- Evidence-based findings (no guessing)
- Transparent investigation (user can see what the agent did)
- Structured output that feeds downstream steps

## Non-Goals

- Clarifying questions during analysis (user reviews findings after)
- Monorepo support (one repo = one app)
- Custom parsing tools (agent writes code instead)
- Token/size/time limits on what the agent can read — unlimited budget

## Decisions

### Decision: Local file:// URL as sole input

The sub-agent receives a `file:///` URL pointing to a local directory. No git clone is needed — the repository is already present on the filesystem.

**Rationale:** Simplifies v1. Git integration (https://, git@ URLs, cloning, authentication) is a separate concern for later. Avoids biasing the agent with caller assumptions — the agent forms its own hypotheses and tests them.

### Decision: Standard Agent SDK tools only

The sub-agent uses:
- **Bash** — run commands, execute scripts
- **Read** — read file contents
- **LS** — list directories
- **Glob** — find files by pattern
- **Grep** — search file contents

No custom tools.

**Rationale:** Bash is the escape hatch for anything not covered. No tool maintenance burden.

### Decision: Sub-agent uses a different tool set than the main agent

The sub-agent is configured independently from the main agent and uses a tool allow-list suited to repository inspection (Bash, Read, LS, Glob, Grep).

The main agent may continue to use a separate tool allow-list (e.g., MCP tools) and is not required to grant repository inspection capabilities directly.

**Rationale:** Prevents coupling repository inspection needs to the main agent's tool permissions model and makes it easier to keep the sub-agent minimal and auditable.

### Decision: Code execution over parsing tools

Instead of `parse.package_json(text)` tools, the agent writes and executes code:

```python
import json
pkg = json.loads(open("package.json").read())
print(f"Scripts: {pkg.get('scripts', {})}")
print(f"Dependencies: {list(pkg.get('dependencies', {}).keys())}")
```

**Rationale:** Agents are better at writing code than learning custom tool APIs. Code is transparent, flexible, and requires no maintenance.

### Decision: No clarifying questions during analysis

The sub-agent produces findings. It does not pause to ask clarifying questions. After findings are presented, the user can approve or request changes.

**Rationale:** Keeps the analysis phase simple and predictable. Clarification happens in the review phase.

### Decision: One repo = one app

The sub-agent assumes each repository contains exactly one deployable application. Monorepos with multiple apps are not supported.

**Rationale:** Simplifies v1. Monorepo detection and selection adds significant complexity.

## Structured Output

The sub-agent returns JSON:

```json
{
  "description": "A Django web application for managing notes with user authentication.",
  "language": "python",
  "framework": "django",
  "service": {
    "type": "web",
    "workdir": ".",
    "run_command": "gunicorn myapp.wsgi:application --bind 0.0.0.0:8000",
    "port": 8000,
    "healthcheck_path": "/health"
  },
  "dependencies": {
    "datastores": ["postgres"],
    "aws_services": []
  },
  "env": {
    "required": [
      {"name": "DATABASE_URL", "purpose": "PostgreSQL connection string"},
      {"name": "SECRET_KEY", "purpose": "Django secret key"}
    ],
    "optional": [
      {"name": "DEBUG", "purpose": "Enable debug mode"}
    ]
  },
  "caveats": [
    "No /health endpoint found — will need to add one for ALB health checks",
    "Uses SQLite in development — needs DATABASE_URL for production Postgres"
  ],
  "evidence": [
    {
      "claim": "framework=django",
      "file_path": "requirements.txt",
      "excerpt": "Django==5.x"
    },
    {
      "claim": "run_command=gunicorn config.wsgi:application --bind 0.0.0.0:8000",
      "file_path": "README.md",
      "excerpt": "gunicorn config.wsgi:application --bind 0.0.0.0:8000"
    }
  ]
}
```

### Output Fields

- **description** — Human-readable summary (2-5 sentences)
- **language** — Primary language (python, node, elixir, go, etc.)
- **framework** — Detected framework (django, fastapi, nextjs, phoenix, express, etc.)
- **service** — The deployable service:
  - **type** — web | worker | scheduled_task
  - **workdir** — Path within repo (usually ".")
  - **run_command** — How to start the service
  - **port** — Listening port (web only)
  - **healthcheck_path** — Health endpoint path (web only)
- **dependencies** — Infrastructure needs:
  - **datastores** — postgres, redis, mysql, etc.
  - **aws_services** — s3, sqs, dynamodb, etc.
- **env** — Environment variables:
  - **required** — List of {name, purpose}
  - **optional** — List of {name, purpose}
- **caveats** — Warnings, concerns, things the user should know
- **evidence** — Evidence items supporting major claims (framework, run command, port/health path, dependencies)

## Investigation Strategy

The sub-agent operates as an investigator. Typical flow:

1. **Survey** — List files at the local path, find manifests (package.json, pyproject.toml, requirements.txt, mix.exs, etc.)
2. **Hypothesize** — "This looks like Django because of manage.py"
3. **Gather evidence** — Read files, search for patterns, run scripts
4. **Classify** — Determine service type (web/worker/scheduled)
5. **Extract config** — Parse .env.example, find port/health patterns
6. **Produce output** — Structured JSON with caveats

The agent is free to explore in whatever order makes sense. No fixed procedure.

## Evidence Discipline

Every claim should be traceable to evidence. The agent should not guess.

- **Good:** "Django detected — manage.py present, django in requirements.txt"
- **Bad:** "This looks like it might be Django"

If evidence is weak or ambiguous, the finding goes in **caveats**, not in a confident assertion.

## How Output Feeds Downstream

- **Step 3 (Dockerfile Generation)** — uses language, framework, workdir, run_command, port
- **Step 5 (Deployment Plan)** — uses service, dependencies, env, caveats
- **Step 6 (CDK Code Generation)** — uses service.type and dependencies to select constructs

**Note:** This change only implements the analysis sub-agent and user review flow. Wiring the output to downstream steps (Dockerfile generation, Deployment Plan, CDK) is not part of this scope — those integrations will be built when the respective steps are implemented.

## User Review Flow

After analysis completes:

1. Findings displayed to user in chat (human-readable rendering of the JSON)
2. User can accept (implicitly, by proceeding) or give corrections in natural language
3. If corrections given (e.g., "the port should be 3000", "add Redis as a dependency"), the main agent re-spawns the sub-agent with the new instructions
4. Process repeats until user is satisfied

## Test Harness

A standalone test harness runs the sub-agent directly, bypassing Django conversation/database and UI. This enables rapid iteration on the system prompt and output schema.

### Structure

```
devopshero_app/services/agent/
├── repo_analysis/
│   ├── __init__.py
│   ├── repo_analysis_agent.py    # Sub-agent entry point (no Django models, no streaming)
│   ├── system_prompt.md          # Sub-agent system prompt
│   └── test_repo_analysis.py     # Test harness
```

### How It Works

The test harness:
1. Calls the sub-agent entry point (a separate agent implementation from the main agent)
2. Sub-agent uses standard SDK tools (Bash, Read, LS, Glob, Grep) — no MCP, no custom tools
3. Sends a query with a `file://` URL pointing to a reference app
4. Collects the structured JSON output
5. Validates against expected results

### Reference App Expectations

Each reference app in `deployable_repos/` has expected outputs:

```python
REFERENCE_APPS = {
    "django_postgres_app": {
        "language": "python",
        "framework": "django",
        "service": {"type": "web", "port": 8000},
        "dependencies": {"datastores": ["postgres"]},
    },
    "fastapi_app": {
        "language": "python",
        "framework": "fastapi",
        "service": {"type": "web", "port": 8000},
        "dependencies": {"datastores": []},
    },
    "job_processor": {
        "language": "python",
        "service": {"type": "worker"},
        "dependencies": {"aws_services": ["sqs", "s3"]},
    },
    # ... more apps
}
```

### Running Tests

```bash
# Test a single app during development
python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis --app django_postgres_app

# Run all reference apps
python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis
```

### Why This Approach

- **No Django required** — bypasses database/conversation setup
- **No UI required** — pure Python, runs from terminal
- **Uses real LLM** — tests actual agent behavior, not mocks
- **Reuses existing code** — same agent infrastructure as production
- **Fast iteration** — change prompt, run test, see results

## Out of Scope

- **Git URL support** — no cloning; only local file:// URLs supported in v1
- **Clarifying questions during analysis** — agent produces best-effort findings; user corrects after
- **Monorepo support** — one repo = one app
- **Multiple services per repo** — web + worker combos handled in future version
- **Custom parsing tools** — agent writes code instead
- **Security scanning** — future enhancement (separate sub-agent)
- **Cost estimation** — handled in Deployment Plan step, not here
- **Analysis persistence** — results will be persisted to database, but not in this change

## Risks / Trade-offs

- **Risk:** Agent may misidentify framework in ambiguous repos
  - **Mitigation:** Evidence discipline + caveats + user review

- **Risk:** Large repos may have many files to survey
  - **Mitigation:** Agent uses Glob/Grep strategically; doesn't read everything
