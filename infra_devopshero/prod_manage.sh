#!/bin/bash
#
# Run Django management commands on the production ECS container.
#
# Usage:
#   ./prod_manage.sh <command> [args...]
#
# Examples:
#   ./prod_manage.sh doh_query Environment
#   ./prod_manage.sh doh_control create-env --aws-account "DevOps Hero AWS Account" --name default --region us-east-1 --hosted-zone devopshero.co --provision
#   ./prod_manage.sh shell
#   ./prod_manage.sh dbshell
#
set -e

# Check for at least one argument
if [ $# -eq 0 ]; then
    echo "Usage: ./prod_manage.sh <command> [args...]"
    echo ""
    echo "Examples:"
    echo "  ./prod_manage.sh doh_query Environment"
    echo "  ./prod_manage.sh doh_control create-env --aws-account \"Name\" --name default --region us-east-1"
    echo "  ./prod_manage.sh shell"
    exit 1
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

# Build the command - escape arguments properly
MANAGE_CMD="uv run python manage.py"
for arg in "$@"; do
    # Escape single quotes in arguments
    escaped_arg=$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")
    MANAGE_CMD="$MANAGE_CMD '$escaped_arg'"
done

# Execute command via ECS exec
aws ecs execute-command \
    --cluster doh-prod-cluster \
    --task "${TASK_ARN}" \
    --container devopshero \
    --interactive \
    --command "/bin/bash -c \"$MANAGE_CMD\""
