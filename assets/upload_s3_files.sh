#!/bin/bash
set -e

# Load .env file from project root
export $(grep -v '^#' ../.env | xargs)

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="$DOH_AWS_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$DOH_AWS_SECRET_KEY"
export AWS_DEFAULT_REGION="us-east-1"


# Install template
aws s3 cp cf_install_template.json s3://devopshero-public/
echo "✓ Install template uploaded successfully"
