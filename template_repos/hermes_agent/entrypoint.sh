#!/bin/bash
set -e

HERMES_DIR="/home/hermeswebui/.hermes"
PROVIDER="${HERMES_INFERENCE_PROVIDER:-openai}"
MODEL="${HERMES_MODEL:-gpt-5.4-mini}"

mkdir -p "$HERMES_DIR"

# Generate config.yaml from Docker env vars on first boot.
#
# If want to avoid the onboarding wizard (HERMES_WEBUI_SKIP_ONBOARDING=1), which is 
# shown unless chat_ready=True, then config.yaml needs model.provider, model.default, 
# and model.base_url, plus the API key must be present in ~/.hermes/.env.
#
# Existing files (from a previous deploy on EFS) are never overwritten.
if [ ! -f "$HERMES_DIR/config.yaml" ]; then
    # Hermes agent treats direct OpenAI as "custom" provider with base_url
    CONFIG_PROVIDER="$PROVIDER"
    BASE_URL_LINE=""
    if [ "$PROVIDER" = "openai" ]; then
        CONFIG_PROVIDER="custom"
        BASE_URL_LINE="  base_url: https://api.openai.com/v1"
    fi

    cat > "$HERMES_DIR/config.yaml" << YAML
# Hermes Agent — DOH Enterprise Configuration
# Generated from Docker env vars at first boot.

model:
  provider: ${CONFIG_PROVIDER}
  default: ${MODEL}
${BASE_URL_LINE}

terminal:
  env: local

session:
  idle_minutes: 1440
  reset_hour: 4
YAML
fi

if [ ! -f "$HERMES_DIR/SOUL.md" ]; then
    cp /opt/hermes-defaults/SOUL.md "$HERMES_DIR/SOUL.md"
fi

# Write .env from Docker env vars so the WebUI detects provider credentials.
# Regenerated on every boot to pick up DOH config changes.
ENV_FILE="$HERMES_DIR/.env"
: > "$ENV_FILE"
[ -n "$OPENAI_API_KEY" ] && echo "OPENAI_API_KEY=$OPENAI_API_KEY" >> "$ENV_FILE"
if [ "$PROVIDER" = "openai" ] && [ -n "$OPENAI_API_KEY" ]; then
    echo "OPENAI_BASE_URL=https://api.openai.com/v1" >> "$ENV_FILE"
fi
[ -n "$ANTHROPIC_API_KEY" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$ENV_FILE"
[ -n "$OPENROUTER_API_KEY" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"
[ -n "$TAVILY_API_KEY" ] && echo "TAVILY_API_KEY=$TAVILY_API_KEY" >> "$ENV_FILE"
[ -n "$SLACK_APP_TOKEN" ] && echo "SLACK_APP_TOKEN=$SLACK_APP_TOKEN" >> "$ENV_FILE"
[ -n "$SLACK_BOT_TOKEN" ] && echo "SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN" >> "$ENV_FILE"

exec "$@"
