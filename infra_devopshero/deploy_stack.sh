#!/bin/bash
set -e

# Deploy a single CDK stack
# Usage: ./deploy_stack.sh <stack-name>
# Example: ./deploy_stack.sh doh-prod-storage

if [ -z "$1" ]; then
    echo "Usage: ./deploy_stack.sh <stack-name>"
    echo "Example: ./deploy_stack.sh doh-prod-storage"
    exit 1
fi

STACK_NAME="$1"

# Load environment variables from .env (selective to avoid issues with multi-line values like GITHUB_APP_PRIVATE_KEY)
if [ -f "../.env" ]; then
    export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_AWS_ACCOUNT_ID=$(grep -E '^DOH_AWS_ACCOUNT_ID=' ../.env | cut -d'=' -f2-)
    export DOH_API_SECRET_KEY=$(grep -E '^DOH_API_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_API_ENDPOINT=$(grep -E '^DOH_API_ENDPOINT=' ../.env | cut -d'=' -f2-)
fi

# Map to AWS CLI expected names
export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

echo "Deploying ${STACK_NAME}..."
cdk deploy "${STACK_NAME}" --require-approval never

echo ""
echo "Done! ${STACK_NAME} deployed successfully."
