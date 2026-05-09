#!/bin/bash
set -euo pipefail

AWS_BROKER_REGION=""
AWS_SIGV4_PROXY_PORT=9911
AWS_STS_PORT=9901
AWS_BEDROCK_PORT=9902
AWS_BEDROCK_RUNTIME_PORT=9903
AWS_HAPROXY_CONFIG=/tmp/hermes-nono-aws-haproxy.cfg
CHILD_HOME=/workspace
NONO_PROFILE=/etc/nono/profiles/hermes-nono-profile.json
GOOGLE_TOKEN_REFRESHER=/google_token_refresher.py
SIGV4_PID=""
HAPROXY_PID=""
GOOGLE_REFRESHER_PID=""

die() {
    echo "FATAL: $*" >&2
    exit 1
}

cleanup() {
    set +e
    if [ -n "$GOOGLE_REFRESHER_PID" ] && kill -0 "$GOOGLE_REFRESHER_PID" 2>/dev/null; then
        kill "$GOOGLE_REFRESHER_PID"
    fi
    if [ -n "$HAPROXY_PID" ] && kill -0 "$HAPROXY_PID" 2>/dev/null; then
        kill "$HAPROXY_PID"
    fi
    if [ -n "$SIGV4_PID" ] && kill -0 "$SIGV4_PID" 2>/dev/null; then
        kill "$SIGV4_PID"
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

start_sigv4_proxy() {
    /usr/local/bin/aws-sigv4-proxy --port "127.0.0.1:${AWS_SIGV4_PROXY_PORT}" &
    SIGV4_PID=$!
    wait_for_port "$AWS_SIGV4_PROXY_PORT" "$SIGV4_PID" "aws-sigv4-proxy"
}

start_haproxy() {
    sed "s|__AWS_BROKER_REGION__|${AWS_BROKER_REGION}|g" \
        /etc/haproxy/haproxy.cfg.template > "$AWS_HAPROXY_CONFIG"
    /usr/sbin/haproxy -f "$AWS_HAPROXY_CONFIG" -db &
    HAPROXY_PID=$!
    wait_for_port "$AWS_STS_PORT" "$HAPROXY_PID" "haproxy"
    wait_for_port "$AWS_BEDROCK_PORT" "$HAPROXY_PID" "haproxy"
    wait_for_port "$AWS_BEDROCK_RUNTIME_PORT" "$HAPROXY_PID" "haproxy"
}

start_aws_broker() {
    start_sigv4_proxy
    start_haproxy
}

start_google_token_refresher() {
    # Only when deploy_app.py's env-bearer overlay supplied the identity
    # triple. Missing any of them = not a personal-assistant deploy (e.g.
    # local dev), so skip silently.
    if [ -z "${DOH_ENV_BEARER:-}" ] || [ -z "${DOH_OWNER_USERNAME:-}" ] || [ -z "${DOH_CONTROL_PLANE_URL:-}" ]; then
        echo "[supervisor] DOH_ENV_BEARER / DOH_OWNER_USERNAME / DOH_CONTROL_PLANE_URL not set; skipping google token refresher"
        return
    fi
    /app/venv/bin/python "$GOOGLE_TOKEN_REFRESHER" &
    GOOGLE_REFRESHER_PID=$!
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

    nono "${nono_args[@]}" -- /usr/bin/env \
        ANTHROPIC_BEDROCK_BASE_URL="http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}" \
        AWS_DEFAULT_REGION="$AWS_BROKER_REGION" \
        AWS_EC2_METADATA_DISABLED=true \
        HOME="$CHILD_HOME" \
        PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
        NO_PROXY=127.0.0.1,localhost \
        "$@"
}

main() {
    require_llm_config
    configure_aws_region
    render_hermes_config
    start_aws_broker
    write_child_aws_config
    start_google_token_refresher

    run_in_nono "$@" &
    NONO_PID=$!
    wait "$NONO_PID"
}

main "$@"
