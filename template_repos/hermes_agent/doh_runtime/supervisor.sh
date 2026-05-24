#!/bin/bash
set -euo pipefail

AWS_STS_PORT=9901
AWS_BEDROCK_PORT=9902
AWS_BEDROCK_RUNTIME_PORT=9903

: "${DOH_BIN_DIR:?DOH_BIN_DIR must be set}"
: "${DOH_ROOT:?DOH_ROOT must be set}"
: "${DOH_RUN_DIR:?DOH_RUN_DIR must be set}"
: "${DOH_RUNTIME_DIR:?DOH_RUNTIME_DIR must be set}"
: "${HERMES_CONFIG_TEMPLATE:?HERMES_CONFIG_TEMPLATE must be set}"
: "${HERMES_HOME:?HERMES_HOME must be set}"
: "${HERMES_WEBUI_AGENT_DIR:?HERMES_WEBUI_AGENT_DIR must be set}"
: "${HERMES_WEBUI_DEFAULT_WORKSPACE:?HERMES_WEBUI_DEFAULT_WORKSPACE must be set}"
: "${HERMES_WEBUI_DIR:?HERMES_WEBUI_DIR must be set}"
: "${HERMES_WEBUI_EXTENSION_DIR:?HERMES_WEBUI_EXTENSION_DIR must be set}"
: "${HERMES_WEBUI_PYTHON:?HERMES_WEBUI_PYTHON must be set}"
: "${HERMES_WEBUI_SKIP_ONBOARDING:?HERMES_WEBUI_SKIP_ONBOARDING must be set}"
: "${HERMES_WEBUI_STATE_DIR:?HERMES_WEBUI_STATE_DIR must be set}"

INTEGRATIONS_BROKER_CA_DIR="${DOH_RUN_DIR}/integrations-broker/ca"
INTEGRATIONS_BROKER_PRIVATE_DIR="${DOH_RUN_DIR}/integrations-broker/private"
INTEGRATIONS_BROKER_PROXY_PORT=9950
INTEGRATIONS_BROKER_CONTROL_PORT=9951
MCP_AGGREGATOR_PORT=9952
AWS_SIGNER_PID=""
INTEGRATIONS_BROKER_PID=""
GATEWAY_INTEGRATION_ENV=()

die() {
    echo "FATAL: $*" >&2
    exit 1
}

cleanup() {
    set +e
    if [ -n "$INTEGRATIONS_BROKER_PID" ] && kill -0 "$INTEGRATIONS_BROKER_PID" 2>/dev/null; then
        kill "$INTEGRATIONS_BROKER_PID"
    fi
    if [ -n "$AWS_SIGNER_PID" ] && kill -0 "$AWS_SIGNER_PID" 2>/dev/null; then
        kill "$AWS_SIGNER_PID"
    fi
}

trap cleanup EXIT INT TERM

require_llm_config() {
    if [ -z "${DOH_LLM_PROVIDER:-}" ] || [ -z "${DOH_LLM_MODEL:-}" ]; then
        die "DOH_LLM_PROVIDER and DOH_LLM_MODEL must be set"
    fi
}

require_aws_region() {
    if [ -z "${AWS_DEFAULT_REGION:-}" ]; then
        die "AWS_DEFAULT_REGION must be set"
    fi
}

wait_for_port() {
    local port="$1"
    local pid="$2"
    local name="$3"

    for _ in $(seq 1 100); do
        if (echo > "/dev/tcp/127.0.0.1/${port}") >/dev/null 2>&1; then
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "FATAL: ${name} exited before opening port ${port}" >&2
            wait "$pid"
            return 1
        fi
        sleep 0.1
    done

    echo "FATAL: ${name} did not open port ${port}" >&2
    return 1
}

start_aws_signer() {
    # DOH-owned streaming SigV4 proxy. Replaces aws-sigv4-proxy + haproxy —
    # those buffer the full response body before flushing, which breaks
    # Bedrock event-stream (see awslabs/aws-sigv4-proxy#250). Runs outside
    # nono so credentials stay out of the sandboxed Hermes process.
    "$HERMES_WEBUI_PYTHON" "${DOH_RUNTIME_DIR}/aws_signer.py" --region "$AWS_DEFAULT_REGION" &
    AWS_SIGNER_PID=$!
    wait_for_port "$AWS_STS_PORT"             "$AWS_SIGNER_PID" "aws-signer"
    wait_for_port "$AWS_BEDROCK_PORT"         "$AWS_SIGNER_PID" "aws-signer"
    wait_for_port "$AWS_BEDROCK_RUNTIME_PORT" "$AWS_SIGNER_PID" "aws-signer"
}

