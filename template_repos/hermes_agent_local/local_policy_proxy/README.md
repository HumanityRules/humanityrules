# local_policy_proxy

Dev-only stand-in for [`policy_proxy`](../../policy_proxy/). Bundled in [`hermes_agent_local`](../README.md) for local Hermes compose.

Production `policy_proxy` verifies SSO, runs PDP, and sets `X-Forwarded-Host` before forwarding to Caddy on :8787. This package does **only** the forward + header normalization, with no auth.

## Config

| Var | Example | Purpose |
| --- | --- | --- |
| `DOH_UPSTREAM_HOST` | `hermes` | Compose service name for the Hermes container |
| `DOH_UPSTREAM_PORT` | `8787` | Caddy inside Hermes |
| `DOH_LISTEN_PORT` | `8788` | Port browsers hit locally |

## With Hermes compose

From `template_repos/hermes_agent_local`:

```bash
docker compose up --build
```

Open **http://localhost:8788/**.
