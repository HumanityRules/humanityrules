#!/bin/bash
#
# Tail the HUMR prod ECS log group (/humr/prod/ecs).
#
# Usage:
#   ./tail_prod.sh [aws-logs-tail-flags...]
#
# Defaults to --follow over the whole log group (both app and migration
# containers). Pass any extra `aws logs tail` flags to override or extend.
# The log group is fixed; everything else is pass-through.
#
# Examples:
#   ./tail_prod.sh                                              # all containers, live
#   ./tail_prod.sh --log-stream-name-prefix humr          # app container only
#   ./tail_prod.sh --log-stream-name-prefix migrate             # migration container only
#   ./tail_prod.sh --since 5m                                   # last 5 min, then follow
#   ./tail_prod.sh --filter-pattern '?ERROR ?Exception ?Traceback'
#   ./tail_prod.sh --since 1h --no-follow                       # one-shot dump of last hour
#
# Note: --filter-pattern matches log MESSAGE content, NOT stream names. To
# scope by container, use --log-stream-name-prefix instead.
#
set -e

LOG_GROUP="/humr/prod/ecs"

if [ -f "../.env" ]; then
    export HUMR_AWS_ACCESS_KEY=$(grep -E '^HUMR_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_AWS_SECRET_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
fi

export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

# Default: --follow. User can pass --no-follow (our own flag; stripped before
# exec) for a one-shot dump.
NO_FOLLOW=false
PASSTHROUGH=()
for arg in "$@"; do
    case "$arg" in
        --no-follow) NO_FOLLOW=true ;;
        *) PASSTHROUGH+=("$arg") ;;
    esac
done

CMD=(aws logs tail "$LOG_GROUP")
$NO_FOLLOW || CMD+=(--follow)
CMD+=("${PASSTHROUGH[@]}")

echo "Running: ${CMD[*]}"
echo "---"
exec "${CMD[@]}"
