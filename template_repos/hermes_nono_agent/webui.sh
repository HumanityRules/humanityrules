#!/bin/bash
set -euo pipefail

HERMES_HOME="/home/hermeswebui/.hermes"
HERMES_AGENT_DIR="$HERMES_HOME/hermes-agent"
VENV_DIR="/app/venv"

source "$VENV_DIR/bin/activate"

HERMES_HOME="$HERMES_HOME" python3 -c "
import sys
sys.path.insert(0, '$HERMES_AGENT_DIR')
from tools.skills_sync import sync_skills
r = sync_skills(quiet=True)
print(f'[webui:skills] copied={len(r[\"copied\"])} updated={len(r[\"updated\"])} skipped={r[\"skipped\"]} user_modified={len(r[\"user_modified\"])} total_bundled={r[\"total_bundled\"]}')
" || echo "[webui:skills] sync failed (non-fatal)"

cd /app
python server.py 2>&1 &
WEBUI_PID=$!

echo "[webui] Waiting for WebUI to start..."
for _ in $(seq 1 60); do
    if curl -sf http://localhost:8787/health >/dev/null 2>&1; then
        echo "[webui] WebUI is healthy."
        break
    fi
    if ! kill -0 "$WEBUI_PID" 2>/dev/null; then
        echo "[webui] FATAL: WebUI exited before becoming healthy." >&2
        wait "$WEBUI_PID"
        exit $?
    fi
    sleep 2
done

echo "[webui] Waiting on WebUI (PID=$WEBUI_PID)."
wait "$WEBUI_PID"
