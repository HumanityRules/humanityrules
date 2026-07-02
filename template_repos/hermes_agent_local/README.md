# hermes_agent_local

Self-contained local dev stack for [`hermes_agent`](../hermes_agent/): Docker Compose, the dev policy-proxy shim.

Production Hermes image and runtime live in `../hermes_agent`. This folder only wires them for laptop development.

## Quick start

```bash
cd template_repos/hermes_agent_local
cp .env.example .env    # optional
docker compose up --build --watch    # recommended while developing — logs + rebuilds on save
```

Open **http://localhost:8788/**

## Layout

- **`docker-compose.yml`** — `hermes` (builds `../hermes_agent`) + `local_policy_proxy`
- **`local_policy_proxy/`** — dev-only forwarder; sets `X-Forwarded-Host` like production `policy_proxy`, no SSO

## Bedrock credentials

Compose mounts `${HOME}/.aws` read-only into the container. Hermes signs Bedrock calls through `aws_signer`, which re-reads creds when AWS returns `ExpiredTokenException`.

## Rebuilds, watch, and caching

The Hermes Dockerfile keeps HUMR-owned source (`humr_runtime/`, `webui-extension/`, `skills/`) in **late COPY layers** so routine edits reuse cached `uv pip install` and Linuxbrew layers instead of rebuilding them.

**While developing**, leave `docker compose up --watch` running (add `--build` on first run or after Dockerfile changes). Unlike standalone `docker compose watch`, `up --watch` streams container logs like `docker compose up`. On save it rebuilds the Hermes image (~1s for source edits), recreates the container, and the persistent-root runner rsyncs the new `/opt/humr` tree on start — the reliable path (no bind mounts that get clobbered).

Manual rebuild:

```bash
docker compose up --build
```

After a watch-triggered rebuild, Hermes is unavailable until the healthcheck passes (up to ~2 minutes with the current `start_period`). Reload the browser once http://localhost:8788 responds again.

Rebuilds are slow only when patches, upstream pins, or Dockerfile structure change. BuildKit cache mounts (apt, uv, git clones) speed cold builds and cache busts.

## Iterating on the WebUI extension

Edit files under `../hermes_agent/webui-extension/` while `docker compose up --watch` is running; reload the browser after the container comes back healthy.

## Standalone policy proxy

```bash
cd local_policy_proxy
HUMR_UPSTREAM_HOST=127.0.0.1 HUMR_UPSTREAM_PORT=8787 HUMR_LISTEN_PORT=8788 \
  pip install -r requirements.txt && python -m local_policy_proxy.main
```
