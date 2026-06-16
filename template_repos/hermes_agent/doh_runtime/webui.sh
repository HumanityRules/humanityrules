#!/bin/bash
set -euo pipefail

# Inside nono. Three direct children run here as hermeswebui:
#   1. system process-compose on :9956 — supervises system.webui and system.gateway.
#   2. webapps process-compose on :9957 — supervises __admin and user webapps.
#   3. Caddy on :8787 — policy-proxy forwards here; routes to WebUI and webapps.
#
# The Hermes WebUI and gateway are supervised by process-compose so the broker
# can restart them in place when managed integration env changes.
#
# If a direct child exits, we kill the others and exit.

: "${DOH_RUNTIME_DIR:?DOH_RUNTIME_DIR must be set}"
: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${AWS_DEFAULT_REGION:?AWS_DEFAULT_REGION must be set}"
: "${HERMES_WEBUI_AGENT_DIR:?HERMES_WEBUI_AGENT_DIR must be set}"
: "${HERMES_WEBUI_DEFAULT_WORKSPACE:?HERMES_WEBUI_DEFAULT_WORKSPACE must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_EXTENSION_DIR:?HERMES_WEBUI_EXTENSION_DIR must be set}"
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

# Point the WebUI at our extension bundle. EXTENSIONS.md-compliant same-origin
# URLs — the upstream static handler serves $HERMES_WEBUI_EXTENSION_DIR under
# /extensions/.
export HERMES_WEBUI_EXTENSION_SCRIPT_URLS="/extensions/doh-integrations.js,/extensions/doh-webapps.js,/extensions/doh-permissions.js"
export HERMES_WEBUI_EXTENSION_STYLESHEET_URLS="/extensions/doh-integrations.css,/extensions/doh-webapps.css,/extensions/doh-permissions.css"

CADDY_PORT=8787
SYSTEM_PROCESS_COMPOSE_PORT=9956
WEBAPPS_PROCESS_COMPOSE_PORT=9957
PROCESS_COMPOSE_ROOT=/workspace/.config/process-compose

CADDY_PID=""
SYSTEM_PROCESS_COMPOSE_PID=""
WEBAPPS_PROCESS_COMPOSE_PID=""

die() {
    echo "FATAL: $*" >&2
    exit 1
}

cleanup() {
    set +e
    for pid in "$SYSTEM_PROCESS_COMPOSE_PID" "$WEBAPPS_PROCESS_COMPOSE_PID" "$CADDY_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
}

trap cleanup EXIT INT TERM

sandbox_seed() {
    "$HERMES_WEBUI_PYTHON" "${DOH_RUNTIME_DIR}/sandbox_seed.py" \
        || die "failed to seed sandbox runtime state"
}

start_system_process_compose() {
    echo "[webui] Starting system process-compose on 127.0.0.1:${SYSTEM_PROCESS_COMPOSE_PORT}..."
    process-compose \
        --log-file "${PROCESS_COMPOSE_ROOT}/system/process-compose.log" \
        --log-no-color \
        up \
        --config "${PROCESS_COMPOSE_ROOT}/system/process-compose.yaml" \
        --port "$SYSTEM_PROCESS_COMPOSE_PORT" \
        --address 127.0.0.1 \
        --tui=false \
        --keep-project \
        2>&1 &
    SYSTEM_PROCESS_COMPOSE_PID=$!
}

start_webapps_process_compose() {
    echo "[webui] Starting webapps process-compose on 127.0.0.1:${WEBAPPS_PROCESS_COMPOSE_PORT}..."
    process-compose \
        --log-file "${PROCESS_COMPOSE_ROOT}/webapps/process-compose.log" \
        --log-no-color \
        up \
        --config "${PROCESS_COMPOSE_ROOT}/webapps/process-compose.yaml" \
        --port "$WEBAPPS_PROCESS_COMPOSE_PORT" \
        --address 127.0.0.1 \
        --tui=false \
        --keep-project \
        2>&1 &
    WEBAPPS_PROCESS_COMPOSE_PID=$!
}

