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

HERMES_AGENT_DIR="/home/hermeswebui/.hermes/hermes-agent"
VENV_DIR="/app/venv"

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
#
# We route its stdout+stderr through a `grep -v` process substitution that
# drops successful /health access-log lines (a ~2/sec firehose between the
# Docker HEALTHCHECK and the ALB target-group probe). Non-200 /health lines
# still pass through, so real health failures remain visible. Process
# substitution (not a pipe) is used so `$!` stays the init script's PID —
# with a pipe, `$!` would become grep's PID and we'd lose exit-code tracking.
/hermeswebui_init.bash > >(grep --line-buffered -v '"path": "/health", "status": 200') 2>&1 &
WEBUI_PID=$!

if [ "$SLACK_ENABLED" -eq 0 ]; then
    echo "[start] Waiting on WebUI (PID=$WEBUI_PID)."
    wait $WEBUI_PID
    exit $?
fi

# Wait for the WebUI to bind its port before we start the gateway — the
# gateway imports server modules that rely on WebUI state being initialized.
echo "[start] Waiting for WebUI to start..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8787/health >/dev/null 2>&1; then
        echo "[start] WebUI is healthy."
        break
    fi
    sleep 2
done

source "$VENV_DIR/bin/activate"

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
