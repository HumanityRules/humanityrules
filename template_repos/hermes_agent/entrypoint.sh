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
# bedrock-runtime base_url from the region so the config.yaml template can be
# filled in uniformly. Credentials come from the ECS task role via the standard
# boto3/AWS SDK credential chain.
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
    export AWS_REGION="$AWS_BEDROCK_REGION"
    export AWS_DEFAULT_REGION="$AWS_BEDROCK_REGION"
    DOH_AUX_BASE_URL="https://bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com"
fi

mkdir -p "$HERMES_DIR" "$HERMES_DIR/workspace"
# Pre-create hermeswebui's XDG_STATE_HOME so the Slack gateway's platform-lock
# file (~/.local/state/hermes/gateway-locks) can be written under nono. The
# gateway mkdir's this on first use, but nono's filesystem.allow list rejects
# non-existent paths at startup.
mkdir -p /home/hermeswebui/.local/state/hermes

# Fixed proxy port under nono. Needed so the config.yaml we render below can
# point Hermes at http://127.0.0.1:NONO_PROXY_PORT/<service> without knowing
# nono's runtime-assigned port. Arbitrary choice, avoided 8787 (WebUI) and
# 7777 (learneo-mcp sidecar).
NONO_PROXY_PORT=44300

# Fixed local port for the ECS IMDS socat forwarder (Bedrock path only).
# Hermes's boto3 talks to nono's aws-creds route, which forwards to this port,
# which socat forwards to 169.254.170.2:80 (the ECS creds endpoint).
# nono can't reach 169.254/16 directly because its HostFilter deny-lists the
# entire link-local range; socat runs outside the sandbox so has normal network.
IMDS_LOCAL_PORT=6777

# Non-Bedrock path: config.yaml's base_url points at nono's /openai route;
# nono injects the real API key and forwards upstream. Bedrock path: keep
# base_url at the real bedrock-runtime.<region> URL — boto3 signs requests
# SigV4 client-side, so nono must not rewrite the URL (that would break the
# signature). Bedrock traffic leaves the sandbox via HTTPS_PROXY (auto-set
# by nono) as a CONNECT tunnel to the real host.
_REAL_LLM_BASE_URL="${DOH_LLM_BASE_URL:-https://api.openai.com/v1}"
_REAL_AUX_BASE_URL="${DOH_AUX_BASE_URL:-$_REAL_LLM_BASE_URL}"
if [ "$DOH_LLM_PROVIDER" != "bedrock" ]; then
    DOH_LLM_BASE_URL="http://127.0.0.1:${NONO_PROXY_PORT}/openai"
fi
if [ "$DOH_AUX_PROVIDER" != "bedrock" ]; then
    DOH_AUX_BASE_URL="http://127.0.0.1:${NONO_PROXY_PORT}/openai"
fi

# Generate config.yaml from template on every boot. We cannot preserve a user-
# edited config.yaml across redeploys because the proxy URL embeds a fixed
# port and the provider config needs to match. A user-editable layer would
# need to live outside these fields.
sed \
    -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
    -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
    -e "s|__BASE_URL__|${DOH_LLM_BASE_URL}|g" \
    -e "s|__AUX_PROVIDER__|${DOH_AUX_PROVIDER}|g" \
    -e "s|__AUX_MODEL__|${DOH_AUX_MODEL}|g" \
    -e "s|__AUX_BASE_URL__|${DOH_AUX_BASE_URL}|g" \
    /opt/hermes-defaults/config.yaml.template > "$HERMES_DIR/config.yaml"

