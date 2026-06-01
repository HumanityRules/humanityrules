#!/usr/bin/env bash
set -euo pipefail

MODEL="${ASK_CODEX_MODEL:-gpt-5.5}"
EFFORT="${ASK_CODEX_EFFORT:-xhigh}"
SANDBOX="${ASK_CODEX_SANDBOX:-read-only}"
TIMEOUT_SECONDS="${ASK_CODEX_TIMEOUT_SECONDS:-900}"
FAST_MODE="${ASK_CODEX_FAST:-1}"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found on PATH" >&2
  exit 127
fi

case "$EFFORT" in
  low|medium|high|xhigh|max) ;;
  *)
    echo "Invalid ASK_CODEX_EFFORT='$EFFORT' (expected low, medium, high, xhigh, or max)" >&2
    exit 2
    ;;
esac

case "$SANDBOX" in
  read-only|workspace-write|danger-full-access) ;;
  *)
    echo "Invalid ASK_CODEX_SANDBOX='$SANDBOX' (expected read-only, workspace-write, or danger-full-access)" >&2
    exit 2
    ;;
esac

case "$FAST_MODE" in
  1|true|TRUE|yes|YES|on|ON)
    DEFAULT_SERVICE_TIER="fast"
    ;;
  0|false|FALSE|no|NO|off|OFF)
    DEFAULT_SERVICE_TIER=""
    ;;
  *)
    echo "Invalid ASK_CODEX_FAST='$FAST_MODE' (expected 1/0, true/false, yes/no, or on/off)" >&2
    exit 2
    ;;
esac

SERVICE_TIER="${ASK_CODEX_SERVICE_TIER:-$DEFAULT_SERVICE_TIER}"

case "$SERVICE_TIER" in
  ''|fast|flex) ;;
  *)
    echo "Invalid ASK_CODEX_SERVICE_TIER='$SERVICE_TIER' (expected fast, flex, or empty)" >&2
    exit 2
    ;;
esac

case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*)
    echo "Invalid ASK_CODEX_TIMEOUT_SECONDS='$TIMEOUT_SECONDS' (expected integer seconds)" >&2
    exit 2
    ;;
esac

TMPDIR_ROOT="${TMPDIR:-/tmp}"
WORK_DIR="$(mktemp -d "${TMPDIR_ROOT%/}/ask-codex.XXXXXX")"
PROMPT_FILE="$WORK_DIR/prompt.md"
ANSWER_FILE="$WORK_DIR/answer.md"
LOG_FILE="$WORK_DIR/codex.log"
KEEP_WORK_DIR=0

cleanup() {
  if [ "$KEEP_WORK_DIR" -eq 0 ]; then
    rm -rf "$WORK_DIR"
  else
    echo "ask-codex kept debug output in $WORK_DIR" >&2
  fi
}
trap cleanup EXIT

if [ "$#" -gt 0 ] && [ "$1" != "-" ]; then
  if [ ! -f "$1" ]; then
    echo "Question file not found: $1" >&2
    exit 2
  fi
  cp "$1" "$PROMPT_FILE"
else
  cat > "$PROMPT_FILE"
fi

if [ ! -s "$PROMPT_FILE" ]; then
  echo "Question is empty" >&2
  exit 2
fi

CODEX_ARGS=(
  exec
  --ignore-user-config \
  --ephemeral \
  --skip-git-repo-check \
  --sandbox "$SANDBOX" \
  --model "$MODEL" \
  -c "model_reasoning_effort=\"$EFFORT\"" \
)

if [ -n "$SERVICE_TIER" ]; then
  CODEX_ARGS+=(-c "service_tier=\"$SERVICE_TIER\"")
fi

CODEX_ARGS+=(
  --output-last-message "$ANSWER_FILE" \
  -
)

codex "${CODEX_ARGS[@]}" < "$PROMPT_FILE" > "$LOG_FILE" 2>&1 &

CODEX_PID=$!
SECONDS_WAITED=0

while kill -0 "$CODEX_PID" >/dev/null 2>&1; do
  if [ "$SECONDS_WAITED" -ge "$TIMEOUT_SECONDS" ]; then
    kill "$CODEX_PID" >/dev/null 2>&1 || true
    sleep 2
    kill -9 "$CODEX_PID" >/dev/null 2>&1 || true
    wait "$CODEX_PID" >/dev/null 2>&1 || true
    KEEP_WORK_DIR=1
    echo "Codex timed out after ${TIMEOUT_SECONDS}s before producing a final answer." >&2
    if [ -s "$LOG_FILE" ]; then
      echo "Last Codex log lines:" >&2
      tail -80 "$LOG_FILE" >&2
    fi
    exit 124
  fi
  sleep 5
  SECONDS_WAITED=$((SECONDS_WAITED + 5))
done

set +e
wait "$CODEX_PID"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  KEEP_WORK_DIR=1
  echo "Codex failed with exit code $STATUS." >&2
  if [ -s "$LOG_FILE" ]; then
    echo "Last Codex log lines:" >&2
    tail -80 "$LOG_FILE" >&2
  fi
  exit "$STATUS"
fi

if [ ! -s "$ANSWER_FILE" ]; then
  KEEP_WORK_DIR=1
  echo "Codex completed but did not write a final answer." >&2
  if [ -s "$LOG_FILE" ]; then
    echo "Last Codex log lines:" >&2
    tail -80 "$LOG_FILE" >&2
  fi
  exit 1
fi

cat "$ANSWER_FILE"
