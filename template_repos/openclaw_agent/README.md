# OpenClaw Agent

An AI personal assistant powered by [OpenClaw](https://github.com/openclaw/openclaw), configured for enterprise deployment via Humanity Rules. Supports Slack integration out of the box.

## Features

- OpenClaw agent runtime with ReAct loop and tool execution
- Slack integration via Socket Mode (optional)
- Configurable model provider (Anthropic, OpenAI)
- Health check endpoint at `/health`
- Customizable personality via `SOUL.md`; OpenClaw's default `HEARTBEAT.md` for scheduled tasks

## Requirements

- Docker
- At least one model provider API key (Anthropic or OpenAI)

## Local Setup

1. Copy the environment file and configure:

```bash
cp .env.example .env
# Edit .env with your API keys and database credentials
```

2. Build and run the agent:

```bash
docker build -t openclaw-agent .
docker run --rm --name openclaw-agent \
    --env-file .env \
    -p 18789:18789 \
    openclaw-agent
```

The gateway will be available at `http://localhost:18789`.

3. Verify the health check:

```bash
curl http://localhost:18789/health
```

## HUMR Deployment

When deployed through Humanity Rules, the following are handled automatically:

- **Secrets** — `ANTHROPIC_API_KEY`, `OPENCLAW_GATEWAY_TOKEN`, and Slack tokens are managed via HUMR secrets.
- **Networking** — ALB routing with HTTPS via the environment's shared hosted zone.
- **Health checks** — ALB health check targets `/health` on port 18789.

### Blueprint Configuration

- **app_type** — web
- **build_strategy** — dockerfile
- **container_port** — 18789
- **health_check_path** — /health
- **cpu** — 1024 (minimum recommended)
- **memory** — 2048 (minimum recommended)

## Authentication & Device Pairing

OpenClaw's gateway has two layers of access control:

1. **Auth mode** (`gateway.auth.mode`) — controls who can connect. Options: `none`, `token`, `password`, `trusted-proxy`.
2. **Device pairing** (`device-pair` plugin) — after auth succeeds, each new browser/client must be "paired" by an operator approving it via `openclaw devices approve <request-id>`. This is designed for personal self-hosted setups where the owner manually approves their own devices.

For HUMR enterprise deployments, the manual pairing step doesn't make sense — HUMR already governs access through ABAC, approval workflows, and VPC network isolation. We disable it with:

```json5
// openclaw.json
gateway: {
  controlUi: {
    dangerouslyDisableDeviceAuth: true,
  },
}
```

This makes the gateway rely on token auth only (no per-device approval). The `dangerously` prefix is OpenClaw's warning for self-hosters; behind HUMR's ALB + governance layer, this is the correct setting.

**Future option: `trusted-proxy` mode.** When HUMR injects user identity headers at the ALB (e.g., via WorkOS/AuthKit claims), OpenClaw can consume them directly via `gateway.auth.mode: "trusted-proxy"` with `gateway.auth.trustedProxy.userHeader` pointing to the identity header. This would give per-user audit trails inside OpenClaw itself. Not implemented yet — requires HUMR to forward identity headers to deployed apps.

## Customization

- **`workspace/SOUL.md`** — Agent personality and behavioral instructions. Edit to match your organization's tone and policies.
- **`openclaw.json`** — OpenClaw configuration (JSON5). Gateway, agent defaults, and structural settings. Provider API keys and Slack tokens are auto-detected from env vars.
- **`HEARTBEAT.md`** — Not included; OpenClaw creates its default on first run. Customize at runtime to add scheduled tasks.

## Endpoints

- **GET /health** — Liveness probe (200 = healthy, 503 = unhealthy)
- **GET /ready** — Readiness probe
- **GET /health/detailed** — Detailed health status with component checks

## Port

This application runs on port **18789**.
