#!/bin/bash
set -euo pipefail

# Inside nono. Three siblings run here as hermeswebui:
#   1. Caddy on :8787 — public-facing reverse proxy. Routes /webapps/<slug>/*
#      to user apps; falls back to WebUI on 127.0.0.1:8788.
#   2. process-compose on 127.0.0.1:9956 — supervises user app processes
#      defined in /workspace/webapps/process-compose.yaml.
#   3. Hermes WebUI on 127.0.0.1:8788.
#
# If any child exits, we kill the others and exit. supervisor.sh's trap then
# tears down the root-side daemons (aws_signer, integrations_broker).

WEBUI_PORT="${HERMES_WEBUI_PORT:-8788}"
CADDY_PORT=8787
PROCESS_COMPOSE_PORT=9956

WEBAPPS_ROOT=/workspace/webapps
WEBAPPS_PROCESS_COMPOSE="${WEBAPPS_ROOT}/process-compose.yaml"
WEBAPPS_CADDY_DIR="${WEBAPPS_ROOT}/caddy"
WEBAPPS_PROJECTS_DIR="${WEBAPPS_ROOT}/projects"
WEBAPPS_LOGS_DIR="${WEBAPPS_ROOT}/logs"

CADDY_PID=""
PROCESS_COMPOSE_PID=""
WEBUI_PID=""

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

cleanup() {
    set +e
    if [ -n "$WEBUI_PID" ] && kill -0 "$WEBUI_PID" 2>/dev/null; then
        kill -TERM "$WEBUI_PID" 2>/dev/null
    fi
    if [ -n "$PROCESS_COMPOSE_PID" ] && kill -0 "$PROCESS_COMPOSE_PID" 2>/dev/null; then
        kill -TERM "$PROCESS_COMPOSE_PID" 2>/dev/null
    fi
    if [ -n "$CADDY_PID" ] && kill -0 "$CADDY_PID" 2>/dev/null; then
        kill -TERM "$CADDY_PID" 2>/dev/null
    fi
}

trap cleanup EXIT INT TERM

seed_webapps_layout() {
    mkdir -p "$WEBAPPS_CADDY_DIR" "$WEBAPPS_PROJECTS_DIR" "$WEBAPPS_LOGS_DIR"
    if [ ! -f "$WEBAPPS_PROCESS_COMPOSE" ]; then
        cat > "$WEBAPPS_PROCESS_COMPOSE" <<'EOF'
version: "0.5"
processes: {}
EOF
    fi
}

start_caddy() {
    echo "[webui] Starting Caddy on :${CADDY_PORT}..."
    caddy run --config /opt/doh/runtime/Caddyfile --adapter caddyfile --watch 2>&1 &
    CADDY_PID=$!
}

start_process_compose() {
    echo "[webui] Starting process-compose on 127.0.0.1:${PROCESS_COMPOSE_PORT}..."
    # --keep-project keeps the daemon alive when the process list is empty;
    # otherwise process-compose exits as soon as no processes are running.
    process-compose up \
        --config "$WEBAPPS_PROCESS_COMPOSE" \
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

wait_for_webui() {
    echo "[webui] Waiting for WebUI to start..."
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
}

wait_for_caddy() {
    echo "[webui] Waiting for Caddy to start..."
    # /health falls through Caddy → WebUI. WebUI is already healthy here, so
    # 200 means Caddy is also up and its catch-all reverse_proxy works.
    for _ in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:${CADDY_PORT}/health" >/dev/null 2>&1; then
            echo "[webui] Caddy is healthy."
            return
        fi
        if ! kill -0 "$CADDY_PID" 2>/dev/null; then
            echo "[webui] FATAL: Caddy exited before becoming healthy." >&2
            wait "$CADDY_PID"
            exit $?
        fi
        sleep 1
    done
}

main() {
    seed_webapps_layout
    start_process_compose
    start_webui
    wait_for_webui
    start_caddy
    wait_for_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} process-compose=${PROCESS_COMPOSE_PID} webui=${WEBUI_PID}."

    # Exit as soon as any child dies; the cleanup trap takes the rest down.
    wait -n "$CADDY_PID" "$PROCESS_COMPOSE_PID" "$WEBUI_PID"
    exit $?
}

main "$@"
