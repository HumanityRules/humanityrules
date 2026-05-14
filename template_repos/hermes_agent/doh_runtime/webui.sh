#!/bin/bash
set -euo pipefail

WEBUI_PORT=8787
WEBUI_PID=""

: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"

start_webui() {
    cd "$HERMES_WEBUI_DIR"
    "$HERMES_WEBUI_PYTHON" server.py 2>&1 &
    WEBUI_PID=$!
}

wait_for_webui() {
    echo "[webui] Waiting for WebUI to start..."
    for _ in $(seq 1 60); do
        if curl -sf "http://localhost:${WEBUI_PORT}/health" >/dev/null 2>&1; then
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

main() {
    start_webui
    wait_for_webui

    echo "[webui] Waiting on WebUI (PID=$WEBUI_PID)."
    wait "$WEBUI_PID"
}

main "$@"
