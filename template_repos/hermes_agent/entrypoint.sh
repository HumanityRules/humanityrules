#!/bin/bash
set -e

# DOH_LLM_* vars are DOH's own config, deliberately namespaced to avoid
# colliding with Hermes's HERMES_* env vars.
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

# Auxiliary LLM (vision, compression, session_search, skills_hub, approval,
# mcp, flush_memories, web_extract, title_generation). One shared config,
# fanned out into all 9 slots. Defaults to the main provider when unset so
# Bedrock users get a Bedrock aux. Reuses the main provider's API key (no
# separate aux key var).
: "${DOH_AUX_PROVIDER:=$DOH_LLM_PROVIDER}"
: "${DOH_AUX_MODEL:=$DOH_LLM_MODEL}"
: "${DOH_AUX_BASE_URL:=}"

# DOCKER_HOST points at the in-task DinD sidecar. The hermes container's
# depends_on: {docker-dind, HEALTHY} already guarantees DinD is up before we
# boot, and ECS launches us with DOCKER_HOST set from the template.
if [ -z "${DOCKER_HOST}" ]; then
    echo "FATAL: DOCKER_HOST is not set; cannot reach the DinD sidecar" >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "FATAL: no Docker server reachable at DOCKER_HOST=${DOCKER_HOST}" >&2
    exit 1
fi
if [ ! -d "/workspace" ]; then
    echo "FATAL: /workspace is not mounted (expected the workspace EFS access point)" >&2
    exit 1
fi
if [ -z "$TOOL_IMAGE" ]; then
    echo "FATAL: TOOL_IMAGE is not set (expected from the hermes container's environment)" >&2
    exit 1
fi
TERMINAL_BACKEND="docker"
DOCKER_VOLUMES='["/workspace:/workspace"]'
echo "[entrypoint] Docker-backed Hermes tools enabled (DinD via DOCKER_HOST=$DOCKER_HOST)."

# Build the providers block. For Bedrock we ship a curated dict of
# inference-profile IDs → human-readable labels; the WebUI's group builder
# (api/config.py:get_available_models) reads providers.<pid>.models.
PROVIDERS_BLOCK_FILE=$(mktemp)
if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    cat > "$PROVIDERS_BLOCK_FILE" <<'EOF'
providers:
  bedrock:
    models:
      'us.anthropic.claude-opus-4-7': "Opus 4.7"
      'us.anthropic.claude-sonnet-4-6': "Sonnet 4.6"
      'us.anthropic.claude-haiku-4-5-20251001-v1:0': "Haiku 4.5"
EOF
else
    echo "providers: {}" > "$PROVIDERS_BLOCK_FILE"
fi

#
# We have everything we need to generate config.yaml!
#
HERMES_DIR="/home/hermeswebui/.hermes"
mkdir -p "$HERMES_DIR"

# ~/.hermes lives on the ECS task's local SSD for low-latency per-turn I/O.
# Durable state (sessions, memories, webui-mvp, SOUL.md, state.db, etc.) is
# mirrored to EFS at /mnt/hermes-persistent by a 10s background rsync loop in
# start.sh. On boot, restore state from EFS so chat history, memories, and
# settings survive task replacement. hermes-agent/ is excluded: it's
# regenerated from the image every boot, and re-running the patches via
# apply.py below keeps it fresh.
HERMES_EFS_PERSIST=/mnt/hermes-persistent
if [ -d "$HERMES_EFS_PERSIST" ] && [ -n "$(ls -A "$HERMES_EFS_PERSIST" 2>/dev/null)" ]; then
    echo "[entrypoint] Restoring ~/.hermes from $HERMES_EFS_PERSIST..."
    _t0=$(date +%s%3N)
    rsync -a --exclude='hermes-agent/' "$HERMES_EFS_PERSIST/" "$HERMES_DIR/"
    _size_mb=$(du -sm "$HERMES_DIR" 2>/dev/null | cut -f1)
    echo "[entrypoint] Restore done in $(($(date +%s%3N) - _t0))ms (~${_size_mb}MB on SSD)."
fi

# Regenerate config.yaml from template on every boot. DOH owns this file.
# sed's `r file` + `d` replaces the single-line __PROVIDERS_BLOCK__ marker
# with the multi-line block from above.
sed \
    -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
    -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
    -e "s|__BASE_URL__|${DOH_LLM_BASE_URL}|g" \
    -e "s|__AUX_PROVIDER__|${DOH_AUX_PROVIDER}|g" \
    -e "s|__AUX_MODEL__|${DOH_AUX_MODEL}|g" \
    -e "s|__AUX_BASE_URL__|${DOH_AUX_BASE_URL}|g" \
    -e "s|__TERMINAL_BACKEND__|${TERMINAL_BACKEND}|g" \
    -e "s|__TOOL_IMAGE__|${TOOL_IMAGE}|g" \
    -e "s|__DOCKER_VOLUMES__|${DOCKER_VOLUMES}|g" \
    -e "/__PROVIDERS_BLOCK__/r ${PROVIDERS_BLOCK_FILE}" \
    -e "/__PROVIDERS_BLOCK__/d" \
    /opt/hermes-defaults/config.yaml.template > "$HERMES_DIR/config.yaml"
rm -f "$PROVIDERS_BLOCK_FILE"

# Hermes reads bedrock.region from config.yaml (runtime_provider.py:895).
if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    cat >> "$HERMES_DIR/config.yaml" <<EOF

bedrock:
  region: ${AWS_BEDROCK_REGION}
EOF
fi

# hermes-agent is rsync-excluded from the EFS persist set (see start.sh), so
# ~/.hermes is fresh SSD on every boot and we seed unconditionally from the
# image. apply.py then re-applies DOH patches against the freshly-seeded tree.
cp -r /opt/hermes-defaults/hermes-agent "$HERMES_DIR/hermes-agent"

# SOUL.md is user-editable, so it *is* in the EFS persist set and was already
# restored above (if EFS had a copy). If restore left no SOUL.md -> brand-new
# deploy with empty EFS -> seed the image default so Hermes has a persona.
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

exec "$@"
