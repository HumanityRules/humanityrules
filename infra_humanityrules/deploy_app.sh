#!/bin/bash
set -e

# DevOps Hero App-Only Deployment Script
#
# Fast deployment for code changes only (no infrastructure changes).
# Skips CDK stack deployment, builds Docker image, and triggers ECS update.
#
# Usage:
#   ./deploy_app.sh              # Deploy app only
#   ./deploy_app.sh --sync-secrets  # Sync secrets first, then deploy app

SYNC_SECRETS=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --sync-secrets)
            SYNC_SECRETS=true
            shift
            ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: ./deploy_app.sh [--sync-secrets]"
            exit 1
            ;;
    esac
done

# Load AWS credentials from .env
if [ -f "../.env" ]; then
    export HUMR_AWS_ACCESS_KEY=$(grep -E '^HUMR_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
    export HUMR_AWS_SECRET_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
fi

export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"

echo "========================================"
echo "DevOps Hero App-Only Deployment"
echo "========================================"

# Sync secrets if requested
if [ "$SYNC_SECRETS" = true ]; then
    echo ""
    echo "Syncing secrets to AWS Secrets Manager..."
    cd "$(dirname "$0")"
    uv run python sync_secrets.py
    cd - > /dev/null
    echo "Secrets synced."
    echo ""
fi

# Get ECR repository URI
ECR_URI=$(aws ecr describe-repositories --repository-names humr --query 'repositories[0].repositoryUri' --output text)
echo "ECR Repository: ${ECR_URI}"

# Login to ECR
echo "Logging in to ECR..."
aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | docker login --username AWS --password-stdin ${ECR_URI}

# Build and push (from project root)
echo ""
echo "Building Docker image..."
cd ..
docker build -f infra_humanityrules/Dockerfile -t humr:latest .

echo ""
echo "Pushing to ECR..."
docker tag humr:latest ${ECR_URI}:latest
docker push ${ECR_URI}:latest

cd infra_humanityrules

# Trigger ECS deployment
echo ""
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