start_caddy() {
    echo "[webui] Starting Caddy on :${CADDY_PORT}..."
    caddy run --config "${DOH_RUNTIME_DIR}/webapps/Caddyfile" --adapter caddyfile --watch 2>&1 &
    CADDY_PID=$!
}

bootstrap_admin_webapp() {
    # Runs *before* webapps process-compose starts. --bootstrap-enabled writes
    # an enabled YAML entry plus the route, then `process-compose up` brings __admin up
    # alongside any user webapps from prior boots. Talking to the daemon at
    # this stage would hang: the `project update` CLI client (subprocess.run)
    # doesn't return promptly during initial supervision, blocking webui.sh forever.
    # --if-missing is the cold-restart idempotence: if __admin is already
    # in the YAML, skip silently.
    webapps create __admin --if-missing --bootstrap-enabled \
        --command "$HERMES_WEBUI_PYTHON -m admin" \
        --cwd "${DOH_RUNTIME_DIR}/webapps"
}

bootstrap_gateway_process() {
    # The gateway process: Hermes's headless cron ticker + loopback API
    # server, plus any messaging-platform bindings activated by env vars
    # the broker writes into ${HERMES_HOME}/.env (see
    # docs/gateway_env_and_restart_design.md).
    #
    # Goes through system process-compose so the broker can hot-restart it
    # via REST when vault credentials change without bouncing the whole
    # container. --replace clears any stale gateway.pid left over from a
    # previous container run that crashed before atexit could remove it.
    #
    # API_SERVER_ENABLED / API_SERVER_KEY are exported at container scope (top
    # of this file) so both the gateway and webapp children inherit them.
    "$HERMES_WEBUI_PYTHON" "${DOH_RUNTIME_DIR}/webapps/system_process_compose_seed.py" system.gateway \
        --command "$HERMES_WEBUI_PYTHON -m hermes_cli.main gateway run --replace -v" \
        --cwd "$HERMES_WEBUI_AGENT_DIR"
}

bootstrap_webui_process() {
    # Keeping WebUI under system process-compose lets the broker restart only
    # WebUI after rewriting connected-provider env such as GITHUB_TOKEN. A
    # targeted restart spawns a fresh server.py, which imports api.config, whose
    # module body runs init_profile_state() -> _reload_dotenv(${HERMES_HOME}),
    # loading the broker's latest .env into os.environ (override) on every start.
    "$HERMES_WEBUI_PYTHON" "${DOH_RUNTIME_DIR}/webapps/system_process_compose_seed.py" system.webui \
        --command "$HERMES_WEBUI_PYTHON server.py" \
        --cwd "$HERMES_WEBUI_DIR"
}

wait_for_webui() {
    echo "[webui] Waiting for WebUI..."
    for _ in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${HERMES_WEBUI_PORT}/health" >/dev/null 2>&1; then
            echo "[webui] WebUI is healthy."
            return
        fi
        if ! kill -0 "$SYSTEM_PROCESS_COMPOSE_PID" 2>/dev/null; then
            echo "[webui] FATAL: system process-compose exited before WebUI became healthy." >&2
            wait "$SYSTEM_PROCESS_COMPOSE_PID"
            exit $?
        fi
        if ! kill -0 "$WEBAPPS_PROCESS_COMPOSE_PID" 2>/dev/null; then
            echo "[webui] FATAL: webapps process-compose exited before WebUI became healthy." >&2
            wait "$WEBAPPS_PROCESS_COMPOSE_PID"
            exit $?
        fi
        sleep 2
    done
    echo "[webui] FATAL: WebUI did not become healthy in time." >&2
    exit 1
}

main() {
    sandbox_seed
    bootstrap_admin_webapp
    bootstrap_gateway_process
    bootstrap_webui_process
    start_system_process_compose
    start_webapps_process_compose
    wait_for_webui
    start_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} system_process_compose=${SYSTEM_PROCESS_COMPOSE_PID} webapps_process_compose=${WEBAPPS_PROCESS_COMPOSE_PID}."
    wait -n "$CADDY_PID" "$SYSTEM_PROCESS_COMPOSE_PID" "$WEBAPPS_PROCESS_COMPOSE_PID"
    exit $?
}

main "$@"
