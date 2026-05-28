# local_policy_proxy

Dev-only stand-in for [`policy_proxy`](../policy_proxy/) when running Hermes via `template_repos/hermes_agent/docker-compose.yml`.

Production `policy_proxy` verifies SSO, runs PDP, and sets `X-Forwarded-Host` before forwarding to Caddy on :8787. This package does **only** the forward + header normalization (same `X-Forwarded-Host` behavior as `policy_proxy/proxy.py`), with no auth.

Also strips the listen port from `Origin` and `Referer` (e.g. `http://localhost:8788` → `http://localhost`) so Hermes WebUI's CSRF check matches `DOH_PUBLIC_HOSTNAME`.

## Config

Uses the same upstream/listen env names as policy-proxy for compose symmetry:

| Var | Example | Purpose |
| --- | --- | --- |
| `DOH_UPSTREAM_HOST` | `hermes` | Compose service name for the Hermes container |
| `DOH_UPSTREAM_PORT` | `8787` | Caddy inside Hermes |
| `DOH_LISTEN_PORT` | `8788` | Port browsers hit locally |

## Run (standalone)

```bash
cd template_repos/local_policy_proxy
DOH_UPSTREAM_HOST=127.0.0.1 DOH_UPSTREAM_PORT=8787 DOH_LISTEN_PORT=8788 \
  pip install -r requirements.txt && python -m local_policy_proxy.main
```

## With Hermes compose

From `template_repos/hermes_agent`:

```bash
docker compose up --build
```

Open **http://localhost:8788/** (not :8789).
