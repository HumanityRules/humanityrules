#!/bin/bash
set -e

# Humanity Rules Production Infrastructure Deployment Script
# 
# Prerequisites:
# - CDK CLI installed (npm install -g aws-cdk)
# - Python dependencies installed (uv sync)
# - .env file with secrets (synced to AWS Secrets Manager by this script)

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

# Map HUMR variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCOUNT_ID="${HUMR_AWS_ACCOUNT_ID}"

echo "========================================"
echo "Humanity Rules Production Deployment"
echo "========================================"
echo "AWS Account: ${AWS_ACCOUNT_ID}"
echo "AWS Region: ${AWS_DEFAULT_REGION}"
echo ""

# Check if CDK is bootstrapped
echo "Checking CDK bootstrap status..."
BOOTSTRAP_STATUS=$(aws cloudformation describe-stacks --stack-name CDKToolkit --region ${AWS_DEFAULT_REGION} 2>/dev/null || echo "NOT_FOUND")

if [[ "$BOOTSTRAP_STATUS" == "NOT_FOUND" ]]; then
    echo "CDK not bootstrapped. Running bootstrap..."
    cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_DEFAULT_REGION}
else
    echo "CDK already bootstrapped."
fi

echo ""
echo "========================================"
echo "Syncing Secrets to AWS"
echo "========================================"
echo "Syncing secrets from .env to AWS Secrets Manager..."
cd "$(dirname "$0")"
uv run python sync_secrets.py
cd - > /dev/null

# Deploy stacks in dependency order
echo ""
echo "========================================"
echo "Deploying CDK Stacks"
echo "========================================"

echo ""
echo "1/7: Deploying Certificate Stack (ACM)..."
echo "     This may take a few minutes for DNS validation."
cdk deploy humr-prod-cert --require-approval never

echo ""
echo "2/7: Deploying VPC and Storage Stacks..."
cdk deploy humr-prod-vpc humr-prod-storage --require-approval never

echo ""
echo "3/7: Deploying Lambda Stack..."
cdk deploy humr-prod-lambda --require-approval never

echo ""
echo "4/7: Deploying Cluster and Database Stacks..."
echo "     Aurora Serverless v2 may take 10-15 minutes."
cdk deploy humr-prod-cluster humr-prod-database --require-approval never

echo ""
echo "5/7: Deploying App Stack (ECR + ECS)..."
cdk deploy humr-prod-app --require-approval never

echo ""
echo "6/7: Deploying CDN Stack (CloudFront + Route53)..."
echo "     CloudFront distribution may take 10-15 minutes to deploy."
cdk deploy humr-prod-cdn --require-approval never

echo ""
# Only when the sandbox is configured (matches app.py's SANDBOX_CFG.enabled gate);
# otherwise the stack isn't synthesized and the deploy would fail.
if [ -n "${HUMR_SANDBOX_EXTERNAL_ID}" ] && [ -n "${HUMR_SANDBOX_AWS_ACCOUNT_ID}" ]; then
    echo "7/7: Deploying Sandbox Role Stack (shared-sandbox assume-role)..."
    cdk deploy humr-prod-sandbox-role --require-approval never
else
    echo "7/7: Skipping Sandbox Stack (HUMR_SANDBOX_* not set)."
fi

echo ""
echo "========================================"
echo "Building and Pushing Docker Image"
echo "========================================"

# Get ECR repository URI
ECR_URI=$(aws ecr describe-repositories --repository-names humr --query 'repositories[0].repositoryUri' --output text)

echo "ECR Repository: ${ECR_URI}"

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | docker login --username AWS --password-stdin ${ECR_URI}

# Build and push (from project root)
echo "Building Docker image..."
cd ..
docker build -f infra_humanityrules/Dockerfile -t humr:latest .

echo "Tagging and pushing to ECR..."
docker tag humr:latest ${ECR_URI}:latest
docker push ${ECR_URI}:latest

cd infra_humanityrules

echo ""
echo "========================================"
echo "Updating ECS Service"
echo "========================================"

# Force new deployment to pick up the new image
echo "Triggering ECS service update..."
aws ecs update-service \
    --cluster humr-prod-cluster \
    --service humr-prod-app \
    --force-new-deployment \
    --desired-count 1 > /dev/null

echo "Waiting for service to stabilize..."
aws ecs wait services-stable \
    --cluster humr-prod-cluster \
    --services humr-prod-app

echo ""
echo "========================================"
echo "Deployment Complete!"
echo "========================================"
echo ""
echo "Your app is now available at:"
echo "  https://humanityrules.io"
echo ""
echo "CloudFront distribution may take additional time to propagate globally."
echo ""
