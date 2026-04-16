#!/bin/bash
# Start the Hermes WebUI and the messaging gateway side by side.
# The WebUI (hermeswebui_init.bash -> server.py) handles the web interface.
# The gateway (gateway/run.py) handles Slack/Discord/Telegram integrations.
#
# If either process exits, the whole container exits so ECS can restart it.

HERMES_AGENT_DIR="/home/hermeswebui/.hermes/hermes-agent"
VENV_DIR="/app/venv"
GATEWAY_DEPS_MARKER="$VENV_DIR/.slack_deps_installed"

# Only start the gateway if Slack tokens are configured.
if [ -z "$SLACK_BOT_TOKEN" ] && [ -z "$SLACK_APP_TOKEN" ]; then
    echo "[start_with_gateway] No Slack tokens found — running WebUI only."
    exec /hermeswebui_init.bash
fi

echo "[start_with_gateway] Slack tokens detected — starting WebUI + gateway."

# Start the WebUI init script in the background.
# It installs deps, then runs python server.py (which blocks forever).
/hermeswebui_init.bash &
WEBUI_PID=$!

# Wait for the venv and base deps to be ready (the init script creates this marker).
echo "[start_with_gateway] Waiting for venv deps to be installed..."
for i in $(seq 1 120); do
    if [ -f "$VENV_DIR/.deps_installed" ]; then
        break
    fi
    sleep 2
done

if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    echo "[start_with_gateway] ERROR: venv deps not ready after 240s — aborting gateway."
    wait $WEBUI_PID
    exit 1
fi

# Activate the venv so we can pip install and run the gateway.
source "$VENV_DIR/bin/activate"

# Install Slack dependencies if not already present.
if [ ! -f "$GATEWAY_DEPS_MARKER" ]; then
    echo "[start_with_gateway] Installing Slack gateway dependencies..."
    uv pip install "slack-bolt>=1.18.0,<2" "slack-sdk>=3.27.0,<4" \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    touch "$GATEWAY_DEPS_MARKER"
else
    echo "[start_with_gateway] Slack deps already installed — skipping."
fi

# Wait a few seconds for the WebUI server to bind its port.
echo "[start_with_gateway] Waiting for WebUI to start..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8787/health >/dev/null 2>&1; then
        echo "[start_with_gateway] WebUI is healthy."
        break
    fi
    sleep 2
done

# Start the gateway.
echo "[start_with_gateway] Starting Hermes gateway..."
cd "$HERMES_AGENT_DIR"
python -m gateway.run &
GATEWAY_PID=$!

echo "[start_with_gateway] WebUI PID=$WEBUI_PID, Gateway PID=$GATEWAY_PID"

# Wait for either process to exit. If one dies, kill the other and exit.
wait -n $WEBUI_PID $GATEWAY_PID 2>/dev/null
EXIT_CODE=$?

echo "[start_with_gateway] A process exited with code $EXIT_CODE — shutting down."
kill $WEBUI_PID $GATEWAY_PID 2>/dev/null
wait 2>/dev/null
exit $EXIT_CODE