# Hermes reads bedrock.region from config.yaml (runtime_provider.py:895).
# Appended only for Bedrock deploys.
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
# Bedrock takes a different shape: SigV4 is client-side so the child must hold
# live AWS creds. Instead of a phantom-token route, we pair a socat sidecar
# (below) with an aws-creds route whose upstream is loopback — the route's
# role is auth-only (phantom-token check) and it forwards to the sidecar,
# which forwards to IMDS. Outbound Bedrock traffic itself goes via nono's
# HTTPS_PROXY (CONNECT tunnel) so SigV4 stays intact end-to-end.
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
[ -n "$SLACK_ALLOW_ALL_USERS" ] && echo "SLACK_ALLOW_ALL_USERS=$SLACK_ALLOW_ALL_USERS" >> "$ENV_FILE"
[ -n "$SLACK_ALLOWED_USERS" ] && echo "SLACK_ALLOWED_USERS=$SLACK_ALLOWED_USERS" >> "$ENV_FILE"
[ -n "$SLACK_HOME_CHANNEL" ] && echo "SLACK_HOME_CHANNEL=$SLACK_HOME_CHANNEL" >> "$ENV_FILE"

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
# Skip empty vars AND operator sentinels that don't have the expected prefix
# for their service. A bogus value would make nono boot a credential route
# whose "secret" is a non-credential string — harmless at startup (proxy loads
# it), but the first real API call fails with an auth error that's confusing
# to diagnose. Better to omit the route entirely.
write_secret() {
    local name=$1 value=$2 prefix=$3
    [ -z "$value" ] && return 0
    # If a prefix is specified, require the value to start with it.
    [ -n "$prefix" ] && [[ "$value" != $prefix* ]] && return 0
    local path="$SECRETS_DIR/$name"
    umask 077
    printf '%s' "$value" > "$path"
    chmod 0400 "$path"
}
write_secret openai_api_key     "$OPENAI_API_KEY"     "sk-"
write_secret anthropic_api_key  "$ANTHROPIC_API_KEY"  "sk-"
write_secret openrouter_api_key "$OPENROUTER_API_KEY" "sk-"
write_secret tavily_api_key     "$TAVILY_API_KEY"     "tvly-"
write_secret slack_bot_token    "$SLACK_BOT_TOKEN"    "xoxb-"
write_secret slack_app_token    "$SLACK_APP_TOKEN"    "xapp-"

# --- Bedrock IMDS forwarder ---
# boto3 reads creds from ECS IMDS at 169.254.170.2:80, which nono denies at the
# HostFilter level (link-local range, hardcoded SSRF protection). socat runs
# outside the sandbox — so it can reach 169.254 normally — and exposes the
# same endpoint on loopback. nono's "aws-creds" route forwards requests from
# the child to this loopback port, and the route passes because 127.0.0.1 is
# not link-local.
#
# Only set up when Bedrock is active on either provider. AWS_CONTAINER_CREDENTIALS_
# RELATIVE_URI is set by Fargate; we rewrite it to a FULL_URI pointing at our
# loopback route so boto3 talks to nono instead of directly to IMDS.
BEDROCK_ACTIVE=0
if [ "$DOH_LLM_PROVIDER" = "bedrock" ] || [ "$DOH_AUX_PROVIDER" = "bedrock" ]; then
    BEDROCK_ACTIVE=1
fi

if [ "$BEDROCK_ACTIVE" = "1" ]; then
    if [ -z "$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI" ]; then
        echo "FATAL: bedrock requires Fargate IMDS (AWS_CONTAINER_CREDENTIALS_RELATIVE_URI unset)" >&2
        exit 1
    fi
    # socat: accept any connection on :6777, forward to ECS IMDS. reuseaddr
    # because we may redeploy without waiting for TIME_WAIT. fork so concurrent
    # creds refreshes don't serialize.
    socat TCP-LISTEN:${IMDS_LOCAL_PORT},reuseaddr,fork,bind=127.0.0.1 TCP:169.254.170.2:80 &
    IMDS_SOCAT_PID=$!
    echo "[entrypoint] Started IMDS socat forwarder (pid=$IMDS_SOCAT_PID) on 127.0.0.1:${IMDS_LOCAL_PORT}"

    # Throwaway token for the aws-creds phantom-token check. IMDS itself
    # ignores Authorization headers, so the value doesn't matter to it — but
    # nono requires the child to present the session token to authenticate
    # against the reverse proxy. We write a random value; nono loads it once
    # at startup and injects it into the child as AWS_CONTAINER_AUTHORIZATION_
    # TOKEN (the env var boto3 uses to authenticate to FULL_URI endpoints).
    umask 077
    head -c 32 /dev/urandom | base64 | tr -d '=+/' > "$SECRETS_DIR/aws_creds_token"
    chmod 0400 "$SECRETS_DIR/aws_creds_token"
fi

# Slack-enabled signal — computed from the real secrets at entrypoint time
# because under nono the child's SLACK_*_TOKEN env vars hold phantom proxy
# tokens (non-empty regardless of whether Slack is configured). start.sh uses
# DOH_SLACK_ENABLED to decide whether to launch the gateway alongside the WebUI.
#
# Shape check, not presence check: operators can park sentinel values
# ("disabled", "-", etc.) in the blueprint/shared-secrets to keep the keys
# schema-valid while disabling Slack. Only real xoxb-/xapp- tokens count.
if [[ "$SLACK_BOT_TOKEN" == xoxb-* ]] && [[ "$SLACK_APP_TOKEN" == xapp-* ]]; then
    export DOH_SLACK_ENABLED=1
