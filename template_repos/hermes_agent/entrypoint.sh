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

# --- Seed bundled skills into ~/.hermes/skills/ ---
# Idempotent: sync_skills() tracks which skills have been offered via a
# manifest and skips user-modified copies. Upstream wires this into
# `hermes update` and `hermes profile create`, neither of which our ECS
# deploy flow goes through — so without this call the WebUI skills panel
# stays empty despite hermes-agent shipping 70+ bundled skills.
HERMES_HOME="$HERMES_DIR" python3 -c "
import sys
sys.path.insert(0, '$HERMES_DIR/hermes-agent')
from tools.skills_sync import sync_skills
r = sync_skills(quiet=True)
print(f'[entrypoint:skills] copied={len(r[\"copied\"])} updated={len(r[\"updated\"])} skipped={r[\"skipped\"]} user_modified={len(r[\"user_modified\"])} total_bundled={r[\"total_bundled\"]}')
" || echo "[entrypoint:skills] sync failed (non-fatal)"

exec "$@"
