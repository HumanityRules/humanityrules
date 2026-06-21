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
#   ./prod_manage.sh doh_control create-env --aws-account "DevOps Hero AWS Account" --name default --region us-east-1 --hosted-zone humanityrules.io --provision
#   ./prod_manage.sh shell
#   ./prod_manage.sh dbshell
#
# Examples (local-exec, target resolved from prod):
#   ./prod_manage.sh doh_build_prebuilt_image --account "Humanity Rules Sandbox" --env production --source-dir template_repos/doh_dind --ecr-repo doh-dind --tag 0.2.5
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
    echo "  ./prod_manage.sh doh_build_prebuilt_image --account \"Humanity Rules Sandbox\" --env production --source-dir template_repos/doh_dind --ecr-repo doh-dind --tag 0.2.5"
    exit 1
fi

if is_local_exec "$1"; then
    exec python3 "$(dirname "$0")/prod_dispatch_local.py" "$@"
fi

# Load AWS credentials from .env
if [ -f "../.env" ]; then
    export HUMR_AWS_ACCESS_KEY=$(grep -E '^HUMR_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_AWS_SECRET_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
fi

export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

# Find a task that's actually ready for ECS exec.
#
# `list-tasks` returns ARNs as soon as the scheduler accepts them — well
# before the container is up — and the order is not a readiness contract.
# Picking `taskArns[0]` raw can land on a PROVISIONING/PENDING task, on a
# task whose ExecuteCommandAgent sidecar hasn't started yet, or (during a
# rolling deploy) on the old task about to be drained. All three produce
# `TargetNotConnectedException` or "execute command agent isn't running".
#
# We poll up to 3 minutes for a task that has:
#   - lastStatus=RUNNING
#   - healthStatus=HEALTHY  (devopshero container has a real curl health check)
#   - ExecuteCommandAgent.lastStatus=RUNNING
# When more than one matches (rolling deploy window), prefer the newest by
# startedAt so we attach to the new task, not the one about to be killed.
TASK_ARN=""
DEADLINE=$(( $(date +%s) + 180 ))
echo "Waiting for a ready task in doh-prod-app..."
while :; do
    TASK_ARNS=$(aws ecs list-tasks \
        --cluster doh-prod-cluster \
        --service-name doh-prod-app \
        --desired-status RUNNING \
        --query 'taskArns' \
        --output text)
    if [ -n "$TASK_ARNS" ] && [ "$TASK_ARNS" != "None" ]; then
        # Of all matching tasks, pick the newest one that's fully ready.
        TASK_ARN=$(aws ecs describe-tasks \
            --cluster doh-prod-cluster \
            --tasks $TASK_ARNS \
            --query 'reverse(sort_by(tasks[?lastStatus==`RUNNING` && healthStatus==`HEALTHY` && (containers[?name==`devopshero`] | [0].managedAgents[?name==`ExecuteCommandAgent`] | [0].lastStatus) == `RUNNING`], &startedAt))[0].taskArn' \
            --output text)
        [ -n "$TASK_ARN" ] && [ "$TASK_ARN" != "None" ] && break
    fi
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
        echo "Error: no ready task in doh-prod-app within 180s"
        echo "       (need lastStatus=RUNNING + healthStatus=HEALTHY + ExecuteCommandAgent=RUNNING)"
        exit 1
    fi
    sleep 3
done

echo "Connecting to task: ${TASK_ARN##*/}"
echo "Running: manage.py $@"
echo "---"

# Build the bash-quoted command, then base64-encode it so the value we hand to
# `--command` contains zero shell-special characters (no quotes, no spaces in
# the payload — only `[A-Za-z0-9+/=]` plus the literal `bash`, `-c`, `eval`,
# `echo`, `base64`, and `-d`). Earlier attempts to pass quotes through the
# chain failed because `--command` traverses multiple shell-parsing layers
# (local bash, the SSM agent's argv split, and the remote `bash -c`), and
# bash's `'\''` escape only survives one of those. With base64 there is
# nothing to mangle: only the final `eval` sees real quotes.
#
# We use `eval "$(echo … | base64 -d)"` rather than piping into `bash` so the
# decoded command inherits the SSM session's stdin. A `… | bash` pipeline
# would wire the inner shell's stdin to the closed end of the base64 pipe and
# every interactive command (`shell`, `dbshell`, anything reading stdin) would
# die instantly with `Cannot perform start session: EOF`.
# Customer-account commands (doh_app_shell, doh_app_logs, …) assume roles via
# HUMR_AWS_* creds. The prod container has no .env file — forward from the
# operator's laptop so Django settings pick them up inside ECS exec.
FULL_CMD="HUMR_AWS_ACCESS_KEY=$(printf '%q' "$HUMR_AWS_ACCESS_KEY") HUMR_AWS_SECRET_KEY=$(printf '%q' "$HUMR_AWS_SECRET_KEY") uv run python manage.py"
for arg in "$@"; do
    FULL_CMD+=" $(printf '%q' "$arg")"
done
B64=$(printf '%s' "$FULL_CMD" | base64 | tr -d '\n')

# Execute command via ECS exec.
#
# `aws ecs execute-command --interactive` always pipes the operator's stdin
# into the SSM data channel; when the calling shell has no TTY (CI, agent
# harness, `</dev/null`), that pipe closes immediately and the local
# session-manager-plugin prints `Cannot perform start session: EOF` after the
# command's real output. To avoid that, when stdin is not a TTY we wrap the
# AWS CLI in `script(1)` so it sees a real PTY. macOS and util-linux disagree
# on `script` argument order, so we branch on `uname`.
run_aws_exec() {
    aws ecs execute-command \
        --cluster doh-prod-cluster \
        --task "${TASK_ARN}" \
        --container devopshero \
        --interactive \
        --command "bash -c 'eval \"\$(echo $B64 | base64 -d)\"'"
}

if [ -t 0 ]; then
    run_aws_exec
else
    # Build the AWS command as an array so `script` invokes it directly
    # (no nested shell-quoting concerns).
    AWS_CMD=(
        aws ecs execute-command
        --cluster doh-prod-cluster
        --task "${TASK_ARN}"
        --container devopshero
        --interactive
        --command "bash -c 'eval \"\$(echo $B64 | base64 -d)\"'"
    )
    # `script` itself fails to initialize a PTY if its own stdin is a socket
    # (e.g. when invoked from an agent harness). We don't need any input
    # forwarding in this branch — we're here precisely because stdin is not a
    # TTY — so feed it /dev/null.
    if [ "$(uname -s)" = "Darwin" ]; then
        # macOS: script -q <typescript-file> <command...>
        script -q /dev/null "${AWS_CMD[@]}" </dev/null
    else
        # util-linux: script -q -c "<cmdline>" <typescript-file>
        # Re-quote each argv element for the embedded shell.
        CMDLINE=""
        for a in "${AWS_CMD[@]}"; do CMDLINE+=" $(printf '%q' "$a")"; done
        script -q -c "${CMDLINE# }" /dev/null </dev/null
    fi
fi
