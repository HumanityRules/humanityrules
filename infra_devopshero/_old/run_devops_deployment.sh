#!/bin/bash
set -e

# Load .env file from project root
export $(grep -v '^#' ../.env | xargs)

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="$DOH_AWS_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$DOH_AWS_SECRET_KEY"
export AWS_DEFAULT_REGION="us-east-1"


#- S3 buckets ---------------------------------------
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

#- Install callback lambda --------------------------
echo "Uploading install callback lambda code before deploying the lambda stack..."
zip install_callback_lambda.zip install_callback_lambda.py
aws s3 cp install_callback_lambda.zip s3://devopshero-private/
rm install_callback_lambda.zip

echo "Deploying install callback lambda..."
# Delete lambda stack only if it's in an error state
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name devopshero-install-callback-lambda \
  --region us-east-1 \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null || echo "DOES_NOT_EXIST")

if [[ "$STACK_STATUS" == *"FAILED"* ]] || [[ "$STACK_STATUS" == *"ROLLBACK"* ]]; then
  echo "Stack is in error state ($STACK_STATUS), deleting..."
  aws cloudformation delete-stack \
    --stack-name devopshero-install-callback-lambda \
    --region us-east-1

  aws cloudformation wait stack-delete-complete \
    --stack-name devopshero-install-callback-lambda \
    --region us-east-1
else
  echo "Stack status: $STACK_STATUS (no deletion needed)"
fi

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