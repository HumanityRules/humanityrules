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

# --- Upstream bug workarounds ---
# apply.py copies overlay files and applies each NN-*.patch to the EFS-backed
# hermes-agent tree, before the venv is populated. Runs on every boot so a
# new image's patches take effect on already-deployed EFS volumes. Idempotent:
# already-applied patches are no-ops, so upstream fixes become a soft landing.
# See patches/ for per-patch rationale.
python3 /opt/hermes-defaults/patches/apply.py "$HERMES_DIR/hermes-agent"

# --- WebUI init (pre-sandbox) ---
# Upstream's /hermeswebui_init.bash does three things: chown+rsync /apptoo/
# into /app, create the venv, install deps. We run our own trimmed equivalent
# before nono takes over so the sandbox profile doesn't need to cover sudo,
# /etc/passwd, /etc/pam.d, or uv's scratch dirs. /app is pre-created by the
# Dockerfile (owned by hermeswebui) so no sudo is needed for the rsync.
#
# Dep installs touch pypi.org. Running them here, outside nono, means the
# profile stays lean: no need to allowlist package registries.
if [ ! -f /app/server.py ]; then
    echo "[entrypoint] Seeding /app from /apptoo/"
    rsync -a /apptoo/ /app/
fi

if [ ! -f /app/venv/bin/activate ]; then
    echo "[entrypoint] Creating Python venv at /app/venv"
    uv venv /app/venv
fi

# Idempotent: first deploy installs, subsequent deploys short-circuit via
# the sentinel file. A new Dockerfile with a bumped pinned Hermes version
# can force a re-install by rm'ing .deps_installed in the Dockerfile layer.
if [ ! -f /app/venv/.deps_installed ]; then
    echo "[entrypoint] Installing WebUI + hermes-agent[honcho] + hermes-agent[bedrock] + slack-sdk into venv"
    # shellcheck disable=SC1091
    source /app/venv/bin/activate
    uv pip install -r /app/requirements.txt \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    uv pip install -U pip setuptools \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    uv pip install "$HERMES_DIR/hermes-agent[honcho,bedrock]" \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    uv pip install "slack-bolt>=1.18.0,<2" "slack-sdk>=3.27.0,<4" \
        --trusted-host pypi.org --trusted-host files.pythonhosted.org
    deactivate
    touch /app/venv/.deps_installed
fi

# --- Secrecy posture ---
#
# Secrets (OPENAI_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY, TAVILY_API_KEY,
# SLACK_*_TOKEN) arrive as ECS env vars but MUST NOT reach the Hermes child
# process or touch disk (.env, logs, /proc/*/environ). We enforce this by
# launching Hermes under `nono run` with a profile that:
#
#   1. reads each secret once via env:// URIs before the sandbox starts,
#   2. injects it as an HTTP header on the matching reverse-proxy route,
#   3. scrubs all non-allowlisted env vars from the child's environment,
#   4. sets <SERVICE>_BASE_URL=http://127.0.0.1:PORT/<service> in the child so
#      SDKs talk to the proxy instead of the upstream.
#
# Bedrock is exempt (uses SigV4 signing over the entire request body, not a
# bearer header — out of scope for header-injection proxies). Bedrock deploys
# rely on IAM task roles; the short-lived, Bedrock-scoped creds fetched from
# 169.254.170.2 are an accepted residual.
USE_NONO=1
if [ "$DOH_LLM_PROVIDER" = "bedrock" ] || [ "$DOH_AUX_PROVIDER" = "bedrock" ]; then
    USE_NONO=0
fi

# Write .env from Docker env vars so the WebUI detects provider credentials.
# Regenerated on every boot to pick up DOH config changes.
#
# Under nono, API keys and *_BASE_URL are injected into the child's environment
# at launch (real values for non-proxied bits like SLACK_ALLOWED_USERS; phantom
# tokens + loopback URLs for proxied services). We deliberately DO NOT write
# those to .env — the child would read them back from disk and see the phantom
# token, which is fine, but keeping the file free of any secret-shaped strings
# makes the "no secrets on disk" audit story trivial.
#
# Non-secret Slack knobs still go to .env because hermes-webui's config layer
# reads them from there for first-boot detection.
ENV_FILE="$HERMES_DIR/.env"
: > "$ENV_FILE"
chmod 600 "$ENV_FILE"
if [ "$USE_NONO" = "0" ]; then
    # Bedrock path: no proxy, pass creds through as before.
    [ -n "$OPENAI_API_KEY" ] && echo "OPENAI_API_KEY=$OPENAI_API_KEY" >> "$ENV_FILE"
    if [ "$DOH_LLM_PROVIDER" != "bedrock" ] && [ -n "$DOH_LLM_BASE_URL" ]; then
        echo "OPENAI_BASE_URL=$DOH_LLM_BASE_URL" >> "$ENV_FILE"
    fi
    [ -n "$ANTHROPIC_API_KEY" ] && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" >> "$ENV_FILE"
    [ -n "$OPENROUTER_API_KEY" ] && echo "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" >> "$ENV_FILE"
    [ -n "$TAVILY_API_KEY" ] && echo "TAVILY_API_KEY=$TAVILY_API_KEY" >> "$ENV_FILE"
    [ -n "$SLACK_APP_TOKEN" ] && echo "SLACK_APP_TOKEN=$SLACK_APP_TOKEN" >> "$ENV_FILE"
    [ -n "$SLACK_BOT_TOKEN" ] && echo "SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN" >> "$ENV_FILE"
