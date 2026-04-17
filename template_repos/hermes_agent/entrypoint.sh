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

# Bedrock provider: validate credentials, bridge user-facing AWS_BEDROCK_* vars
# to boto3's standard chain names, and derive the bedrock-runtime base_url from
# the region so the config.yaml template can be filled in uniformly.
if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    if [ -z "$AWS_BEDROCK_ACCESS_KEY_ID" ] || [ -z "$AWS_BEDROCK_SECRET_ACCESS_KEY" ] || [ -z "$AWS_BEDROCK_REGION" ]; then
        echo "FATAL: DOH_LLM_PROVIDER=bedrock requires AWS_BEDROCK_ACCESS_KEY_ID, AWS_BEDROCK_SECRET_ACCESS_KEY, and AWS_BEDROCK_REGION" >&2
        exit 1
    fi
    export AWS_ACCESS_KEY_ID="$AWS_BEDROCK_ACCESS_KEY_ID"
    export AWS_SECRET_ACCESS_KEY="$AWS_BEDROCK_SECRET_ACCESS_KEY"
    export AWS_REGION="$AWS_BEDROCK_REGION"
    export AWS_DEFAULT_REGION="$AWS_BEDROCK_REGION"
    DOH_LLM_BASE_URL="https://bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com"
fi

mkdir -p "$HERMES_DIR" "$HERMES_DIR/workspace"

# Generate config.yaml from template on first boot.
# Existing files (from a previous deploy on EFS) are never overwritten.
if [ ! -f "$HERMES_DIR/config.yaml" ]; then
    sed \
        -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
        -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
        -e "s|__BASE_URL__|${DOH_LLM_BASE_URL}|g" \
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

if [ ! -d "$HERMES_DIR/hermes-agent" ]; then
    cp -r /opt/hermes-defaults/hermes-agent "$HERMES_DIR/hermes-agent"
fi

if [ ! -f "$HERMES_DIR/SOUL.md" ]; then
    cp /opt/hermes-defaults/SOUL.md "$HERMES_DIR/SOUL.md"
fi

# --- Upstream bug workaround: Bedrock Claude inference-profile IDs ---
# Hermes' run_agent._anthropic_preserve_dots() doesn't list "bedrock" in its
# allow-set, so build_anthropic_kwargs → normalize_model_name() rewrites dots
# to hyphens before the AnthropicBedrock SDK call. That turns valid IDs like
# `us.anthropic.claude-opus-4-6-v1` into `us-anthropic-claude-opus-4-6-v1`,
# which Bedrock rejects with `400 The provided model identifier is invalid.`.
#
# We patch the source tree on every boot before hermeswebui_init.bash runs
# `uv pip install $HERMES_DIR/hermes-agent`, so the installed site-packages
# copy picks up the fix. The matcher is strict (looks for the exact upstream
# set of providers) and idempotent, so if upstream ships a fix later, this
# becomes a no-op.
AGENT_RUN_PY="$HERMES_DIR/hermes-agent/run_agent.py"
if [ -f "$AGENT_RUN_PY" ]; then
    python3 - "$AGENT_RUN_PY" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f: src = f.read()
target = '{"alibaba", "minimax", "minimax-cn", "opencode-go", "opencode-zen", "zai"}'
patched = '{"alibaba", "minimax", "minimax-cn", "opencode-go", "opencode-zen", "zai", "bedrock"}'
if patched in src:
    print(f"[entrypoint] run_agent.py already patched for bedrock dot-preservation.")
elif target in src:
    with open(path, "w", encoding="utf-8") as f: f.write(src.replace(target, patched, 1))
    print(f"[entrypoint] Patched run_agent._anthropic_preserve_dots to include 'bedrock'.")
else:
    print(f"[entrypoint] run_agent._anthropic_preserve_dots signature not found — upstream may have fixed this; skipping patch.", file=sys.stderr)
PYEOF
fi

# --- Upstream bug workaround: Bedrock Claude prompt caching ---
# Hermes decides whether to inject Anthropic cache_control breakpoints based on
# `is_native_anthropic = api_mode == "anthropic_messages" and provider == "anthropic"`.
# That excludes the Bedrock+Claude path (where provider == "bedrock" but api_mode
# is still "anthropic_messages" because hermes uses the AnthropicBedrock SDK).
# Result: no cache_control is ever sent to Bedrock → Bedrock caches nothing
# → input tokens are billed in full on every turn, missing the ~75-90% savings.
#
# We rewrite the three `is_native_anthropic = ... provider == "anthropic"` sites
# to also accept `"bedrock"`. Matchers are strict (verbatim upstream lines) and
# idempotent, so if upstream ships a fix later, this becomes a no-op.
# Verified in-container: with this patch, Sonnet 4.5 on Bedrock reports
# cache_creation_input_tokens>0 on turn 1 and cache_read_input_tokens>0 on turn 2.
if [ -f "$AGENT_RUN_PY" ]; then
    python3 - "$AGENT_RUN_PY" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f: src = f.read()

# Three verbatim upstream lines that gate prompt caching. Each patched form
# replaces the strict `== "anthropic"` check with an `in {"anthropic","bedrock"}`.
patches = [
    # __init__: initial decision
    (
        'is_native_anthropic = self.api_mode == "anthropic_messages" and self.provider == "anthropic"',
        'is_native_anthropic = self.api_mode == "anthropic_messages" and self.provider in {"anthropic", "bedrock"}',
    ),
    # Provider-switch path (in-session model change)
    (
        'is_native_anthropic = api_mode == "anthropic_messages" and new_provider == "anthropic"',
        'is_native_anthropic = api_mode == "anthropic_messages" and new_provider in {"anthropic", "bedrock"}',
    ),
    # Fallback-provider path
    (
        'is_native_anthropic = fb_api_mode == "anthropic_messages" and fb_provider == "anthropic"',
        'is_native_anthropic = fb_api_mode == "anthropic_messages" and fb_provider in {"anthropic", "bedrock"}',
    ),
]

changed = 0
for old, new in patches:
    if new in src:
        continue  # already patched
    if old in src:
        src = src.replace(old, new, 1)
        changed += 1
    else:
        print(f"[entrypoint] prompt-caching matcher not found (upstream may have fixed this): {old[:70]}...", file=sys.stderr)

if changed:
    with open(path, "w", encoding="utf-8") as f: f.write(src)
    print(f"[entrypoint] Patched run_agent.py: enabled Bedrock prompt caching at {changed} site(s).")
else:
    print(f"[entrypoint] run_agent.py prompt caching already enabled for Bedrock (no patch needed).")
PYEOF
fi

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

exec "$@"
