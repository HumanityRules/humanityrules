#!/bin/bash
# Start the Hermes WebUI and (optionally) the messaging gateway side by side.
# The WebUI (hermeswebui_init.bash -> server.py) handles the web interface.
# The gateway (gateway/run.py) handles Slack/Discord/Telegram integrations.
#
# On every boot, we also install the hermes-agent[bedrock] extra into the
# shared venv. It's tiny (boto3 only, today) and having it present means the
# bedrock provider just works without a rebuild when users switch to it.
#
# If any launched process exits, the whole container exits so ECS can restart it.

HERMES_AGENT_DIR="/home/hermeswebui/.hermes/hermes-agent"
VENV_DIR="/app/venv"
BEDROCK_DEPS_MARKER="$VENV_DIR/.bedrock_deps_installed"
GATEWAY_DEPS_MARKER="$VENV_DIR/.slack_deps_installed"

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
# hermes-webui + hermes-agent[honcho] deps (touching $VENV_DIR/.deps_installed
# when done), then runs `python server.py` which blocks forever.
#
# We route its stdout+stderr through a `grep -v` process substitution that
# drops successful /health access-log lines (a ~2/sec firehose between the
# Docker HEALTHCHECK and the ALB target-group probe). Non-200 /health lines
# still pass through, so real health failures remain visible. Process
# substitution (not a pipe) is used so `$!` stays the init script's PID —
# with a pipe, `$!` would become grep's PID and we'd lose exit-code tracking.
/hermeswebui_init.bash > >(grep --line-buffered -v '"path": "/health", "status": 200') 2>&1 &
WEBUI_PID=$!

# Wait for the venv and base deps to be ready before we install extras into it.
echo "[start] Waiting for venv deps to be installed..."
for i in $(seq 1 120); do
    if [ -f "$VENV_DIR/.deps_installed" ]; then
        break
    fi
    sleep 2
done

if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    echo "[start] ERROR: venv deps not ready after 240s — aborting."
    kill $WEBUI_PID 2>/dev/null
    wait $WEBUI_PID 2>/dev/null
    exit 1
fi

source "$VENV_DIR/bin/activate"

# Install hermes-agent extras unconditionally:
#   [bedrock] -> boto3, needed whenever DOH_LLM_PROVIDER=bedrock.
#   [mcp]     -> the mcp python package; without it tools.mcp_tool is a no-op
#               and the mcp_servers: block in config.yaml is silently ignored.
#               Required because the sidecar MCP server ships by default in
#               config.yaml.template.
if [ ! -f "$BEDROCK_DEPS_MARKER" ]; then
    echo "[start] Installing hermes-agent[bedrock,mcp] extras..."
    uv pip install "$HERMES_AGENT_DIR[bedrock,mcp]" \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    touch "$BEDROCK_DEPS_MARKER"
else
    echo "[start] Extras already installed — skipping."
fi

if [ "$SLACK_ENABLED" -eq 0 ]; then
    echo "[start] Waiting on WebUI (PID=$WEBUI_PID)."
    wait $WEBUI_PID
    exit $?
fi

# Install Slack dependencies if not already present.
if [ ! -f "$GATEWAY_DEPS_MARKER" ]; then
    echo "[start] Installing Slack gateway dependencies..."
    uv pip install "slack-bolt>=1.18.0,<2" "slack-sdk>=3.27.0,<4" \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    touch "$GATEWAY_DEPS_MARKER"
else
    echo "[start] Slack deps already installed — skipping."
fi

# Wait a few seconds for the WebUI server to bind its port.
echo "[start] Waiting for WebUI to start..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8787/health >/dev/null 2>&1; then
        echo "[start] WebUI is healthy."
        break
    fi
    sleep 2
done

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
