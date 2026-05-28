# hermes_agent_local

Self-contained local dev stack for [`hermes_agent`](../hermes_agent/): Docker Compose, the dev policy-proxy shim, and host-side Bedrock credential refresh.

Production Hermes image and runtime live in `../hermes_agent`. This folder only wires them for laptop development.

## Quick start

```bash
cd template_repos/hermes_agent_local
cp .env.example .env    # optional
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

## Iterating on the WebUI extension

Edit files under `../hermes_agent/webui-extension/`; reload the browser. Rebuild the stack for Dockerfile, patch, or `doh_runtime` changes.

## Standalone policy proxy

```bash
cd local_policy_proxy
DOH_UPSTREAM_HOST=127.0.0.1 DOH_UPSTREAM_PORT=8787 DOH_LISTEN_PORT=8788 \
  pip install -r requirements.txt && python -m local_policy_proxy.main
```
