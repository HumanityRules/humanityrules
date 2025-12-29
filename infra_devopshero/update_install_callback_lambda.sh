#!/bin/bash
set -e

# Load .env file from project root
export $(grep -v '^#' ../.env | xargs)

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="$DOH_AWS_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$DOH_AWS_SECRET_KEY"
export AWS_DEFAULT_REGION="us-east-1"

# Callback lambda code
zip install_callback_lambda.zip install_callback_lambda.py
aws s3 cp install_callback_lambda.zip s3://devopshero-private/
rm install_callback_lambda.zip

# Update Lambda function with new code
aws lambda update-function-code \
  --function-name devopshero-install-callback \
  --s3-bucket devopshero-private \
  --s3-key install_callback_lambda.zip \
  --region us-east-1 > /dev/null
echo "✓ Callback lambda code uploaded and deployed"