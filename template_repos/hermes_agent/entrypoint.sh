#!/bin/bash
set -e

HERMES_DIR="/home/hermeswebui/.hermes"

# DOH_LLM_* vars are DOH's own config, deliberately namespaced to avoid
# colliding with Hermes's HERMES_* env vars. They are consumed only by this
# entrypoint to generate config.yaml and .env; never exported to child processes.
# Values are Hermes-native (e.g. "custom" not "openai").
if [ -z "$DOH_LLM_PROVIDER" ] || [ -z "$DOH_LLM_MODEL" ]; then
    echo "FATAL: DOH_LLM_PROVIDER and DOH_LLM_MODEL must be set" >&2
    exit 1
fi

# Bedrock provider: set the standard AWS region vars and derive the
# bedrock-runtime base_url from the region. Credentials come from the ECS
# task role via the standard boto3 credential chain (AWS_CONTAINER_CREDENTIALS_
# RELATIVE_URI on Fargate), so no static keys are plumbed through.
if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    if [ -z "$AWS_BEDROCK_REGION" ]; then
        echo "FATAL: DOH_LLM_PROVIDER=bedrock requires AWS_BEDROCK_REGION" >&2
        exit 1
    fi
    export AWS_REGION="$AWS_BEDROCK_REGION"
    export AWS_DEFAULT_REGION="$AWS_BEDROCK_REGION"
    DOH_LLM_BASE_URL="https://bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com"
fi

# Auxiliary LLM (vision, compression, session_search, skills_hub, approval, mcp,
# flush_memories, web_extract). One shared config, fanned out into all 8 slots.
# Defaults to the main provider when unset so Bedrock users get a Bedrock aux.
# Reuses the main provider's API key (no separate aux key var).
: "${DOH_AUX_PROVIDER:=$DOH_LLM_PROVIDER}"
: "${DOH_AUX_MODEL:=$DOH_LLM_MODEL}"
: "${DOH_AUX_BASE_URL:=}"
if [ "$DOH_AUX_PROVIDER" = "bedrock" ]; then
    if [ -z "$AWS_BEDROCK_REGION" ]; then
        echo "FATAL: DOH_AUX_PROVIDER=bedrock requires AWS_BEDROCK_REGION" >&2
        exit 1
    fi
    DOH_AUX_BASE_URL="https://bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com"
fi

mkdir -p "$HERMES_DIR" "$HERMES_DIR/workspace"

TERMINAL_BACKEND="local"
TERMINAL_CWD="."
DOCKER_VOLUMES="[]"

# DOCKER_HOST points at the in-task DinD sidecar. The DinD sidecar mounts the
# workspace EFS at /workspace, so -v /workspace:/... in tool runs refers to that
# data on the sidecar, not a path in this Hermes container.
#
# We bind the same EFS source (/workspace on DinD) to two container paths in each
# tool run: /workspace (the canonical cwd) and /home/hermeswebui/.hermes/workspace
# (the path the agent learns from HERMES_WEBUI_DEFAULT_WORKSPACE and from the
# hermes parent container layout). Without the second bind, any command that
# references the ~/.hermes/workspace path inside the tool container writes to
# the tool container's ephemeral overlay instead of EFS.
if [ -n "${DOCKER_HOST}" ] && docker info >/dev/null 2>&1; then
    if [ -d "$HERMES_DIR/workspace" ]; then
        TERMINAL_BACKEND="docker"
        TERMINAL_CWD="/workspace"
        DOCKER_VOLUMES='["/workspace:/workspace", "/workspace:/home/hermeswebui/.hermes/workspace"]'
        echo "[entrypoint] Docker-backed Hermes tools enabled (DinD via DOCKER_HOST)."
    elif [ "$DOH_HERMES_REQUIRE_DOCKER" = "1" ]; then
        echo "FATAL: DOH_HERMES_REQUIRE_DOCKER=1 but ${HERMES_DIR}/workspace is missing" >&2
        exit 1
    fi
elif [ "$DOH_HERMES_REQUIRE_DOCKER" = "1" ]; then
    echo "FATAL: DOH_HERMES_REQUIRE_DOCKER=1 but DOCKER_HOST is not set or no Docker server at DOCKER_HOST" >&2
    exit 1
fi

# Generate config.yaml from template on first boot.
# Existing files (from a previous deploy on EFS) are never overwritten.
if [ ! -f "$HERMES_DIR/config.yaml" ]; then
    sed \
        -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
        -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
        -e "s|__BASE_URL__|${DOH_LLM_BASE_URL}|g" \
        -e "s|__AUX_PROVIDER__|${DOH_AUX_PROVIDER}|g" \
        -e "s|__AUX_MODEL__|${DOH_AUX_MODEL}|g" \
        -e "s|__AUX_BASE_URL__|${DOH_AUX_BASE_URL}|g" \
        -e "s|__TERMINAL_BACKEND__|${TERMINAL_BACKEND}|g" \
        -e "s|__TERMINAL_CWD__|${TERMINAL_CWD}|g" \
        -e "s|__DOCKER_VOLUMES__|${DOCKER_VOLUMES}|g" \
        /opt/hermes-defaults/config.yaml.template > "$HERMES_DIR/config.yaml"

    # Hermes reads bedrock.region from config.yaml (runtime_provider.py:895).
    # Appended on first boot only so user edits to config.yaml are preserved.
    if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
        cat >> "$HERMES_DIR/config.yaml" <<EOF

