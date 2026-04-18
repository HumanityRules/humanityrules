# Personal Assistant Deployment — Current State Report

## What this feature is

DOH deploys **Hermes Agent** — a governed AI personal assistant — into a customer's AWS VPC as a Fargate app, via the same one-click template-deploy flow used for OpenClaw. Employees get a real agent (tool execution, persistent memory, skills) reachable through WebUI and Slack. Companies keep central governance: approved model providers, per-deployment IAM, per-agent EFS isolation, secrets managed centrally.

## Core pieces in place

- **Hermes Agent AppTemplate** — full template repo under `template_repos/hermes_agent/` (Dockerfile, entrypoint, `config.yaml.template`, SOUL.md, README).
- **Deploy flow** — template picker → deploy form with grouped, user-editable runtime variables (Main LLM, Auxiliary LLM, Slack groups); required-empty groups auto-expand. Workspace/Environment use the shared dropdown component (light-mode-fixed).
- **Multi-channel** — `start_with_gateway.sh` runs WebUI + Slack gateway side-by-side when Slack tokens are present; WebUI-only otherwise. `SLACK_HOME_CHANNEL` exposed for proactive messages.
- **Persistent state on EFS** — mount at `/home/hermeswebui/.hermes` with per-app access point (path + UID isolation, UID 1024 for hermes). `/workspace` symlinked into EFS so terminal/file-tool output survives restarts. `hermes-agent` framework staged at `/opt/hermes-defaults/`, seeded on first boot; upstream `hermes update` manages later versions.
- **LLM configuration** — `DOH_LLM_PROVIDER` / `DOH_LLM_MODEL` / `DOH_LLM_BASE_URL` (user-editable); auxiliary slots unified under `DOH_AUX_*` and fanned out into all 8 aux positions; defaults to Bedrock Opus 4.6 main / Sonnet 4.6 aux.
- **Bedrock governance path** — "nothing leaves our VPC" supported via Bedrock provider. Upstream Hermes gaps worked around by a runtime patch system (`patches/` dir with unified diffs + `BedrockAuxiliaryClient` overlay, applied idempotently on every boot against the EFS tree).
- **Secrets** — per-app Secrets Manager entry + shared-per-environment store (`devopshero/{env-slug}/shared-secrets`) so common API keys are entered once per env and merged in at deploy time. Auto-generated password for WebUI (`HERMES_WEBUI_PASSWORD`).
- **Operator tooling** — `doh_app_shell`, `doh_app_logs` (with `--follow`), `doh_efs_browse`, `doh_secrets` (incl. shared subcommands). `customer-debug` skill documents the two-plane model.

## Known constraints / open surface

- The Bedrock support depends on the local patch stack; if upstream Hermes ships PR #11700, the patches become no-ops automatically.
- Aux LLM assumes one shared provider/model across all 8 slots (simple knob; not per-task tunable yet).
- `SLACK_HOME_CHANNEL` default is a placeholder that fails politely until the operator sets a real channel ID.
- Deploy rollback granularity: CloudFormation-native since the two-phase deploy refactor; we lose task-level crash diagnostics on first-deploy failures.

## Where the governance story stands

ABAC / approval workflows / audit trails are inherited from DOH's existing pipeline — the Hermes template rides that infrastructure rather than adding a parallel path. The governance pieces specific to this feature are: model-provider pinning (Bedrock), per-agent EFS access point (chroot-like isolation), shared secrets (central key custody), and user-editable vs hidden runtime variables (template author decides what's operator-tunable per deploy).
