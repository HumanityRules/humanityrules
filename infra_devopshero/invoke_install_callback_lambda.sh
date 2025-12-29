#!/bin/bash
set -e

# Load .env file from project root
export $(grep -v '^#' ../.env | xargs)

# Map DOH variables to AWS CLI expected names
export AWS_ACCESS_KEY_ID="$DOH_AWS_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$DOH_AWS_SECRET_KEY"
export AWS_DEFAULT_REGION="us-east-1"

aws lambda invoke \
  --function-name devopshero-install-callback \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "RequestType": "Create",
    "ResponseURL": "https://httpbin.org/put",
    "StackId": "test-stack",
    "RequestId": "test-request",
    "LogicalResourceId": "TestResource",
    "ResourceProperties": {
      "AwsAccount": "123456789012",
      "RoleArn": "arn:aws:iam::123456789012:role/devopshero-test",
      "ExternalId": "test-external-id",
      "StackRegion": "us-east-1"
    }
  }' \
  /dev/stdout
