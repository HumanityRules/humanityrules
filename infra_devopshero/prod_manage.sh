#!/bin/bash
#
# Run Django management commands against production.
#
# Most commands ECS-exec into the prod app container (DB ops, control plane).
# A few "local-exec" commands (Docker builds, etc.) need the operator's machine
# but want prod's DB as the source of truth — those are dispatched to
# prod_dispatch_local.py, which fetches target metadata from prod and runs the
# command locally. From the operator's perspective the entry point is the same.
#
# Usage:
#   ./prod_manage.sh <command> [args...]
#
# Examples (ECS-exec into prod):
#   ./prod_manage.sh doh_query Environment
#   ./prod_manage.sh doh_control create-env --aws-account "DevOps Hero AWS Account" --name default --region us-east-1 --hosted-zone devopshero.co --provision
#   ./prod_manage.sh shell
#   ./prod_manage.sh dbshell
#
# Examples (local-exec, target resolved from prod):
#   ./prod_manage.sh doh_build_prebuilt_image --account "Course Hero Sandbox" --env production --source-dir template_repos/doh_dind --ecr-repo doh-dind --tag 0.2.5
#
set -e

# Commands that must run on the operator's local machine but need target
# metadata (account_id, external_id, region, env_slug) sourced from prod's DB.
# Add more as their use cases arise.
LOCAL_EXEC_COMMANDS=("doh_build_prebuilt_image")

is_local_exec() {
    local cmd="$1"
    for c in "${LOCAL_EXEC_COMMANDS[@]}"; do
        [ "$c" = "$cmd" ] && return 0
    done
    return 1
}

if [ $# -eq 0 ]; then
    echo "Usage: ./prod_manage.sh <command> [args...]"
    echo ""
    echo "Examples (ECS-exec):"
    echo "  ./prod_manage.sh doh_query Environment"
    echo "  ./prod_manage.sh doh_control create-env --aws-account \"Name\" --name default --region us-east-1"
    echo "  ./prod_manage.sh shell"
    echo ""
    echo "Examples (local-exec, target resolved from prod):"
    echo "  ./prod_manage.sh doh_build_prebuilt_image --account \"Course Hero Sandbox\" --env production --source-dir template_repos/doh_dind --ecr-repo doh-dind --tag 0.2.5"
    exit 1
fi

if is_local_exec "$1"; then
    exec python3 "$(dirname "$0")/prod_dispatch_local.py" "$@"
fi

# Load AWS credentials from .env
if [ -f "../.env" ]; then
    export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
fi

export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

# Find running task
TASK_ARN=$(aws ecs list-tasks \
    --cluster doh-prod-cluster \
    --service-name doh-prod-app \
    --query 'taskArns[0]' \
    --output text)

if [ "$TASK_ARN" = "None" ] || [ -z "$TASK_ARN" ]; then
    echo "Error: No running tasks found for doh-prod-app"
    exit 1
fi

echo "Connecting to task: ${TASK_ARN##*/}"
echo "Running: manage.py $@"
echo "---"

# Build the bash-quoted command, then base64-encode it so the value we hand to
# `--command` contains zero shell-special characters (no quotes, no spaces in
# the payload — only `[A-Za-z0-9+/=]` plus the literal `bash`, `-c`, `echo`,
# `base64`, and `-d`). Earlier attempts to pass quotes through the chain failed
# because `--command` traverses multiple shell-parsing layers (local bash, the
# SSM agent's argv split, and the remote `bash -c`), and bash's `'\''` escape
# only survives one of those. With base64 there is nothing to mangle: only the
# final `bash` after `base64 -d` sees real quotes.
FULL_CMD="uv run python manage.py"
for arg in "$@"; do
    FULL_CMD+=" $(printf '%q' "$arg")"
done
B64=$(printf '%s' "$FULL_CMD" | base64 | tr -d '\n')

# Execute command via ECS exec
aws ecs execute-command \
    --cluster doh-prod-cluster \
    --task "${TASK_ARN}" \
    --container devopshero \
    --interactive \
    --command "bash -c \"echo $B64 | base64 -d | bash\""
