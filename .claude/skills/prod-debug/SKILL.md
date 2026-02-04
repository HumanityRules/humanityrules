---
name: prod-debug
description: Debug DevOps Hero production infrastructure issues. Use when there's a problem with the control plane, ECS tasks are failing, deployment issues, or when examining CloudFormation stacks, ECS logs, or ALB health checks.
---

# Debug Production Infrastructure

For investigating production issues with the DevOps Hero control plane (devopshero.ai).

## AWS Credentials

Load from `.env` before running AWS CLI commands:

```bash
export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' .env | cut -d'=' -f2-)
export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' .env | cut -d'=' -f2-)
export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
```

## ECS Service Status

```bash
# Check service status
aws ecs describe-services \
  --cluster doh-prod-cluster \
  --services doh-prod-app \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount,pending:pendingCount}'

# List recent tasks (running and stopped)
aws ecs list-tasks --cluster doh-prod-cluster --service-name doh-prod-app

# Describe a task (get stop reason if failed)
aws ecs describe-tasks \
  --cluster doh-prod-cluster \
  --tasks <task-arn> \
  --query 'tasks[0].{status:lastStatus,stopCode:stopCode,stopReason:stoppedReason}'
```

## CloudWatch Logs

**Log group:** `/devopshero/prod/ecs`

**Stream prefixes:**
- `devopshero/devopshero/` — App container logs
- `migrate/migrate/` — Migration init container logs

```bash
# List recent log streams (find latest task)
aws logs describe-log-streams \
  --log-group-name "/devopshero/prod/ecs" \
  --order-by LastEventTime \
  --descending \
  --limit 5 \
  --query 'logStreams[*].[logStreamName,lastEventTimestamp]' \
  --output text

# Get logs from a specific stream
aws logs get-log-events \
  --log-group-name "/devopshero/prod/ecs" \
  --log-stream-name "devopshero/devopshero/<TASK_ID>" \
  --limit 100 \
  --query 'events[*].message' \
  --output text

# Filter for errors
aws logs get-log-events \
  --log-group-name "/devopshero/prod/ecs" \
  --log-stream-name "devopshero/devopshero/<TASK_ID>" \
  --limit 100 \
  --query 'events[*].message' \
  --output text | grep -iE 'error|exception|traceback'

# Tail logs in real-time
aws logs tail /devopshero/prod/ecs --follow --filter-pattern devopshero
```

## ECS Exec (Shell Access)

```bash
# Find running task
TASK_ARN=$(aws ecs list-tasks --cluster doh-prod-cluster --service-name doh-prod-app --query 'taskArns[0]' --output text)

# Get a shell
aws ecs execute-command \
  --cluster doh-prod-cluster \
  --task ${TASK_ARN} \
  --container devopshero \
  --interactive \
  --command "/bin/bash"
```

## ALB Health Checks

```bash
# Get target group ARN
TG_ARN=$(aws elbv2 describe-target-groups \
  --names doh-prod-tg \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text)

# Check target health
aws elbv2 describe-target-health \
  --target-group-arn ${TG_ARN} \
  --query 'TargetHealthDescriptions[*].{target:Target.Id,health:TargetHealth.State,reason:TargetHealth.Reason}'
```

## CloudFormation Stacks

```bash
# List DOH stacks
aws cloudformation list-stacks \
  --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --query 'StackSummaries[?starts_with(StackName,`doh-prod`)].StackName'

# Check stack status
aws cloudformation describe-stacks \
  --stack-name doh-prod-app \
  --query 'Stacks[0].{status:StackStatus,reason:StackStatusReason}'

# View recent stack events (for debugging failed deployments)
aws cloudformation describe-stack-events \
  --stack-name doh-prod-app \
  --query 'StackEvents[0:10].{time:Timestamp,status:ResourceStatus,resource:LogicalResourceId,reason:ResourceStatusReason}' \
  --output table
```

## Resource Names

Run this script to extract current resource names from CDK source:

```bash
python infra_devopshero/extract_resources.py
```

Key resources:
- **Cluster:** `doh-prod-cluster`
- **Service:** `doh-prod-app`
- **Log group:** `/devopshero/prod/ecs`
- **Target group:** `doh-prod-tg`
