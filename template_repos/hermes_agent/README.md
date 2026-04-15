# Hermes Agent

An AI personal assistant powered by [Hermes Agent](https://github.com/NousResearch/hermes-agent) with the [Hermes WebUI](https://github.com/nesquena/hermes-webui), configured for enterprise deployment via DevOps Hero.

## Features

- Hermes agent runtime with 47 built-in tools and autonomous execution
- Browser-based WebUI with three-panel layout (sessions, chat, workspace)
- Persistent layered memory that accumulates across sessions
- Self-improving skills system (auto-written from experience)
- Built-in cron scheduler for autonomous tasks
- Slack integration via Socket Mode (optional)
- Configurable model provider (Anthropic, OpenAI, OpenRouter, 19+ providers)
- Customizable personality via `SOUL.md`

## Requirements

- Docker
- At least one model provider API key (Anthropic, OpenAI, or OpenRouter)

## Local Setup

1. Copy the environment file and configure:

```bash
cp .env.example .env
# Edit .env with your API keys
```

2. Build and run the agent:

```bash
docker build -t hermes-agent .
docker run --rm --name hermes-agent \
    --env-file .env \
    -p 8787:8787 \
    hermes-agent
```

The WebUI will be available at `http://localhost:8787`.

## DOH Deployment

When deployed through DevOps Hero, the following are handled automatically:

- **Secrets** — `ANTHROPIC_API_KEY`, `HERMES_WEBUI_PASSWORD`, and Slack tokens are managed via DOH secrets.
- **Networking** — ALB routing with HTTPS via the environment's shared hosted zone.
- **Persistence** — EFS volume for `~/.hermes` (memory, sessions, skills, config).
- **Health checks** — ALB health check targets `/health` on port 8787.

### Blueprint Configuration

- **app_type** — web
- **build_strategy** — dockerfile
- **container_port** — 8787
- **health_check_path** — /health
- **cpu** — 1024 (minimum recommended)
- **memory** — 2048 (minimum recommended)

## Configuration

- **`SOUL.md`** — Agent personality and behavioral instructions. Seeded on first boot; persists on EFS. Edit to match your organization's tone and policies.
- **`config.yaml`** — Hermes agent configuration. Seeded on first boot; persists on EFS. Provider, model, and API keys are injected via environment variables.
- **Skills** — Hermes auto-writes skills from experience. They persist in `~/.hermes/skills/` on the EFS volume.
- **Memory** — Layered memory (user profile, agent memory, session history) persists in `~/.hermes/` as editable markdown files.

## Port

This application runs on port **8787**.
