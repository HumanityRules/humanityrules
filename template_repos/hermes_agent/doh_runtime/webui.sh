#!/bin/bash
set -euo pipefail

# Inside nono. Three siblings run here as hermeswebui:
#   1. Caddy on :8787 — policy-proxy forwards here; routes /webapps/* to user apps.
#   2. process-compose on :9956 — supervises apps from process-compose.yaml.
#   3. Hermes WebUI on :8789.
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

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

cleanup() {
    set +e
    for pid in "$WEBUI_PID" "$PROCESS_COMPOSE_PID" "$CADDY_PID"; do
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

start_caddy() {
    echo "[webui] Starting Caddy on :${CADDY_PORT}..."
    caddy run --config /opt/doh/runtime/Caddyfile --adapter caddyfile --watch 2>&1 &
    CADDY_PID=$!
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
    start_process_compose
    start_webui
    wait_for_webui
    start_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} process-compose=${PROCESS_COMPOSE_PID} webui=${WEBUI_PID}."
    wait -n "$CADDY_PID" "$PROCESS_COMPOSE_PID" "$WEBUI_PID"
    exit $?
}

main "$@"
