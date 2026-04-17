# Hermes Agent

An AI personal assistant powered by [Hermes Agent](https://github.com/NousResearch/hermes-agent) with the [Hermes WebUI](https://github.com/nesquena/hermes-webui), configured for enterprise deployment via DevOps Hero.


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


## How Environment Variables Reach the Container

The Hermes template defines its variables in `seed_app_templates.py` under `runtime_variables`. Each variable has a `category` (`config` or `secret`) and a `value`. At deploy time, DOH splits them into two paths:

**Config vars** (`category: "config"`) become plain ECS environment variables. `template_deploy_service._materialize_environment_variables()` extracts them into `[{"name": ..., "value": ...}]` and stores them on the `DeploymentBlueprint`. The CDK then passes them as the `environment` dict on the ECS container definition. They arrive as regular `os.environ` in the container.

For hermes, this covers `DOH_LLM_PROVIDER` and `DOH_LLM_MODEL`.

**Secret vars** (`category: "secret"`) go through AWS Secrets Manager. `template_deploy_service._materialize_app_secrets()` extracts them into `{"KEY": value}` and stores them on the blueprint's `app_secrets` field. Before CDK runs, `secrets_utils.ensure_app_secrets_exist()` creates (or merges into) a Secrets Manager entry at `devopshero/{app-name}/secrets` as a JSON blob with all the keys. The CDK then wires each key as an `ecs.Secret.from_secrets_manager(field=...)` reference, so ECS resolves them at task startup — the container sees them as regular env vars, but they never appear in the CloudFormation template.

The `value` field in seed data controls initial resolution:

- `None` — auto-generate a random 64-char token at first deploy (e.g. gateway tokens)
- `""` — empty placeholder; if a shared secret exists for this environment with the same key, that value is copied in; otherwise stays empty for the user to fill in via AWS console
- `"literal"` — use as-is (e.g. `HERMES_WEBUI_PASSWORD: "mysquirrel"`)

For hermes, this covers `HERMES_WEBUI_PASSWORD`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `AWS_BEDROCK_ACCESS_KEY_ID`, `AWS_BEDROCK_SECRET_ACCESS_KEY`, `SLACK_APP_TOKEN`, and `SLACK_BOT_TOKEN`.

**Inside the container**, the entrypoint bridges these ECS env vars to the two places hermes reads them from:

- **`config.yaml`** — generated on first boot from `DOH_LLM_PROVIDER` and `DOH_LLM_MODEL`. Persists on EFS; not regenerated on reboot.
- **`.env` file** — regenerated every boot by writing each API key env var into `/home/hermeswebui/.hermes/.env`. The hermes agent subprocess loads this via dotenv. The WebUI server reads its own config (`HERMES_WEBUI_PASSWORD`) from the process environment directly — it's not in the `.env` file.


## AWS Bedrock Provider

Hermes supports AWS Bedrock natively (via the Converse API for most models, and the `AnthropicBedrock` SDK for Claude models with full feature parity — prompt caching, thinking budgets, adaptive thinking) DOH wires it up with three deploy-time variables:

- **`DOH_LLM_PROVIDER=bedrock`**
- **`DOH_LLM_MODEL`** — any bedrock model ID or inference profile (e.g. `us.anthropic.claude-opus-4-6-v1`, `anthropic.claude-sonnet-4-20250514-v1:0`, `amazon.nova-pro-v1:0`). Regional inference-profile prefixes (`us.`, `eu.`, `global.`) are supported.
- **`AWS_BEDROCK_ACCESS_KEY_ID`** / **`A.WS_BEDROCK_SECRET_ACCESS_KEY`** / **`AWS_BEDROCK_REGION`** — static IAM user credentials for an account that has Bedrock model access enabled.


## Storage Architecture: What's Ephemeral, What's Persistent

The container has two layers of storage: the **ephemeral container filesystem** (lost on every ECS task replacement) and a **persistent EFS volume** (survives across reboots, redeployments, and scaling events).

### How EFS is mounted

DOH creates a shared EFS filesystem per environment. Each hermes deployment gets its own **EFS access point** scoped to `/deployments/{app-name}` with UID/GID 1000 (the `hermeswebui` user). At runtime, ECS mounts this access point at `/home/hermeswebui/.hermes` — the hermes home directory. Everything under that path is persistent.

### What lives where

**On EFS (`/home/hermeswebui/.hermes`)** — all hermes state:

- `config.yaml` — agent configuration (seeded on first boot, never overwritten)
- `SOUL.md` — agent personality (seeded on first boot, never overwritten)
- `.env` — API keys (regenerated every boot from ECS env vars)
- `hermes-agent/` — the agent framework code (seeded on first boot, updated via `hermes update`)
- `skills/` — auto-written skills that hermes creates from experience
- `memories/` — layered memory files (user profile, agent memory, session history)
- `sessions/` — chat session data
- `webui-mvp/` — WebUI state (the `HERMES_WEBUI_STATE_DIR`)
- `workspace/` — the agent's working directory for file operations

**On the ephemeral container filesystem** — replaceable on every boot:

- `/opt/hermes-defaults/` — staging area with build-time copies of `hermes-agent` and `SOUL.md`, used only to seed EFS on first boot
- The WebUI binary (from the base image)
- The entrypoint script

### The `/workspace` symlink

Hermes uses `/workspace` as its default working directory for terminal commands and file tools (e.g. `touch`, `search_files`, `write_file`). The Dockerfile replaces the base image's `/workspace` directory with a symlink:

```
/workspace  ->  /home/hermeswebui/.hermes/workspace  (EFS)
```

This means all files the agent creates via terminal commands are transparently persisted on EFS.

### First boot vs subsequent boots

The entrypoint runs on every boot and does the following:

1. `mkdir -p /home/hermeswebui/.hermes/ /home/hermeswebui/.hermes/workspace/` — ensures directories exist (no-op after first boot)
2. Copies `hermes-agent` from `/opt/hermes-defaults/` — **only if `/home/hermeswebui/.hermes/hermes-agent/` doesn't exist**
3. Generates `config.yaml` from environment variables — **only if `/home/hermeswebui/.hermes/config.yaml` doesn't exist**
4. Copies `SOUL.md` from `/opt/hermes-defaults/` — **only if `/home/hermeswebui/.hermes/SOUL.md` doesn't exist**
5. Writes `.env` from environment variables — **every boot** (picks up DOH config changes)

On first boot all five steps do work. On subsequent boots only steps 1 and 5 are effective; 2-4 are skipped because their targets already exist on EFS. This means config changes made by the user (editing SOUL.md, running `hermes config set`, etc.) are preserved.

### Updating hermes

The container image and the agent framework are **independently versioned**:

- **WebUI** (the server binary) — updated by rebuilding the Docker image with a newer base image tag and redeploying through DOH.
- **Agent framework** (`hermes-agent/` on EFS) — updated via the built-in `hermes update` command, which downloads the latest release while preserving all user data. DOH does not run this automatically; it's up to the user.

When bumping the base image version, test against an existing EFS volume to verify compatibility. Most WebUI updates are backward-compatible, but major version bumps could require running `hermes update` to sync the agent framework.

## Port

This application runs on port **8787**.
