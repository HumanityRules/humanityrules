---
name: debug-production
description: Debug DevOps Hero production infrastructure issues. Use when there's a problem with the control plane, ECS tasks are failing, deployment issues, or when examining CloudFormation stacks, ECS logs, or ALB health checks.
---

# Debug Production Infrastructure

For investigating production issues with the DevOps Hero control plane (devopshero.ai).

## Get Resource Names

Run this script to extract current resource names from CDK source:

```bash
python infra_devopshero/extract_resources.py
```

## AWS Credentials

Load from `.env` before running AWS CLI commands:

```bash
export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' .env | cut -d'=' -f2-)
export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' .env | cut -d'=' -f2-)
export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
```