start_integrations_broker() {
    # Only when deploy_app.py's env-bearer overlay supplied the required
    # identity. Missing any of them = not a personal-assistant deploy (e.g.
    # local dev), so skip silently — but nono-managed clients will then
    # call Google without HTTPS_PROXY set and get ENOTCONN, which is the
    # expected local-dev behavior.
    if [ -z "${DOH_ENV_BEARER:-}" ] || [ -z "${DOH_OWNER_USERNAME:-}" ] || [ -z "${DOH_APP_SLUG:-}" ] || [ -z "${DOH_CONTROL_PLANE_URL:-}" ]; then
        echo "[supervisor] DOH_ENV_BEARER / DOH_OWNER_USERNAME / DOH_APP_SLUG / DOH_CONTROL_PLANE_URL not set; skipping integrations broker"
        return
    fi
    mkdir -p "$INTEGRATIONS_BROKER_CA_DIR" "$INTEGRATIONS_BROKER_PRIVATE_DIR"
    chmod 700 "$INTEGRATIONS_BROKER_PRIVATE_DIR"
    "$HERMES_WEBUI_PYTHON" "${DOH_RUNTIME_DIR}/integrations_broker.py" \
        --proxy-port "$INTEGRATIONS_BROKER_PROXY_PORT" \
        --control-port "$INTEGRATIONS_BROKER_CONTROL_PORT" \
        --mcp-port "$MCP_AGGREGATOR_PORT" \
        --ca-dir "$INTEGRATIONS_BROKER_CA_DIR" \
        --private-dir "$INTEGRATIONS_BROKER_PRIVATE_DIR" &
    INTEGRATIONS_BROKER_PID=$!
    wait_for_port "$INTEGRATIONS_BROKER_PROXY_PORT" "$INTEGRATIONS_BROKER_PID" "integrations-broker-proxy"
    wait_for_port "$INTEGRATIONS_BROKER_CONTROL_PORT" "$INTEGRATIONS_BROKER_PID" "integrations-broker-control"
    wait_for_port "$MCP_AGGREGATOR_PORT" "$INTEGRATIONS_BROKER_PID" "mcp-aggregator"
}

load_gateway_integration_env() {
    GATEWAY_INTEGRATION_ENV=()
    if [ -z "${INTEGRATIONS_BROKER_PID:-}" ]; then
        return
    fi

    local env_output
    if ! env_output=$(INTEGRATIONS_BROKER_CONTROL_PORT="$INTEGRATIONS_BROKER_CONTROL_PORT" "$HERMES_WEBUI_PYTHON" - <<'PY'
import json
import os
import sys
import urllib.request

port = os.environ["INTEGRATIONS_BROKER_CONTROL_PORT"]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/integrations", timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    print(f"[supervisor] could not load gateway integration env: {exc}", file=sys.stderr)
    raise SystemExit(0)

for item in payload.get("items", []):
    if item.get("slug") != "telegram" or item.get("status") != "connected":
        continue
    print("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER")
    allowed_users = item.get("config", {}).get("allowed_users") or []
    if allowed_users:
        print("TELEGRAM_ALLOWED_USERS=" + ",".join(str(user_id) for user_id in allowed_users if str(user_id)))
PY
    ); then
        echo "[supervisor] Failed to load gateway integration env; continuing without messaging env"
        return
    fi

    while IFS= read -r line; do
        if [ -n "$line" ]; then
            GATEWAY_INTEGRATION_ENV+=("$line")
        fi
    done <<< "$env_output"
}

export_webui_extension_env() {
    # Point the WebUI at our extension bundle. EXTENSIONS.md-compliant same-origin
    # URLs — the upstream static handler serves $HERMES_WEBUI_EXTENSION_DIR under
    # /extensions/. These three vars are in the nono profile's allow_vars.
    export HERMES_WEBUI_EXTENSION_SCRIPT_URLS="/extensions/doh-integrations.js,/extensions/doh-webapps.js"
    export HERMES_WEBUI_EXTENSION_STYLESHEET_URLS="/extensions/doh-integrations.css,/extensions/doh-webapps.css"
}