else
    export DOH_SLACK_ENABLED=0
fi

# --- Render the nono profile ---
# Three dynamic substitutions:
#   1. openai.upstream follows DOH_LLM_BASE_URL so users pointing Hermes at an
#      OpenAI-compatible endpoint (OpenRouter Classic, vLLM, Azure, etc. via
#      DOH_LLM_PROVIDER=custom) keep working.
#   2. credentials[] is trimmed to services whose secrets are actually present.
#      Routes with missing secret files would fail nono's startup credential
#      load even if the route is otherwise harmless.
#   3. allow_domain lists hosts the child can reach via nono's forward proxy
#      (CONNECT tunnels). Empty by default — an empty list plus at least one
#      reverse-proxy route is how Hermes's standard providers work. Bedrock
#      needs its real bedrock-runtime.<region> host here because boto3 signs
#      requests client-side with SigV4, so nono must pass the raw CONNECT
#      through rather than intercepting the body.
#
# `upstream` is where nono forwards the request after injecting the Bearer
# header. Hermes talks to the loopback proxy (via config.yaml.base_url rendered
# earlier) and the proxy forwards here.
OPENAI_UPSTREAM="$_REAL_LLM_BASE_URL"

ACTIVE=()
[ -f "$SECRETS_DIR/openai_api_key" ]     && ACTIVE+=('"openai"')
[ -f "$SECRETS_DIR/anthropic_api_key" ]  && ACTIVE+=('"anthropic"')
[ -f "$SECRETS_DIR/openrouter_api_key" ] && ACTIVE+=('"openrouter"')
[ -f "$SECRETS_DIR/tavily_api_key" ]     && ACTIVE+=('"tavily"')
[ -f "$SECRETS_DIR/slack_bot_token" ]    && ACTIVE+=('"slack-bot"')
[ -f "$SECRETS_DIR/slack_app_token" ]    && ACTIVE+=('"slack-app"')
[ -f "$SECRETS_DIR/aws_creds_token" ]    && ACTIVE+=('"aws-creds"')
# Join with ", " — bash expands arrays with IFS, set to ", " briefly.
_oldIFS="$IFS"; IFS=', '; ACTIVE_JSON="[${ACTIVE[*]}]"; IFS="$_oldIFS"

ALLOW_DOMAINS=()
if [ "$BEDROCK_ACTIVE" = "1" ]; then
    # boto3 SigV4 calls go out via nono's HTTPS_PROXY CONNECT tunnel to the
    # real bedrock-runtime host. The allow_domain entry permits that CONNECT.
    ALLOW_DOMAINS+=("\"bedrock-runtime.${AWS_BEDROCK_REGION}.amazonaws.com\"")
fi
_oldIFS="$IFS"; IFS=', '; ALLOW_DOMAINS_JSON="[${ALLOW_DOMAINS[*]}]"; IFS="$_oldIFS"

NONO_PROFILE="$HERMES_DIR/nono-profile.json"
sed -e "s|__OPENAI_UPSTREAM__|${OPENAI_UPSTREAM}|g" \
    -e "s|__ACTIVE_CREDENTIALS__|${ACTIVE_JSON}|g" \
    -e "s|__ALLOW_DOMAINS__|${ALLOW_DOMAINS_JSON}|g" \
    /opt/hermes-defaults/nono-profile.json.template > "$NONO_PROFILE"

# Propagate FULL_URI to the child via an env var allowed through nono's
# allow_vars list. boto3 sees FULL_URI → talks to nono's aws-creds route →
# socat → real IMDS. RELATIVE_URI is scrubbed by the allow_vars filter so
# boto3 doesn't prefer it over FULL_URI.
if [ "$BEDROCK_ACTIVE" = "1" ]; then
    export AWS_CONTAINER_CREDENTIALS_FULL_URI="http://127.0.0.1:${NONO_PROXY_PORT}/aws-creds${AWS_CONTAINER_CREDENTIALS_RELATIVE_URI}"
fi

exec nono run --profile "$NONO_PROFILE" --proxy-port "$NONO_PROXY_PORT" -- "$@"
