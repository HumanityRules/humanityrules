# hermes_agent_local

Self-contained local dev stack for [`hermes_agent`](../hermes_agent/): Docker Compose, the dev policy-proxy shim, and host-side Bedrock credential refresh.

Production Hermes image and runtime live in `../hermes_agent`. This folder only wires them for laptop development.

## Quick start

```bash
cd template_repos/hermes_agent_local
cp .env.example .env    # optional
docker compose up --build --watch    # recommended while developing — logs + rebuilds on save
```

One-shot (no file watcher):

```bash
docker compose up --build
```

Open **http://localhost:8788/** (not :8789).

## Layout

- **`docker-compose.yml`** — `hermes` (builds `../hermes_agent`) + `local_policy_proxy`
- **`local_policy_proxy/`** — dev-only forwarder; sets `X-Forwarded-Host` like production `policy_proxy`, no SSO
- **`refresh-bedrock-creds.sh`** / **`bedrock-creds-launchagent.sh`** — keep host `bedrock_dev` fresh via HumanityRules `opsh`

## Bedrock credentials

Compose mounts `${HOME}/.aws` read-only into the container. Hermes signs Bedrock calls through `aws_signer`, which re-reads creds when AWS returns `ExpiredTokenException`.

The host file must stay fresh. Claude Code can refresh on demand (`awsAuthRefresh`); for Hermes while Claude is idle:

```bash
./refresh-bedrock-creds.sh
```

On macOS, run a LaunchAgent (~every 45 minutes; does not refresh at login):

```bash
./bedrock-creds-launchagent.sh start
./bedrock-creds-launchagent.sh stop
```

`install` writes the plist without loading it.

Requires `opsh` on `PATH` (HumanityRules OPS console) and a working `bedrock_dev` profile in `~/.aws/config`.

## Rebuilds, watch, and caching

The Hermes Dockerfile keeps DOH-owned source (`doh_runtime/`, `webui-extension/`, `skills/`) in **late COPY layers** so routine edits reuse cached `uv pip install` and Linuxbrew layers instead of rebuilding them.

**While developing**, leave `docker compose up --watch` running (add `--build` on first run or after Dockerfile changes). Unlike standalone `docker compose watch`, `up --watch` streams container logs like `docker compose up`. On save it rebuilds the Hermes image (~1s for source edits), recreates the container, and the persistent-root runner rsyncs the new `/opt/doh` tree on start — the reliable path (no bind mounts that get clobbered).

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
DOH_UPSTREAM_HOST=127.0.0.1 DOH_UPSTREAM_PORT=8787 DOH_LISTEN_PORT=8788 \
  pip install -r requirements.txt && python -m local_policy_proxy.main
```
