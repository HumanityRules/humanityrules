#!/bin/bash
set -euo pipefail

AWS_BROKER_REGION=""
AWS_STS_PORT=9901
AWS_BEDROCK_PORT=9902
AWS_BEDROCK_RUNTIME_PORT=9903
AWS_SIGNER=/aws_signer.py
CHILD_HOME=/workspace
NONO_PROFILE=/etc/nono/profiles/hermes-nono-profile.json
INTEGRATIONS_BROKER=/integrations_broker.py
INTEGRATIONS_BROKER_CA_DIR=/opt/doh/ca
INTEGRATIONS_BROKER_PRIVATE_DIR=/opt/doh/broker-private
INTEGRATIONS_BROKER_PROXY_PORT=9950
INTEGRATIONS_BROKER_CONTROL_PORT=9951
WEBUI_EXTENSION_DIR=/opt/doh/webui-extension
AWS_SIGNER_PID=""
INTEGRATIONS_BROKER_PID=""

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

configure_aws_region() {
    if [ -z "${AWS_DEFAULT_REGION:-}" ]; then
        die "AWS_DEFAULT_REGION must be set"
    fi

    AWS_BROKER_REGION="$AWS_DEFAULT_REGION"
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
    /app/venv/bin/python3 "$AWS_SIGNER" --region "$AWS_BROKER_REGION" &
    AWS_SIGNER_PID=$!
    wait_for_port "$AWS_STS_PORT"             "$AWS_SIGNER_PID" "aws-signer"
    wait_for_port "$AWS_BEDROCK_PORT"         "$AWS_SIGNER_PID" "aws-signer"
    wait_for_port "$AWS_BEDROCK_RUNTIME_PORT" "$AWS_SIGNER_PID" "aws-signer"
}

start_integrations_broker() {
    # Only when deploy_app.py's env-bearer overlay supplied the identity
    # triple. Missing any of them = not a personal-assistant deploy (e.g.
    # local dev), so skip silently — but nono-managed clients will then
    # call Google without HTTPS_PROXY set and get ENOTCONN, which is the
    # expected local-dev behavior.
    if [ -z "${DOH_ENV_BEARER:-}" ] || [ -z "${DOH_OWNER_USERNAME:-}" ] || [ -z "${DOH_CONTROL_PLANE_URL:-}" ]; then
        echo "[supervisor] DOH_ENV_BEARER / DOH_OWNER_USERNAME / DOH_CONTROL_PLANE_URL not set; skipping integrations broker"
        return
    fi
    mkdir -p "$INTEGRATIONS_BROKER_CA_DIR"
    /app/venv/bin/python "$INTEGRATIONS_BROKER" \
        --proxy-port "$INTEGRATIONS_BROKER_PROXY_PORT" \
        --control-port "$INTEGRATIONS_BROKER_CONTROL_PORT" \
        --ca-dir "$INTEGRATIONS_BROKER_CA_DIR" \
        --private-dir "$INTEGRATIONS_BROKER_PRIVATE_DIR" &
    INTEGRATIONS_BROKER_PID=$!
    wait_for_port "$INTEGRATIONS_BROKER_PROXY_PORT" "$INTEGRATIONS_BROKER_PID" "integrations-broker-proxy"
    wait_for_port "$INTEGRATIONS_BROKER_CONTROL_PORT" "$INTEGRATIONS_BROKER_PID" "integrations-broker-control"
}

export_webui_extension_env() {
    # Point the WebUI at our extension bundle. EXTENSIONS.md-compliant same-origin
    # URLs — the upstream static handler serves $HERMES_WEBUI_EXTENSION_DIR under
    # /extensions/. These three vars are in the nono profile's allow_vars.
    export HERMES_WEBUI_EXTENSION_DIR="$WEBUI_EXTENSION_DIR"
    export HERMES_WEBUI_EXTENSION_SCRIPT_URLS="/extensions/doh.js"
    export HERMES_WEBUI_EXTENSION_STYLESHEET_URLS="/extensions/doh.css"
}

write_child_aws_config() {
    mkdir -p "${CHILD_HOME}/.aws"
    cat > "${CHILD_HOME}/.aws/config" <<EOF
[default]
region = ${AWS_BROKER_REGION}
services = hermes-nono-endpoints

[services hermes-nono-endpoints]
sts =
  endpoint_url = http://127.0.0.1:${AWS_STS_PORT}

bedrock =
  endpoint_url = http://127.0.0.1:${AWS_BEDROCK_PORT}

bedrock_runtime =
  endpoint_url = http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}
EOF

    cat > "${CHILD_HOME}/.aws/credentials" <<'EOF'
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
        doh_llm_base_url="https://bedrock-runtime.${AWS_BROKER_REGION}.amazonaws.com"
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
        -e "/__PROVIDERS_BLOCK__/r ${providers_block_file}" \
        -e "/__PROVIDERS_BLOCK__/d" \
        /opt/hermes-defaults/config.yaml.template > "$HERMES_HOME/config.yaml"
    rm -f "$providers_block_file"

    if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
        cat >> "$HERMES_HOME/config.yaml" <<EOF

bedrock:
  region: ${AWS_BROKER_REGION}
EOF
    fi
}

run_in_nono() {
    local nono_args=(run --profile "$NONO_PROFILE")

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
    # sandbox can still reach the AWS haproxy on 9901-9903 and the broker
    # itself on 9950/9951 without a proxy round-trip.
    local broker_env=()
    if [ -n "${INTEGRATIONS_BROKER_PID:-}" ]; then
        broker_env+=(
            "HTTPS_PROXY=http://127.0.0.1:${INTEGRATIONS_BROKER_PROXY_PORT}"
            "SSL_CERT_FILE=${INTEGRATIONS_BROKER_CA_DIR}/bundle.pem"
        )
    fi

    nono "${nono_args[@]}" -- /usr/bin/env \
        ANTHROPIC_BEDROCK_BASE_URL="http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}" \
        AWS_DEFAULT_REGION="$AWS_BROKER_REGION" \
        AWS_EC2_METADATA_DISABLED=true \
        HOME="$CHILD_HOME" \
        PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
        NO_PROXY=127.0.0.1,localhost \
        "${broker_env[@]}" \
        "$@"
}

main() {
    require_llm_config
    configure_aws_region
    render_hermes_config
    start_aws_signer
    write_child_aws_config
    start_integrations_broker
    export_webui_extension_env

    run_in_nono "$@" &
    NONO_PID=$!
    wait "$NONO_PID"
}

main "$@"
