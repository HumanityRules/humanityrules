#!/bin/bash
set -euo pipefail

# Inside nono. Three direct children run here as hermeswebui:
#   1. system process-compose on :9956 — supervises system.webui and system.gateway.
#   2. app-workloads process-compose on :9957 — supervises Web Apps and Widget backends.
#   3. Caddy on :8787 — policy-proxy forwards here; routes WebUI, Web Apps, and Widgets.
#
# The Hermes WebUI and gateway are supervised by process-compose so the broker
# can restart them in place when managed integration env changes.
#
# If a direct child exits, we kill the others and exit.

: "${HUMR_RUNTIME_DIR:?HUMR_RUNTIME_DIR must be set}"
: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${AWS_DEFAULT_REGION:?AWS_DEFAULT_REGION must be set}"
: "${HERMES_WEBUI_AGENT_DIR:?HERMES_WEBUI_AGENT_DIR must be set}"
: "${HERMES_WEBUI_DEFAULT_WORKSPACE:?HERMES_WEBUI_DEFAULT_WORKSPACE must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PORT:?HERMES_WEBUI_PORT must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

# Loopback Hermes agent API (gateway's api_server platform, 127.0.0.1:8642).
# Enabled for in-sandbox app workloads that call a full Hermes agent at
# http://127.0.0.1:8642/v1/. Upstream *requires* a bearer key even for
# loopback binds, so we mint a fixed one here. This is NOT a secret: it only
# guards a loopback port inside this nono sandbox, where every process already
# shares a uid, venv, and workspace and fully trusts its siblings. Exporting it
# here makes it visible to the gateway and every process-compose child.
export API_SERVER_ENABLED=true
export API_SERVER_KEY=humr-loopback-gateway-key

# Platform environment facts (integrations flow, GitHub auth proxy, package
# managers) for the agent's system prompt, via Hermes's embedder hook. Kept
# image-owned at /opt/hermes/ENVIRONMENT.md so every redeploy updates it —
# unlike SOUL.md, which is seeded once into HERMES_HOME and user-editable.
if [ -s /opt/hermes/ENVIRONMENT.md ]; then
    HERMES_ENVIRONMENT_HINT="$(cat /opt/hermes/ENVIRONMENT.md)"
    export HERMES_ENVIRONMENT_HINT
fi

CADDY_PORT=8787
SYSTEM_PROCESS_COMPOSE_PORT=9956
APP_WORKLOADS_PROCESS_COMPOSE_PORT=9957
PROCESS_COMPOSE_ROOT=/workspace/.config/process-compose

CADDY_PID=""
SYSTEM_PROCESS_COMPOSE_PID=""
APP_WORKLOADS_PROCESS_COMPOSE_PID=""

die() {
    echo "FATAL: $*" >&2
    exit 1
}

cleanup() {
    set +e
    for pid in "$SYSTEM_PROCESS_COMPOSE_PID" "$APP_WORKLOADS_PROCESS_COMPOSE_PID" "$CADDY_PID"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null
        fi
    done
}

trap cleanup EXIT INT TERM

sandbox_seed() {
    "$HERMES_WEBUI_PYTHON" "${HUMR_RUNTIME_DIR}/sandbox_seed.py" \
        || die "failed to seed sandbox runtime state"
}

ensure_caddy_fragments() {
    mkdir -p /workspace/.config/caddy
    if [ ! -e /workspace/.config/caddy/webapps.caddy ]; then
        echo "# no Web App routes" > /workspace/.config/caddy/webapps.caddy
    fi
    if [ ! -e /workspace/.config/caddy/widgets.caddy ]; then
        echo "# no Widget routes" > /workspace/.config/caddy/widgets.caddy
    fi
}

reconcile_widgets() {
    widgets apply --all --bootstrap \
        || echo "[webui] Widget reconciliation failed (non-fatal); continuing with last-good routes."
}

