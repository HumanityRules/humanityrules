#!/bin/bash
# Start the Hermes WebUI and (optionally) the messaging gateway side by side.
# The WebUI (hermeswebui_init.bash -> server.py) handles the web interface.
# The gateway (gateway/run.py) handles Slack/Discord/Telegram integrations.
#
# Platform dependencies (mcp, boto3, slack-{bolt,sdk}) are appended to the
# webui's requirements.txt in the Dockerfile, so they're installed by
# hermeswebui_init.bash before server.py starts.
#
# If any launched process exits, the whole container exits so ECS can restart it.

HERMES_HOME="/home/hermeswebui/.hermes"
HERMES_AGENT_DIR="$HERMES_HOME/hermes-agent"
VENV_DIR="/app/venv"
HERMES_EFS_PERSIST=/mnt/hermes-persistent

SLACK_ENABLED=0
if [ -n "$SLACK_BOT_TOKEN" ] || [ -n "$SLACK_APP_TOKEN" ]; then
    SLACK_ENABLED=1
fi

if [ "$SLACK_ENABLED" -eq 1 ]; then
    echo "[start] Slack tokens detected — starting WebUI + gateway."
else
    echo "[start] No Slack tokens found — running WebUI only."
fi

# Start the WebUI init script in the background. It creates /app/venv, installs
# hermes-webui + hermes-agent deps, then runs `python server.py` which blocks
# forever.
/hermeswebui_init.bash 2>&1 &
WEBUI_PID=$!

# Wait for WebUI to bind its port. Healthy implies hermeswebui_init.bash has
# finished installing into $VENV_DIR, so we can safely `source` it below.
echo "[start] Waiting for WebUI to start..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8787/health >/dev/null 2>&1; then
        echo "[start] WebUI is healthy."
        break
    fi
    if ! kill -0 "$WEBUI_PID" 2>/dev/null; then
        echo "[start] FATAL: WebUI exited before becoming healthy." >&2
        wait $WEBUI_PID
        exit $?
    fi
    sleep 2
done

# Background sync: mirror ~/.hermes to EFS every 10s so chat history, memories,
# and settings survive task replacement. Durability window is ~10s (plus one
# final sync on SIGTERM). hermes-agent/ is excluded — regenerated from image
# each boot, would waste ~700MB of NFS traffic. See entrypoint.sh for the
# matching restore-on-boot logic.
(
    while true; do
        _t0=$(date +%s%3N)
        rsync -a --delete --exclude='hermes-agent/' \
            "$HERMES_HOME/" "$HERMES_EFS_PERSIST/" 2>/dev/null
        echo "[start:sync] mirror pass $(($(date +%s%3N) - _t0))ms"
        sleep 10
    done
) &
SYNC_PID=$!
echo "[start] EFS mirror loop started (PID=$SYNC_PID, interval=10s)."

# SIGTERM from ECS → final sync before exit, shrinking the durability window
# to zero on graceful stops. Unconditional kill of the sync loop so it can't
# race with the final rsync.
trap '
    kill $SYNC_PID 2>/dev/null
    echo "[start] Final EFS sync on shutdown..."
    _t0=$(date +%s%3N)
    rsync -a --delete --exclude="hermes-agent/" "$HERMES_HOME/" "$HERMES_EFS_PERSIST/" 2>/dev/null
    echo "[start] Final sync done in $(($(date +%s%3N) - _t0))ms."
' EXIT

source "$VENV_DIR/bin/activate"

# Seed bundled skills into ~/.hermes/skills/. Upstream wires sync_skills into
# `hermes update` and `hermes profile create`, neither of which our deploy
# flow goes through. Without this call the WebUI skills panel stays empty.
HERMES_HOME="$HERMES_HOME" python3 -c "
import sys
sys.path.insert(0, '$HERMES_AGENT_DIR')
from tools.skills_sync import sync_skills
r = sync_skills(quiet=True)
print(f'[start:skills] copied={len(r[\"copied\"])} updated={len(r[\"updated\"])} skipped={r[\"skipped\"]} user_modified={len(r[\"user_modified\"])} total_bundled={r[\"total_bundled\"]}')
" || echo "[start:skills] sync failed (non-fatal)"

if [ "$SLACK_ENABLED" -eq 0 ]; then
    echo "[start] Waiting on WebUI (PID=$WEBUI_PID)."
    wait $WEBUI_PID
    exit $?
fi

echo "[start] Starting Hermes gateway..."
cd "$HERMES_AGENT_DIR"
python -m gateway.run &
GATEWAY_PID=$!

echo "[start] WebUI PID=$WEBUI_PID, Gateway PID=$GATEWAY_PID"

# Wait for either process to exit. If one dies, kill the other and exit.
wait -n $WEBUI_PID $GATEWAY_PID 2>/dev/null
EXIT_CODE=$?

echo "[start] A process exited with code $EXIT_CODE — shutting down."
kill $WEBUI_PID $GATEWAY_PID 2>/dev/null
wait 2>/dev/null
exit $EXIT_CODE