write_child_aws_config() {
    mkdir -p "${HERMES_WEBUI_DEFAULT_WORKSPACE}/.aws"
    cat > "${HERMES_WEBUI_DEFAULT_WORKSPACE}/.aws/config" <<EOF
[default]
region = ${AWS_DEFAULT_REGION}
services = hermes-nono-endpoints

[services hermes-nono-endpoints]
sts =
  endpoint_url = http://127.0.0.1:${AWS_STS_PORT}

bedrock =
  endpoint_url = http://127.0.0.1:${AWS_BEDROCK_PORT}

bedrock_runtime =
  endpoint_url = http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}
EOF

    cat > "${HERMES_WEBUI_DEFAULT_WORKSPACE}/.aws/credentials" <<'EOF'
[default]
aws_access_key_id = dummy
aws_secret_access_key = dummy
EOF
}

write_providers_block() {
    local providers_block_file="$1"

    if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
        cat > "$providers_block_file" <<'EOF'
providers:
  bedrock:
    models:
      'us.anthropic.claude-opus-4-7': "Opus 4.7"
      'us.anthropic.claude-sonnet-4-6': "Sonnet 4.6"
      'us.anthropic.claude-haiku-4-5-20251001-v1:0': "Haiku 4.5"
EOF
        return
    fi

    echo "providers: {}" > "$providers_block_file"
}

render_hermes_config() {
    local doh_llm_base_url="${DOH_LLM_BASE_URL:-}"
    local doh_aux_provider="${DOH_AUX_PROVIDER:-$DOH_LLM_PROVIDER}"
    local doh_aux_model="${DOH_AUX_MODEL:-$DOH_LLM_MODEL}"
    local doh_aux_base_url="${DOH_AUX_BASE_URL:-}"
    local providers_block_file

    if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
        doh_llm_base_url="https://bedrock-runtime.${AWS_DEFAULT_REGION}.amazonaws.com"
    fi

    providers_block_file=$(mktemp)
    write_providers_block "$providers_block_file"

    sed \
        -e "s|__CONFIG_PROVIDER__|${DOH_LLM_PROVIDER}|g" \
        -e "s|__MODEL__|${DOH_LLM_MODEL}|g" \
        -e "s|__BASE_URL__|${doh_llm_base_url}|g" \
        -e "s|__AUX_PROVIDER__|${doh_aux_provider}|g" \
        -e "s|__AUX_MODEL__|${doh_aux_model}|g" \
        -e "s|__AUX_BASE_URL__|${doh_aux_base_url}|g" \
        -e "s|__HERMES_WEBUI_AGENT_SKILLS_DIR__|${HERMES_WEBUI_AGENT_DIR}/skills|g" \
        -e "/__PROVIDERS_BLOCK__/r ${providers_block_file}" \
        -e "/__PROVIDERS_BLOCK__/d" \
        "$HERMES_CONFIG_TEMPLATE" > "$HERMES_HOME/config.yaml"
    rm -f "$providers_block_file"

    if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
        cat >> "$HERMES_HOME/config.yaml" <<EOF

bedrock:
  region: ${AWS_DEFAULT_REGION}
EOF
    fi
}

ensure_user_runtime_dirs() {
    mkdir -p "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE" "${HERMES_HOME}/skills" "$DOH_RUN_DIR"
    if [ -f /opt/hermes/SOUL.md ] && [ ! -f "$HERMES_HOME/SOUL.md" ]; then
        cp /opt/hermes/SOUL.md "$HERMES_HOME/SOUL.md"
    fi
}

chown_sandbox_paths() {
    # The sandbox runs as hermeswebui (see run_in_nono). Everything we wrote
    # above ran as root, so hand the paths the sandbox needs to read or write
    # to hermeswebui. The integrations broker's CA bundle stays root-owned but
    # is mode 0644 (see tls_intercept._write_bundle), so no chown needed there.
    chown -R hermeswebui:hermeswebui "$HERMES_WEBUI_DEFAULT_WORKSPACE"
}

