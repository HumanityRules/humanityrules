#!/bin/bash
set -e

# DevOps Hero Production Infrastructure Deployment Script
# 
# Prerequisites:
# - CDK CLI installed (npm install -g aws-cdk)
# - Python dependencies installed (uv sync)
# - .env file with secrets (synced to AWS Secrets Manager by this script)

# Load environment variables from .env (selective to avoid issues with multi-line values like GITHUB_APP_PRIVATE_KEY)
if [ -f "../.env" ]; then
    export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_AWS_ACCOUNT_ID=$(grep -E '^DOH_AWS_ACCOUNT_ID=' ../.env | cut -d'=' -f2-)
    export DOH_API_SECRET_KEY=$(grep -E '^DOH_API_SECRET_KEY=' ../.env | cut -d'=' -f2-)
    export DOH_API_ENDPOINT=$(grep -E '^DOH_API_ENDPOINT=' ../.env | cut -d'=' -f2-)
fi

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_ACCOUNT_ID="${DOH_AWS_ACCOUNT_ID:-555553041615}"

echo "========================================"
echo "DevOps Hero Production Deployment"
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
echo "1/6: Deploying Certificate Stack (ACM)..."
echo "     This may take a few minutes for DNS validation."
cdk deploy doh-prod-cert --require-approval never

echo ""
echo "2/6: Deploying VPC and Storage Stacks..."
cdk deploy doh-prod-vpc doh-prod-storage --require-approval never

echo ""
echo "3/6: Deploying Lambda Stack..."
cdk deploy doh-prod-lambda --require-approval never

echo ""
echo "4/6: Deploying Cluster and Database Stacks..."
echo "     Aurora Serverless v2 may take 10-15 minutes."
cdk deploy doh-prod-cluster doh-prod-database --require-approval never

echo ""
echo "5/6: Deploying App Stack (ECR + ECS)..."
cdk deploy doh-prod-app --require-approval never

echo ""
echo "6/6: Deploying CDN Stack (CloudFront + Route53)..."
echo "     CloudFront distribution may take 10-15 minutes to deploy."
cdk deploy doh-prod-cdn --require-approval never

echo ""
echo "========================================"
echo "Building and Pushing Docker Image"
echo "========================================"

# Get ECR repository URI
ECR_URI=$(aws ecr describe-repositories --repository-names devopshero --query 'repositories[0].repositoryUri' --output text)

echo "ECR Repository: ${ECR_URI}"

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | docker login --username AWS --password-stdin ${ECR_URI}

# Build and push (from project root)
echo "Building Docker image..."
cd ..
docker build -f infra_devopshero/Dockerfile -t devopshero:latest .

echo "Tagging and pushing to ECR..."
docker tag devopshero:latest ${ECR_URI}:latest
docker push ${ECR_URI}:latest

cd infra_devopshero

echo ""
echo "========================================"
echo "Updating ECS Service"
echo "========================================"

# Force new deployment to pick up the new image
echo "Triggering ECS service update..."
aws ecs update-service \
    --cluster doh-prod-cluster \
    --service doh-prod-app \
    --force-new-deployment \
    --desired-count 1 > /dev/null

echo "Waiting for service to stabilize..."
aws ecs wait services-stable \
    --cluster doh-prod-cluster \
    --services doh-prod-app

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
