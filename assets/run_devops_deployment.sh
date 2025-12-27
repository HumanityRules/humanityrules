#!/bin/bash
set -e

# Load .env file from project root
export $(grep -v '^#' ../.env | xargs)

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="$DOH_AWS_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$DOH_AWS_SECRET_KEY"
export AWS_DEFAULT_REGION="us-east-1"

echo "Deploying public bucket..."
aws cloudformation deploy \
  --template-file cf_public_bucket.json \
  --stack-name devopshero-public-bucket \
  --parameter-overrides BucketName=devopshero-public \
  --region us-east-1

echo "Deploying private bucket..."
aws cloudformation deploy \
  --template-file cf_private_bucket.json \
  --stack-name devopshero-private-bucket \
  --parameter-overrides BucketName=devopshero-private \
  --region us-east-1

echo "Uploading S3 files..."
./upload_s3_files.sh


echo "Deploying install callback lambda..."

# Delete lambda stack if it exists, let's make sure we redeploy it every time
aws cloudformation delete-stack \
  --stack-name devopshero-install-callback-lambda \
  --region us-east-1 2>/dev/null || true

aws cloudformation wait stack-delete-complete \
  --stack-name devopshero-install-callback-lambda \
  --region us-east-1 2>/dev/null || true

aws cloudformation deploy \
  --template-file cf_install_callback_lambda.json \
  --stack-name devopshero-install-callback-lambda \
  --parameter-overrides \
    DohApiEndpoint="$DOH_API_ENDPOINT" \
    DohApiSecretKey="$DOH_API_SECRET_KEY" \
    LambdaCodeS3Bucket=devopshero-private \
    LambdaCodeS3Key=install_callback_lambda.zip \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1