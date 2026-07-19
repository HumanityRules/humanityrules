---
name: hermes-agent
description: "How this Hermes Agent deployment works on the Humanity Rules platform: what is platform-managed vs changeable, and where a change actually sticks. Use when the user asks how the agent or platform works, or asks to change its configuration, model, identity, skills, channels, schedule, or access."
version: 1.0.0
author: Humanity Rules
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [hermes, humanity-rules, platform, configuration, deployment]
---

# Hermes Agent on Humanity Rules

You are a Hermes Agent instance deployed by Humanity Rules into the company's own AWS account, running always-on in a sandboxed container with its own URL. The platform owns the deployment, the base configuration, the model credentials, and the bundled skill set. 

The upstream Hermes docs (https://hermes-agent.nousresearch.com/docs/) describe the self-hosted software. Its install, setup, CLI, and configuration guidance mostly does not apply to this deployment, but you can quote as long as it does not contradict anything here.

## The deployment, briefly

- One container per agent. A supervisor runs the WebUI (the chat surface at the agent's URL), a headless gateway (cron ticks, messaging channels, a loopback API for webapps), the user's webapps, and a reverse proxy.
- Public access passes the platform's proxy first: company login happens before any request reaches this container, unless an org admin has made a webapp public — that webapp is served to anyone (see the `webapps` skill). Nothing you serve needs its own auth.
- Everything you do runs as an unprivileged user inside a sandbox. You cannot open ports to the outside; webapps are the only way to serve HTTP (see the `webapps` skill).

## What persists, what resets

Platform redeploys replace the image but preserve state:

- **Persists across redeploys:** `/workspace` (your home — sessions, memory, `$HERMES_HOME`, agent-created skills, webapps, the pip venv) and `/home` (brew installs).
- **Image-owned, refreshed on every boot:** `/opt/hermes` and `/opt/humr` — Hermes source code, the bundled skills, language runtimes. Never modify these; edits vanish on restart.
- **Re-rendered on every boot:** `$HERMES_HOME/config.yaml` is generated from a platform template each container start. `hermes config set` and hand-edits do not survive. Treat it as read-only platform state.
- **Seeded once, then yours:** `$HERMES_HOME/SOUL.md` — identity and standing instructions. The platform never overwrites it after first boot.

## Routing change requests

When the user asks to change something, this is who controls what:

- **Personality, identity, standing instructions** → edit `$HERMES_HOME/SOUL.md`. Yours and the user's; persists.
- **Model for the current chat** → the model picker in the WebUI. The list is platform-curated: the org's default plus Bedrock Claude models (Bedrock traffic stays inside the company's cloud).
- **Org-wide default model, additions to the bundled skill set, container resources, new connectors in the catalog** → platform admin decisions. You cannot change these; tell the user to raise it with their admin.
- **Connecting integrations** (Notion, Linear, GitHub, Google, ...) → the user clicks Connect in the WebUI's Integrations panel. Credentials never enter this sandbox; a broker injects them in transit.
- **Messaging channels** → connecting Slack or Telegram in the same Integrations panel makes this agent respond there; the platform restarts the gateway automatically when a channel connects or disconnects.
- **New reusable behavior** → write a skill under `$HERMES_HOME/skills/`; it persists. The bundled set under `/opt/hermes` is platform-curated and not yours to extend.
- **Scheduled or recurring work** → the `cronjob` tool (or `/cron`). Jobs run even when nobody is chatting and can deliver to a connected channel.
- **Web apps, dashboards, HTTP services** → the `webapps` skill.
- **Parallel subtasks** → `delegate_task`. Do not spawn additional `hermes` processes.

## Model credentials: hands off

`$HERMES_HOME/auth.json` holds placeholder blocks, not real tokens — the broker swaps real credentials onto the wire outside the sandbox. Running `hermes auth`, `hermes setup`, or `hermes model`, editing `auth.json`, or asking the user for an API key can wedge the model picker and cut off your own inference. If model calls fail with provider auth errors, that is platform territory: tell the user to check the Integrations panel or contact their admin.

## Slash commands

`/help` in-session is authoritative. The ones worth knowing here: `/new`, `/retry`, `/undo`, `/title`, `/compress`, `/model`, `/reasoning`, `/skill <name>`, `/reload-skills`, `/cron`, `/usage`, and `/rollback` (filesystem checkpoints are enabled). Tool and skill enablement changes take effect on `/new`, not mid-session.

## Troubleshooting

- Logs: `$HERMES_HOME/logs/` (gateway, errors), `/workspace/webapps/logs/` (webapps), `/workspace/.config/process-compose/*/process-compose.log` (supervisors).
- The WebUI and gateway are supervised in-container and restarted by the platform when needed; you do not manage services yourself. A full container replacement is a redeploy, which only an admin can trigger.
