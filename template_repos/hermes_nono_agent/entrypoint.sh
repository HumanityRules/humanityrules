#!/bin/bash
set -e

if [ -z "$DOH_LLM_PROVIDER" ] || [ -z "$DOH_LLM_MODEL" ]; then
    echo "FATAL: DOH_LLM_PROVIDER and DOH_LLM_MODEL must be set" >&2
    exit 1
fi

if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    if [ -z "$AWS_BEDROCK_REGION" ]; then
        echo "FATAL: DOH_LLM_PROVIDER=bedrock requires AWS_BEDROCK_REGION" >&2
        exit 1
    fi
    export AWS_REGION="$AWS_BEDROCK_REGION"
    export AWS_DEFAULT_REGION="$AWS_BEDROCK_REGION"
    DOH_LLM_BASE_URL="https://bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com"
fi

: "${DOH_AUX_PROVIDER:=$DOH_LLM_PROVIDER}"
: "${DOH_AUX_MODEL:=$DOH_LLM_MODEL}"
: "${DOH_AUX_BASE_URL:=}"
: "${TERMINAL_BACKEND:=local}"

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

HERMES_DIR="/home/hermeswebui/.hermes"
mkdir -p "$HERMES_DIR" /workspace

sed \
    -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
    -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
    -e "s|__BASE_URL__|${DOH_LLM_BASE_URL}|g" \
    -e "s|__AUX_PROVIDER__|${DOH_AUX_PROVIDER}|g" \
    -e "s|__AUX_MODEL__|${DOH_AUX_MODEL}|g" \
    -e "s|__AUX_BASE_URL__|${DOH_AUX_BASE_URL}|g" \
    -e "s|__TERMINAL_BACKEND__|${TERMINAL_BACKEND}|g" \
    -e "/__PROVIDERS_BLOCK__/r ${PROVIDERS_BLOCK_FILE}" \
    -e "/__PROVIDERS_BLOCK__/d" \
    /opt/hermes-defaults/config.yaml.template > "$HERMES_DIR/config.yaml"
rm -f "$PROVIDERS_BLOCK_FILE"

if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    cat >> "$HERMES_DIR/config.yaml" <<EOF

bedrock:
  region: ${AWS_BEDROCK_REGION}
EOF
fi

rm -rf "$HERMES_DIR/hermes-agent"
cp -r /opt/hermes-defaults/hermes-agent "$HERMES_DIR/hermes-agent"

if [ ! -f "$HERMES_DIR/SOUL.md" ]; then
    cp /opt/hermes-defaults/SOUL.md "$HERMES_DIR/SOUL.md"
fi

exec "$@"
