#!/bin/bash
# Steady-state supervisor — runs under nono (credential-injection sandbox).
#
# Pre-reqs that entrypoint.sh guarantees before we get here:
#   - /app is populated from /apptoo.
#   - /app/venv exists with all deps (WebUI, hermes-agent[honcho,bedrock],
#     slack-sdk, slack-bolt) installed.
#   - /tmp/doh-secrets/ was staged by entrypoint.sh for nono to read at
#     startup. nono has already loaded the values into zeroized memory.
#   - DOH_SLACK_ENABLED is set from the real tokens; the child's
#     $SLACK_*_TOKEN env vars may hold non-empty phantom proxy tokens, so
#     don't use them as an "is Slack configured" signal.
#
# If any launched process exits, the whole container exits so ECS restarts it.

set -e

HERMES_AGENT_DIR="/home/hermeswebui/.hermes/hermes-agent"

# Scrub nono's secret staging dir the moment we're inside the sandbox. nono
# loaded the values into its parent-process memory (zeroized on drop) before
# this script ever started, so the files are dead weight by now. Removing
# them closes the only remaining plaintext surface. Safe when the dir doesn't
# exist (Bedrock path, local-dev path).
rm -rf /tmp/doh-secrets 2>/dev/null || true

SLACK_ENABLED="${DOH_SLACK_ENABLED:-0}"

if [ "$SLACK_ENABLED" -eq 1 ]; then
    echo "[start] Slack tokens detected — starting WebUI + gateway."
else
    echo "[start] No Slack tokens — running WebUI only."
fi

# shellcheck disable=SC1091
source /app/venv/bin/activate

cd /app

if [ "$SLACK_ENABLED" -eq 0 ]; then
    # Single-process mode: WebUI runs in foreground, its exit is our exit.
    exec python server.py
fi

# Multi-process mode: WebUI + Slack gateway. If either dies, bring the other
# down and exit so ECS restarts the whole task (clean state beats partial).
python server.py &
WEBUI_PID=$!

# Give the WebUI a moment to bind :8787 before the gateway connects — the
# gateway's first action is to call into the WebUI for config validation.
echo "[start] Waiting for WebUI to start..."
for _ in $(seq 1 30); do
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

wait -n $WEBUI_PID $GATEWAY_PID 2>/dev/null
EXIT_CODE=$?

echo "[start] A process exited with code $EXIT_CODE — shutting down."
kill $WEBUI_PID $GATEWAY_PID 2>/dev/null
wait 2>/dev/null
exit $EXIT_CODE
