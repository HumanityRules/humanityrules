#!/bin/bash
set -euo pipefail

# Inside nono. Four siblings run here as hermeswebui:
#   1. Caddy on :8787 — policy-proxy forwards here; routes /webapps/* to user apps.
#   2. process-compose on :9956 — supervises apps from process-compose.yaml.
#   3. Hermes WebUI on :8789.
#   4. Hermes gateway
#
# If any child exits, we kill the others and exit.

WEBUI_PORT="${HERMES_WEBUI_PORT:-8789}"
CADDY_PORT=8787
PROCESS_COMPOSE_PORT=9956
WEBAPPS_ROOT=/workspace/webapps
WEBAPPS_YAML="${WEBAPPS_ROOT}/process-compose.yaml"
WEBAPPS_ROUTES="${WEBAPPS_ROOT}/routes.caddy"

CADDY_PID=""
PROCESS_COMPOSE_PID=""
WEBUI_PID=""
GATEWAY_PID=""

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

cleanup() {
    set +e
    for pid in "$WEBUI_PID" "$GATEWAY_PID" "$PROCESS_COMPOSE_PID" "$CADDY_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
}

trap cleanup EXIT INT TERM

seed_webapps_layout() {
    mkdir -p "${WEBAPPS_ROOT}/projects" "${WEBAPPS_ROOT}/logs"
    if [ ! -f "$WEBAPPS_YAML" ]; then
        cat > "$WEBAPPS_YAML" <<'EOF'
version: "0.5"
processes: {}
EOF
    fi
    if [ ! -f "$WEBAPPS_ROUTES" ]; then
        echo '# no routes' > "$WEBAPPS_ROUTES"
    fi
}

start_process_compose() {
    echo "[webui] Starting process-compose on 127.0.0.1:${PROCESS_COMPOSE_PORT}..."
    process-compose up \
        --config "$WEBAPPS_YAML" \
        --port "$PROCESS_COMPOSE_PORT" \
        --address 127.0.0.1 \
        --tui=false \
        --keep-project \
        2>&1 &
    PROCESS_COMPOSE_PID=$!
}

start_webui() {
    echo "[webui] Starting Hermes WebUI on 127.0.0.1:${WEBUI_PORT}..."
    cd "$HERMES_WEBUI_DIR"
    "$HERMES_WEBUI_PYTHON" server.py 2>&1 &
    WEBUI_PID=$!
}

start_gateway() {
    # Headless gateway — drives the cron ticker. With no messaging platforms
    # enabled (no TELEGRAM_BOT_TOKEN/etc. in env), `start_gateway()` logs
    # "Gateway will continue running for cron job execution." and the
    # ~60s cron tick thread spawns. --replace clears any stale gateway.pid
    # left over from a previous container run that crashed before atexit
    # could remove it.
    echo "[webui] Starting Hermes gateway (cron ticker + loopback API server)..."
    cd "$HERMES_WEBUI_AGENT_DIR"
    API_SERVER_ENABLED=true \
        "$HERMES_WEBUI_PYTHON" -m hermes_cli.main gateway run --replace -v 2>&1 &
    GATEWAY_PID=$!
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

wait_for_webui() {
    echo "[webui] Waiting for WebUI..."
    for _ in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${WEBUI_PORT}/health" >/dev/null 2>&1; then
            echo "[webui] WebUI is healthy."
            return
        fi
        if ! kill -0 "$WEBUI_PID" 2>/dev/null; then
            echo "[webui] FATAL: WebUI exited before becoming healthy." >&2
            wait "$WEBUI_PID"
            exit $?
        fi
        sleep 2
    done
    echo "[webui] FATAL: WebUI did not become healthy in time." >&2
    exit 1
}

main() {
    seed_webapps_layout
    bootstrap_admin_webapp
    start_process_compose
    start_webui
    wait_for_webui
    start_caddy
    start_gateway

    echo "[webui] All services up. caddy=${CADDY_PID} process-compose=${PROCESS_COMPOSE_PID} webui=${WEBUI_PID} gateway=${GATEWAY_PID}."
    wait -n "$CADDY_PID" "$PROCESS_COMPOSE_PID" "$WEBUI_PID" "$GATEWAY_PID"
    exit $?
}

main "$@"