reconcile_platform_skills() {
    "$HERMES_WEBUI_PYTHON" "${HUMR_RUNTIME_DIR}/reconcile_platform_skills.py" \
        || die "failed to reconcile platform skills"
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

start_app_workloads_compose() {
    echo "[webui] Starting app-workloads process-compose on 127.0.0.1:${APP_WORKLOADS_PROCESS_COMPOSE_PORT}..."
    process-compose \
        --log-file "${PROCESS_COMPOSE_ROOT}/app-workloads/process-compose.log" \
        --log-no-color \
        up \
        --config "${PROCESS_COMPOSE_ROOT}/app-workloads/process-compose.yaml" \
        --port "$APP_WORKLOADS_PROCESS_COMPOSE_PORT" \
        --address 127.0.0.1 \
        --tui=false \
        --keep-project \
        2>&1 &
    APP_WORKLOADS_PROCESS_COMPOSE_PID=$!
}

start_caddy() {
    echo "[webui] Starting Caddy on :${CADDY_PORT}..."
    caddy run --config "${HUMR_RUNTIME_DIR}/http_router/Caddyfile" --adapter caddyfile --watch 2>&1 &
    CADDY_PID=$!
}

bootstrap_admin_webapp() {
    # Runs before the app-workloads supervisor starts. --bootstrap-enabled writes
    # webapp.__admin plus its route, then `process-compose up` brings it up
    # alongside registered Web Apps and Widget backends. Talking to the daemon at
    # this stage would hang: the `project update` CLI client (subprocess.run)
    # doesn't return promptly during initial supervision, blocking webui.sh forever.
    # --if-missing is the cold-restart idempotence: if __admin is already
    # in the YAML, skip silently.
    webapps create __admin --if-missing --bootstrap-enabled \
        --command "$HERMES_WEBUI_PYTHON -m admin" \
        --cwd "${HUMR_RUNTIME_DIR}/webapps"
}

seed_example_webapps() {
    # Install bundled example webapps (the image-baked catalog under
    # /opt/humr/runtime/webapps/examples) into a fresh agent. Runs before the
    # app-workloads supervisor starts, so the seeder registers via `webapps create
    # --bootstrap-enabled` — no daemon RPC, same as bootstrap_admin_webapp. A
    # per-install marker under /workspace/webapps/.seeded/ holds each example's
    # update policy: "refresh" (default) re-syncs it from the image every boot,
    # "freeze" leaves the user's copy alone; a deleted example is not resurrected
    # (see seed_example_webapps.py). Best-effort: never fail boot over an example.
    "$HERMES_WEBUI_PYTHON" "${HUMR_RUNTIME_DIR}/webapps/seed_example_webapps.py" \
        || echo "[webui] example webapp seeding failed (non-fatal); continuing."
}

bootstrap_gateway_process() {
    # The gateway process: Hermes's headless cron ticker + loopback API
    # server, plus any messaging-platform bindings activated by env vars
    # the broker writes into ${HERMES_HOME}/.env (see
    # docs/integrations/gateway_env_and_restart_design.md).
    #
    # Goes through system process-compose so the broker can hot-restart it
    # via REST when vault credentials change without bouncing the whole
    # container. --replace clears any stale gateway.pid left over from a
    # previous container run that crashed before atexit could remove it.
    #
    # API_SERVER_ENABLED / API_SERVER_KEY are exported at container scope (top
    # of this file) so both the gateway and webapp children inherit them.
    "$HERMES_WEBUI_PYTHON" "${HUMR_RUNTIME_DIR}/process_supervisor/system_process_seed.py" system.gateway \
        --command "$HERMES_WEBUI_PYTHON -m hermes_cli.main gateway run --replace -v" \
        --cwd "$HERMES_WEBUI_AGENT_DIR"
}

bootstrap_webui_process() {
    # Keeping WebUI under system process-compose lets the broker restart only
    # WebUI after rewriting connected-provider env such as GITHUB_TOKEN. A
    # targeted restart spawns a fresh server.py, which imports api.config, whose
    # module body runs init_profile_state() -> _reload_dotenv(${HERMES_HOME}),
    # loading the broker's latest .env into os.environ (override) on every start.
    "$HERMES_WEBUI_PYTHON" "${HUMR_RUNTIME_DIR}/process_supervisor/system_process_seed.py" system.webui \
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
        if ! kill -0 "$APP_WORKLOADS_PROCESS_COMPOSE_PID" 2>/dev/null; then
            echo "[webui] FATAL: app-workloads process-compose exited before WebUI became healthy." >&2
            wait "$APP_WORKLOADS_PROCESS_COMPOSE_PID"
            exit $?
        fi
        sleep 2
    done
    echo "[webui] FATAL: WebUI did not become healthy in time." >&2
    exit 1
}

main() {
    reconcile_platform_skills
    sandbox_seed
    ensure_caddy_fragments
    reconcile_widgets
    bootstrap_admin_webapp
    seed_example_webapps
    bootstrap_gateway_process
    bootstrap_webui_process
    start_system_process_compose
    start_app_workloads_compose
    wait_for_webui
    start_caddy

    echo "[webui] All services up. caddy=${CADDY_PID} system_process_compose=${SYSTEM_PROCESS_COMPOSE_PID} app_workloads_process_compose=${APP_WORKLOADS_PROCESS_COMPOSE_PID}."
    wait -n "$CADDY_PID" "$SYSTEM_PROCESS_COMPOSE_PID" "$APP_WORKLOADS_PROCESS_COMPOSE_PID"
    exit $?
}

main "$@"