run_in_nono() {
    local nono_args=(run --profile "${DOH_RUNTIME_DIR}/hermes-nono-profile.json")

    if [ -n "${TAVILY_API_KEY:-}" ]; then
        # Tavily uses JSON payload, which nono doesn't support in its credential injection mechanism. 
        # It is acceptable to pass it to Hermes because Hermes strips this particular env var in all 
        # its tool calls (except web_search). We use "--env-credential-map" instead of allow_vars 
        # in the nono profile because this makes explicit that this is a credential.
        nono_args+=(--env-credential-map env://TAVILY_API_KEY TAVILY_API_KEY)
    fi

    # HTTPS_PROXY + SSL_CERT_FILE route in-sandbox clients (gws, curl, etc.)
    # through the integrations broker, which injects per-user access tokens
    # and forwards to real upstreams. NO_PROXY keeps loopback direct so the
    # sandbox can still reach the AWS signer on 9901-9903 and the broker
    # itself on 9950/9951 without a proxy round-trip.
    local broker_env=()
    if [ -n "${INTEGRATIONS_BROKER_PID:-}" ]; then
        broker_env+=(
            "HTTPS_PROXY=http://127.0.0.1:${INTEGRATIONS_BROKER_PROXY_PORT}"
            "SSL_CERT_FILE=${INTEGRATIONS_BROKER_CA_DIR}/bundle.pem"
            # Git uses libcurl built against OpenSSL but doesn't honor
            # SSL_CERT_FILE on Debian — it has its own knob. Without this,
            # `git clone https://github.com/...` fails verification of the
            # broker's MITM leaf cert with "certificate signer not trusted".
            "GIT_SSL_CAINFO=${INTEGRATIONS_BROKER_CA_DIR}/bundle.pem"
            # Same placeholder swap as the git credential helper, but for
            # `gh`: gh reads GITHUB_TOKEN, sends `Authorization: token
            # DOH_PLACEHOLDER` to api.github.com, the broker swaps it for
            # the user's real GitHub user-to-server token before forwarding.
            # No real token ever lives in the sandbox.
            "GITHUB_TOKEN=DOH_PLACEHOLDER"
        )
    fi

    local doh_login_path="${HERMES_WEBUI_DEFAULT_WORKSPACE}/.venv/bin:${DOH_BIN_DIR}:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"

    # Drop privileges to hermeswebui before launching the sandbox so the LLM,
    # terminal, and execute_code all run as UID 1024. Combined with nono's
    # bounding set (CAP_SYS_PTRACE dropped), this makes /proc/<pid>/environ on
    # supervisor's root-owned children (aws_signer, integrations_broker)
    # unreadable from inside the sandbox. supervisor itself stays root so it
    # can still signal those daemons during cleanup.
    #
    # VIRTUAL_ENV + venv on PATH wire the user venv (created in the Dockerfile)
    # into both tools: terminal resolves python/pip via PATH, execute_code's
    # project mode walks $VIRTUAL_ENV when picking the child interpreter
    # (hermes-agent tools/code_execution_tool.py:_resolve_child_python).
    # HERMES_WEBUI_PORT=8789 frees 8787 for Caddy. Public traffic flows:
    #   ALB → policy-proxy:8788 (auth gate) → Caddy:8787 → user app on 4xxx
    #                                                   ↘ fallback → WebUI:8789
    # 8788 is owned by policy-proxy (separate container, shared net ns), so
    # WebUI cannot use it; 8787 is now owned by Caddy.
    runuser -u hermeswebui -- "$DOH_BIN_DIR/nono" "${nono_args[@]}" -- /usr/bin/env \
        ANTHROPIC_BEDROCK_BASE_URL="http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}" \
        AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
        AWS_EC2_METADATA_DISABLED=true \
        HOME="$HERMES_WEBUI_DEFAULT_WORKSPACE" \
        HERMES_WEBUI_PORT=8789 \
        HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew \
        HOMEBREW_NO_ANALYTICS=1 \
        HOMEBREW_NO_AUTO_UPDATE=1 \
        HOMEBREW_INSTALL_FROM_API=1 \
        VIRTUAL_ENV="${HERMES_WEBUI_DEFAULT_WORKSPACE}/.venv" \
        DOH_LOGIN_PATH="$doh_login_path" \
        PATH="$doh_login_path" \
        NO_PROXY=127.0.0.1,localhost \
        "${broker_env[@]}" \
        "${GATEWAY_INTEGRATION_ENV[@]}" \
        "$@"
}

main() {
    require_llm_config
    require_aws_region

    # === Stage 1: root setup. Render configs, start the credential-holding
    # daemons (aws_signer, integrations_broker) — these stay root so the LLM
    # can never read their /proc/<pid>/environ. Anything written here that the
    # sandbox needs to read is chowned to hermeswebui in Stage 2 prep.
    ensure_user_runtime_dirs
    render_hermes_config
    start_aws_signer
    write_child_aws_config
    start_integrations_broker
    load_gateway_integration_env
    export_webui_extension_env

    # === Stage 2: prep + launch the sandbox. supervisor stays root (it owns
    # the daemons started above), but everything inside nono runs as
    # hermeswebui — see run_in_nono.
    chown_sandbox_paths
    run_in_nono "$@" &
    NONO_PID=$!
    wait "$NONO_PID"
}

main "$@"