fi
[ -n "$SLACK_ALLOW_ALL_USERS" ] && echo "SLACK_ALLOW_ALL_USERS=$SLACK_ALLOW_ALL_USERS" >> "$ENV_FILE"
[ -n "$SLACK_ALLOWED_USERS" ] && echo "SLACK_ALLOWED_USERS=$SLACK_ALLOWED_USERS" >> "$ENV_FILE"
[ -n "$SLACK_HOME_CHANNEL" ] && echo "SLACK_HOME_CHANNEL=$SLACK_HOME_CHANNEL" >> "$ENV_FILE"

if [ "$USE_NONO" = "0" ]; then
    exec "$@"
fi

# --- Stage secrets for nono ---
# nono's custom_credentials only accepts keyring / op:// / apple-password:// /
# file:// URIs for credential_key — env:// is supported by the core keystore
# but rejected by the profile validator (profile/mod.rs:265-273). We use
# file:// URIs, staging each secret as a 0400 file in a per-boot directory.
# Child processes never see these files: start.sh (running under nono as the
# "sandboxed" child) removes the directory first thing, after nono has loaded
# the values into zeroized memory.
SECRETS_DIR="/tmp/doh-secrets"
rm -rf "$SECRETS_DIR"
mkdir -m 0700 "$SECRETS_DIR"
write_secret() {
    local name=$1 value=$2
    [ -z "$value" ] && return 0
    local path="$SECRETS_DIR/$name"
    umask 077
    printf '%s' "$value" > "$path"
    chmod 0400 "$path"
}
write_secret openai_api_key     "$OPENAI_API_KEY"
write_secret anthropic_api_key  "$ANTHROPIC_API_KEY"
write_secret openrouter_api_key "$OPENROUTER_API_KEY"
write_secret tavily_api_key     "$TAVILY_API_KEY"
write_secret slack_bot_token    "$SLACK_BOT_TOKEN"
write_secret slack_app_token    "$SLACK_APP_TOKEN"

# Slack-enabled signal — computed from the real secrets at entrypoint time
# because under nono the child's SLACK_*_TOKEN env vars hold phantom proxy
# tokens (non-empty regardless of whether Slack is configured). start.sh uses
# DOH_SLACK_ENABLED to decide whether to launch the gateway alongside the WebUI.
if [ -n "$SLACK_BOT_TOKEN" ] || [ -n "$SLACK_APP_TOKEN" ]; then
    export DOH_SLACK_ENABLED=1
else
    export DOH_SLACK_ENABLED=0
fi

# --- Render the nono profile ---
# Two dynamic substitutions:
#   1. openai.upstream follows DOH_LLM_BASE_URL so users pointing Hermes at an
#      OpenAI-compatible endpoint (OpenRouter Classic, vLLM, Azure, etc. via
#      DOH_LLM_PROVIDER=custom) keep working.
#   2. credentials[] is trimmed to services whose secrets are actually present.
#      Routes with missing secret files would fail nono's startup credential
#      load even if the route is otherwise harmless.
OPENAI_UPSTREAM="${DOH_LLM_BASE_URL:-https://api.openai.com/v1}"

ACTIVE=()
[ -f "$SECRETS_DIR/openai_api_key" ]     && ACTIVE+=('"openai"')
[ -f "$SECRETS_DIR/anthropic_api_key" ]  && ACTIVE+=('"anthropic"')
[ -f "$SECRETS_DIR/openrouter_api_key" ] && ACTIVE+=('"openrouter"')
[ -f "$SECRETS_DIR/tavily_api_key" ]     && ACTIVE+=('"tavily"')
[ -f "$SECRETS_DIR/slack_bot_token" ]    && ACTIVE+=('"slack-bot"')
[ -f "$SECRETS_DIR/slack_app_token" ]    && ACTIVE+=('"slack-app"')
# Join with ", " — bash expands arrays with IFS, set to ", " briefly.
_oldIFS="$IFS"; IFS=', '; ACTIVE_JSON="[${ACTIVE[*]}]"; IFS="$_oldIFS"

NONO_PROFILE="$HERMES_DIR/nono-profile.json"
sed -e "s|__OPENAI_UPSTREAM__|${OPENAI_UPSTREAM}|g" \
    -e "s|__ACTIVE_CREDENTIALS__|${ACTIVE_JSON}|g" \
    /opt/hermes-defaults/nono-profile.json.template > "$NONO_PROFILE"

exec nono run --profile "$NONO_PROFILE" -- "$@"
