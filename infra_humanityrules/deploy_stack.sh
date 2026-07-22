#!/bin/bash
set -e

# Deploy a single CDK stack
# Usage: ./deploy_stack.sh <stack-name>
# Example: ./deploy_stack.sh humr-prod-storage

if [ -z "$1" ]; then
    echo "Usage: ./deploy_stack.sh <stack-name>"
    echo "Example: ./deploy_stack.sh humr-prod-storage"
    exit 1
fi

STACK_NAME="$1"

# Load environment variables from .env (selective to avoid issues with multi-line values like GITHUB_APP_PRIVATE_KEY)
if [ -f "../.env" ]; then
    export HUMR_AWS_ACCESS_KEY=$(grep -E '^HUMR_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_AWS_SECRET_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_AWS_ACCOUNT_ID=$(grep -E '^HUMR_AWS_ACCOUNT_ID=' ../.env | cut -d'=' -f2-)
    export HUMR_API_SECRET_KEY=$(grep -E '^HUMR_API_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_API_ENDPOINT=$(grep -E '^HUMR_API_ENDPOINT=' ../.env | cut -d'=' -f2-)
    # Sandbox config — read by app.py at synth to gate the sandbox role + container wiring.
    export HUMR_SANDBOX_AWS_ACCOUNT_ID=$(grep -E '^HUMR_SANDBOX_AWS_ACCOUNT_ID=' ../.env | cut -d'=' -f2-)
    export HUMR_SANDBOX_EXTERNAL_ID=$(grep -E '^HUMR_SANDBOX_EXTERNAL_ID=' ../.env | cut -d'=' -f2-)
    export HUMR_SANDBOX_REGION=$(grep -E '^HUMR_SANDBOX_REGION=' ../.env | cut -d'=' -f2-)
    export HUMR_SANDBOX_HOSTED_ZONE=$(grep -E '^HUMR_SANDBOX_HOSTED_ZONE=' ../.env | cut -d'=' -f2-)
fi

# Map to AWS CLI expected names
export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

echo "Deploying ${STACK_NAME}..."
cdk deploy "${STACK_NAME}" --exclusively --require-approval never

echo ""
echo "Done! ${STACK_NAME} deployed successfully."
