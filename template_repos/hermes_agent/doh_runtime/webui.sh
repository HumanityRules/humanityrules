#!/bin/bash
set -euo pipefail

# Inside nono. Three siblings run here as hermeswebui:
#   1. Caddy on :8787 — policy-proxy forwards here; routes /webapps/* to user apps.
#   2. process-compose on :9956 — supervises every DOH-managed process inside
#      nono: __admin, system.gateway, plus any user webapps.
#   3. Hermes WebUI on :8789.
#
# The Hermes gateway is supervised by process-compose as `system.gateway`,
# not started directly here, so the broker can restart it in place when
# vault credentials change.
#
# If any child exits, we kill the others and exit.

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_AGENT_DIR:?HERMES_WEBUI_AGENT_DIR must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PORT:?HERMES_WEBUI_PORT must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

CADDY_PORT=8787
PROCESS_COMPOSE_PORT=9956
WEBAPPS_ROOT=/workspace/webapps
PROCESS_COMPOSE_YAML=/workspace/.config/process-compose/process-compose.yaml
CADDY_ROUTES=/workspace/.config/caddy/routes.caddy

CADDY_PID=""
PROCESS_COMPOSE_PID=""
WEBUI_PID=""

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

start_webui() {
    echo "[webui] Starting Hermes WebUI on 127.0.0.1:${HERMES_WEBUI_PORT}..."
    cd "$HERMES_WEBUI_DIR"
    "$HERMES_WEBUI_PYTHON" server.py 2>&1 &
    WEBUI_PID=$!
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
    "$HERMES_WEBUI_PYTHON" /opt/doh/runtime/process_compose_seed.py system.gateway \
        --command "$HERMES_WEBUI_PYTHON -m hermes_cli.main gateway run --replace -v" \
        --cwd "$HERMES_WEBUI_AGENT_DIR" \
        --env API_SERVER_ENABLED=true
}

wait_for_webui() {
    echo "[webui] Waiting for WebUI..."
    for _ in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${HERMES_WEBUI_PORT}/health" >/dev/null 2>&1; then
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
    seed_caddy_layout
    seed_process_compose_layout
    bootstrap_admin_webapp
    bootstrap_gateway_process
    start_process_compose
    start_webui
    wait_for_webui
    start_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} process-compose=${PROCESS_COMPOSE_PID} webui=${WEBUI_PID}."
    wait -n "$CADDY_PID" "$PROCESS_COMPOSE_PID" "$WEBUI_PID"
    exit $?
}

main "$@"