bedrock:
  region: ${AWS_BEDROCK_REGION}
EOF
    fi
fi

# The terminal.* block is DOH-controlled (driven by the task's mount layout),
# not user-tunable. Rewrite it on every boot so already-deployed apps with an
# out-of-date config.yaml on EFS pick up fixes without a fresh volume. Other
# top-level blocks (user-editable) are preserved byte-for-byte.
python3 - "$HERMES_DIR/config.yaml" "$DOCKER_VOLUMES" "$TERMINAL_BACKEND" "$TERMINAL_CWD" <<'PY'
import json, re, sys
path, volumes_json, backend, cwd = sys.argv[1:5]
volumes = json.loads(volumes_json)
with open(path) as f:
    text = f.read()
# Scope the edit to the lines between `^terminal:` and the next top-level key.
def rewrite_terminal_block(m):
    block = m.group(0)
    def sub(block, key, value):
        pat = re.compile(rf'^(\s+){re.escape(key)}:.*$', re.MULTILINE)
        return pat.sub(lambda mm: f'{mm.group(1)}{key}: {value}', block, count=1)
    block = sub(block, 'backend', backend)
    block = sub(block, 'cwd', cwd)
    block = sub(block, 'docker_volumes', json.dumps(volumes))
    return block
new_text, n = re.subn(r'(?ms)^terminal:\n(?:[ \t].*\n)*', rewrite_terminal_block, text)
if n != 1:
    sys.exit(f"expected exactly one terminal: block, found {n}")
with open(path, 'w') as f:
    f.write(new_text)
PY

if [ ! -d "$HERMES_DIR/hermes-agent" ]; then
    cp -r /opt/hermes-defaults/hermes-agent "$HERMES_DIR/hermes-agent"
fi

if [ ! -f "$HERMES_DIR/SOUL.md" ]; then
    cp /opt/hermes-defaults/SOUL.md "$HERMES_DIR/SOUL.md"
fi

# --- Upstream bug workarounds ---
# apply.py copies overlay files and applies each NN-*.patch to the EFS-backed
# hermes-agent tree, before hermeswebui_init.bash runs `uv pip install`. Runs
# on every boot so a new image's patches take effect on already-deployed EFS
# volumes. Idempotent: already-applied patches are no-ops, so upstream fixes
# become a soft landing. See patches/ for per-patch rationale.
python3 /opt/hermes-defaults/patches/apply.py "$HERMES_DIR/hermes-agent"

# Write .env from Docker env vars so the WebUI detects provider credentials.
# Regenerated on every boot to pick up DOH config changes.
ENV_FILE="$HERMES_DIR/.env"
: > "$ENV_FILE"
chmod 600 "$ENV_FILE"
[ -n "$OPENAI_API_KEY" ] && echo "OPENAI_API_KEY=$OPENAI_API_KEY" >> "$ENV_FILE"
# OPENAI_BASE_URL is only meaningful for OpenAI-compatible endpoints, not for
# Bedrock (where DOH_LLM_BASE_URL is the bedrock-runtime URL used by boto3).
if [ "$DOH_LLM_PROVIDER" != "bedrock" ] && [ -n "$DOH_LLM_BASE_URL" ]; then
    echo "OPENAI_BASE_URL=$DOH_LLM_BASE_URL" >> "$ENV_FILE"
fi
[ -n "$ANTHROPIC_API_KEY" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$ENV_FILE"
[ -n "$OPENROUTER_API_KEY" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"
[ -n "$TAVILY_API_KEY" ] && echo "TAVILY_API_KEY=$TAVILY_API_KEY" >> "$ENV_FILE"
[ -n "$SLACK_APP_TOKEN" ] && echo "SLACK_APP_TOKEN=$SLACK_APP_TOKEN" >> "$ENV_FILE"
[ -n "$SLACK_BOT_TOKEN" ] && echo "SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN" >> "$ENV_FILE"
[ -n "$SLACK_ALLOW_ALL_USERS" ] && echo "SLACK_ALLOW_ALL_USERS=$SLACK_ALLOW_ALL_USERS" >> "$ENV_FILE"
[ -n "$SLACK_ALLOWED_USERS" ] && echo "SLACK_ALLOWED_USERS=$SLACK_ALLOWED_USERS" >> "$ENV_FILE"
[ -n "$SLACK_HOME_CHANNEL" ] && echo "SLACK_HOME_CHANNEL=$SLACK_HOME_CHANNEL" >> "$ENV_FILE"

exec "$@"
