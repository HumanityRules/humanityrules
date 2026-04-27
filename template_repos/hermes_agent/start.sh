#!/bin/bash
# Start the Hermes WebUI and (optionally) the messaging gateway side by side.
# The WebUI (hermeswebui_init.bash -> server.py) handles the web interface.
# The gateway (gateway/run.py) handles Slack/Discord/Telegram integrations.
#
# Dependency ordering: hermeswebui_init.bash creates /app/venv, runs its pip
# installs, touches .deps_installed, then execs `python server.py` — with no
# join point in between. Any "install more deps after it starts" runs AFTER
# server.py has already imported modules. tools.mcp_tool in particular caches
# `_MCP_AVAILABLE = try import mcp except False` at module-load time, so a late
# mcp install never takes effect and no mcp_{server}_{tool} handlers register.
# Fix: patch the init script's own `uv pip install` line to pull in the extras
# we need ([bedrock,mcp] for hermes-agent, slack-bolt/slack-sdk for gateway),
# so all required packages are in the venv before server.py ever runs.
#
# If any launched process exits, the whole container exits so ECS can restart it.

HERMES_AGENT_DIR="/home/hermeswebui/.hermes/hermes-agent"
VENV_DIR="/app/venv"

# Patch hermeswebui_init.bash to fold our extras into its own pip install.
# Adds hermes-agent[bedrock,mcp] plus slack-bolt/slack-sdk unconditionally —
# always installing Slack deps (they're small) is simpler than gating on token
# presence. Idempotent: the marker string is our own grep anchor.
#
# We run as the non-root hermeswebui user, so we can't write to `/` (where
# /hermeswebui_init.bash lives) and `sed -i` fails to create its temp file
# there. Write the patched script to a writable location and have start.sh
# invoke the patched copy instead.
WEBUI_INIT_PATCHED=/tmp/hermeswebui_init.patched.bash
if ! grep -q 'hermes-agent\[honcho,bedrock,mcp\]' "$WEBUI_INIT_PATCHED" 2>/dev/null; then
    sed 's|"/home/hermeswebui/.hermes/hermes-agent\[honcho\]"|"/home/hermeswebui/.hermes/hermes-agent[honcho,bedrock,mcp]" "slack-bolt>=1.18.0,<2" "slack-sdk>=3.27.0,<4"|' /hermeswebui_init.bash > "$WEBUI_INIT_PATCHED"
    chmod +x "$WEBUI_INIT_PATCHED"
fi

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
# hermes-webui + hermes-agent deps (touching $VENV_DIR/.deps_installed when
# done), then runs `python server.py` which blocks forever.
#
# We route its stdout+stderr through a `grep -v` process substitution that
# drops successful /health access-log lines (a ~2/sec firehose between the
# Docker HEALTHCHECK and the ALB target-group probe). Non-200 /health lines
# still pass through, so real health failures remain visible. Process
# substitution (not a pipe) is used so `$!` stays the init script's PID —
# with a pipe, `$!` would become grep's PID and we'd lose exit-code tracking.
"$WEBUI_INIT_PATCHED" > >(grep --line-buffered -v '"path": "/health", "status": 200') 2>&1 &
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
