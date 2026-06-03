#!/bin/bash
set -euo pipefail

# Inside nono. Two direct children run here as hermeswebui:
#   1. process-compose on :9956 — supervises every DOH-managed process inside
#      nono: system.webui, system.gateway, __admin, plus any user webapps.
#   2. Caddy on :8787 — policy-proxy forwards here; routes /webapps/* to user apps.
#
# The Hermes WebUI and gateway are supervised by process-compose so the broker
# can restart them in place when managed integration env changes.
#
# If a direct child exits, we kill the other and exit.

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_AGENT_DIR:?HERMES_WEBUI_AGENT_DIR must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PORT:?HERMES_WEBUI_PORT must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

# Loopback Hermes agent API (gateway's api_server platform, 127.0.0.1:8642).
# Enabled for in-sandbox webapps that want to call a full Hermes agent at
# http://127.0.0.1:8642/v1/. Upstream *requires* a bearer key even for
# loopback binds, so we mint a fixed one here. This is NOT a secret: it only
# guards a loopback port inside this nono sandbox, where every process already
# shares a uid, venv, and workspace and fully trusts its siblings. Exporting it
# here makes it visible to the gateway AND to every process-compose child
# (webapps inherit the parent env), so webapps read it from $API_SERVER_KEY.
export API_SERVER_ENABLED=true
export API_SERVER_KEY=doh-loopback-gateway-key

CADDY_PORT=8787
PROCESS_COMPOSE_PORT=9956
WEBAPPS_ROOT=/workspace/webapps
PROCESS_COMPOSE_YAML=/workspace/.config/process-compose/process-compose.yaml
CADDY_ROUTES=/workspace/.config/caddy/routes.caddy

CADDY_PID=""
PROCESS_COMPOSE_PID=""

cleanup() {
    set +e
    for pid in "$PROCESS_COMPOSE_PID" "$CADDY_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
}

trap cleanup EXIT INT TERM

seed_webapps_layout() {
    mkdir -p "${WEBAPPS_ROOT}/projects" "${WEBAPPS_ROOT}/logs"
}

seed_caddy_layout() {
    mkdir -p "$(dirname "$CADDY_ROUTES")"
    if [ ! -f "$CADDY_ROUTES" ]; then
        echo '# no routes' > "$CADDY_ROUTES"
    fi
}

seed_process_compose_layout() {
    mkdir -p "$(dirname "$PROCESS_COMPOSE_YAML")"
    if [ ! -f "$PROCESS_COMPOSE_YAML" ]; then
        cat > "$PROCESS_COMPOSE_YAML" <<'EOF'
version: "0.5"
processes: {}
EOF
    fi
}

start_process_compose() {
    echo "[webui] Starting process-compose on 127.0.0.1:${PROCESS_COMPOSE_PORT}..."
    process-compose up \
        --config "$PROCESS_COMPOSE_YAML" \
        --port "$PROCESS_COMPOSE_PORT" \
        --address 127.0.0.1 \
        --tui=false \
        --keep-project \
        2>&1 &
    PROCESS_COMPOSE_PID=$!
}

start_caddy() {
    echo "[webui] Starting Caddy on :${CADDY_PORT}..."
    caddy run --config /opt/doh/runtime/Caddyfile --adapter caddyfile --watch 2>&1 &
    CADDY_PID=$!
}

bootstrap_admin_webapp() {
    # Runs *before* process-compose starts. --no-start writes the YAML entry
    # plus the route, then `process-compose up` brings __admin up alongside
    # any user webapps from prior boots. Talking to the daemon at this stage
    # would hang: the `project update` CLI client (subprocess.run) doesn't
    # return promptly during initial supervision, blocking webui.sh forever.
    # --if-missing is the cold-restart idempotence: if __admin is already
    # in the YAML, skip silently.
    webapps create __admin --if-missing --no-start \
        --command "$HERMES_WEBUI_PYTHON -m admin" \
        --cwd /opt/doh/runtime
}

bootstrap_gateway_process() {
    # The gateway process: Hermes's headless cron ticker + loopback API
    # server, plus any messaging-platform bindings activated by env vars
    # the broker writes into ${HERMES_HOME}/.env (see
    # docs/gateway_env_and_restart_design.md).
    #
    # Goes through process-compose so the broker can hot-restart it
    # via REST when vault credentials change without bouncing the whole
    # container. --replace clears any stale gateway.pid left over from a
    # previous container run that crashed before atexit could remove it.
    #
    # API_SERVER_ENABLED / API_SERVER_KEY are exported at container scope (top
    # of this file) so both the gateway and webapp children inherit them.
    "$HERMES_WEBUI_PYTHON" /opt/doh/runtime/process_compose_seed.py system.gateway \
        --command "$HERMES_WEBUI_PYTHON -m hermes_cli.main gateway run --replace -v" \
        --cwd "$HERMES_WEBUI_AGENT_DIR"
}

bootstrap_webui_process() {
    # profile_env_exec.py overlays ${HERMES_HOME}/.env into WebUI's process env
    # on every start. Keeping WebUI under process-compose lets the broker
    # restart only WebUI after rewriting connected-provider env such as
    # GITHUB_TOKEN.
    "$HERMES_WEBUI_PYTHON" /opt/doh/runtime/process_compose_seed.py system.webui \
        --command "$HERMES_WEBUI_PYTHON /opt/doh/runtime/profile_env_exec.py $HERMES_WEBUI_PYTHON server.py" \
        --cwd "$HERMES_WEBUI_DIR"
}

wait_for_webui() {
    echo "[webui] Waiting for WebUI..."
    for _ in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${HERMES_WEBUI_PORT}/health" >/dev/null 2>&1; then
            echo "[webui] WebUI is healthy."
            return
        fi
        if ! kill -0 "$PROCESS_COMPOSE_PID" 2>/dev/null; then
            echo "[webui] FATAL: process-compose exited before WebUI became healthy." >&2
            wait "$PROCESS_COMPOSE_PID"
            exit $?
        fi
        sleep 2
    done
    echo "[webui] FATAL: WebUI did not become healthy in time." >&2
    exit 1
}

main() {
    seed_webapps_layout
    seed_caddy_layout
    seed_process_compose_layout
    bootstrap_admin_webapp
    bootstrap_gateway_process
    bootstrap_webui_process
    start_process_compose
    wait_for_webui
    start_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} process-compose=${PROCESS_COMPOSE_PID}."
    wait -n "$CADDY_PID" "$PROCESS_COMPOSE_PID"
    exit $?
}

main "$@"
