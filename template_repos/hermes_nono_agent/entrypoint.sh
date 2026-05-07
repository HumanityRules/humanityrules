#!/bin/bash
set -euo pipefail

AWS_BROKER_REGION="${AWS_BEDROCK_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
AWS_SIGV4_PROXY_PORT=9911
AWS_STS_PORT=9901
AWS_BEDROCK_PORT=9902
AWS_BEDROCK_RUNTIME_PORT=9903
AWS_HAPROXY_CONFIG=/tmp/hermes-nono-aws-haproxy.cfg
AWS_SIGNER_HOME=/tmp/hermes-nono-sigv4-home
AWS_CHILD_CONFIG_DIR="${HOME}/.aws"
AWS_CHILD_CONFIG_FILE="${AWS_CHILD_CONFIG_DIR}/config"
AWS_CHILD_CREDENTIALS_FILE="${AWS_CHILD_CONFIG_DIR}/credentials"
NONO_PROFILE=/etc/nono/profiles/hermes-nono.json
SIGV4_PID=""
HAPROXY_PID=""

cleanup() {
    set +e
    if [ -n "$HAPROXY_PID" ] && kill -0 "$HAPROXY_PID" 2>/dev/null; then
        kill "$HAPROXY_PID"
    fi
    if [ -n "$SIGV4_PID" ] && kill -0 "$SIGV4_PID" 2>/dev/null; then
        kill "$SIGV4_PID"
    fi
}

trap cleanup EXIT INT TERM

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

write_child_aws_config() {
    mkdir -p "$AWS_CHILD_CONFIG_DIR"
    cat > "$AWS_CHILD_CONFIG_FILE" <<EOF
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

    cat > "$AWS_CHILD_CREDENTIALS_FILE" <<'EOF'
[default]
aws_access_key_id = dummy
aws_secret_access_key = dummy
EOF
}

start_aws_broker() {
    mkdir -p "$AWS_SIGNER_HOME"
    touch "$AWS_SIGNER_HOME/config" "$AWS_SIGNER_HOME/credentials"

    env \
        -u AWS_ACCESS_KEY_ID \
        -u AWS_SECRET_ACCESS_KEY \
        -u AWS_SESSION_TOKEN \
        HOME="$AWS_SIGNER_HOME" \
        AWS_CONFIG_FILE="$AWS_SIGNER_HOME/config" \
        AWS_SHARED_CREDENTIALS_FILE="$AWS_SIGNER_HOME/credentials" \
        /usr/local/bin/aws-sigv4-proxy --port "127.0.0.1:${AWS_SIGV4_PROXY_PORT}" &
    SIGV4_PID=$!
    wait_for_port "$AWS_SIGV4_PROXY_PORT" "$SIGV4_PID" "aws-sigv4-proxy"

    sed "s|__AWS_REGION__|${AWS_BROKER_REGION}|g" \
        /etc/haproxy/aws-endpoints.cfg.template > "$AWS_HAPROXY_CONFIG"
    /usr/sbin/haproxy -f "$AWS_HAPROXY_CONFIG" -db &
    HAPROXY_PID=$!
    wait_for_port "$AWS_STS_PORT" "$HAPROXY_PID" "haproxy"
    wait_for_port "$AWS_BEDROCK_PORT" "$HAPROXY_PID" "haproxy"
    wait_for_port "$AWS_BEDROCK_RUNTIME_PORT" "$HAPROXY_PID" "haproxy"
}

run_in_nono() {
    nono run --profile "$NONO_PROFILE" -- env \
        -u AWS_CONFIG_FILE \
        -u AWS_SHARED_CREDENTIALS_FILE \
        -u AWS_PROFILE \
        -u AWS_SESSION_TOKEN \
        -u AWS_CONTAINER_CREDENTIALS_FULL_URI \
        -u AWS_CONTAINER_CREDENTIALS_RELATIVE_URI \
        -u AWS_CONTAINER_AUTHORIZATION_TOKEN \
        -u AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE \
        AWS_ACCESS_KEY_ID=dummy \
        AWS_SECRET_ACCESS_KEY=dummy \
        AWS_DEFAULT_REGION="$AWS_BROKER_REGION" \
        AWS_REGION="$AWS_BROKER_REGION" \
        AWS_BEDROCK_REGION="$AWS_BROKER_REGION" \
        ANTHROPIC_BEDROCK_BASE_URL="http://127.0.0.1:${AWS_BEDROCK_RUNTIME_PORT}" \
        AWS_EC2_METADATA_DISABLED=true \
        NO_PROXY=127.0.0.1,localhost \
        no_proxy=127.0.0.1,localhost \
        "$@"
}

if [ -z "$DOH_LLM_PROVIDER" ] || [ -z "$DOH_LLM_MODEL" ]; then
    echo "FATAL: DOH_LLM_PROVIDER and DOH_LLM_MODEL must be set" >&2
    exit 1
fi

if [ "$DOH_LLM_PROVIDER" = "bedrock" ]; then
    if [ -z "$AWS_BEDROCK_REGION" ]; then
        echo "FATAL: DOH_LLM_PROVIDER=bedrock requires AWS_BEDROCK_REGION" >&2
        exit 1
    fi
    AWS_BROKER_REGION="$AWS_BEDROCK_REGION"
    export AWS_REGION="$AWS_BROKER_REGION"
    export AWS_DEFAULT_REGION="$AWS_BROKER_REGION"
    DOH_LLM_BASE_URL="https://bedrock-runtime.${AWS_BROKER_REGION}.amazonaws.com"
fi

: "${DOH_LLM_BASE_URL:=}"
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
  region: ${AWS_BROKER_REGION}
EOF
fi

rm -rf "$HERMES_DIR/hermes-agent"
cp -r /opt/hermes-defaults/hermes-agent "$HERMES_DIR/hermes-agent"

if [ ! -f "$HERMES_DIR/SOUL.md" ]; then
    cp /opt/hermes-defaults/SOUL.md "$HERMES_DIR/SOUL.md"
fi

start_aws_broker
write_child_aws_config

run_in_nono "$@" &
NONO_PID=$!
wait "$NONO_PID"
