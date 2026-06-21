---
name: prod-controlplane-debug
description: Debug DOH's own production control plane (humanityrules.io). Use when the DOH platform itself is broken — its ECS service, ALB, CloudFormation stacks, or CloudWatch logs. NOT for customer app issues — use the customer-debug skill for that.
---

# Debug DOH Control Plane Production Infrastructure

For investigating production issues with DOH's own control plane (humanityrules.io) — the platform itself, not customer apps.

**This skill is for DOH's own AWS account and infrastructure.** If a customer app is crashing or failing to deploy, that happens in the customer's AWS account — see the `customer-debug` skill instead.

## AWS Credentials

Load from `.env` before running AWS CLI commands:

```bash
export HUMR_AWS_ACCESS_KEY=$(grep -E '^HUMR_AWS_ACCESS_KEY=' .env | cut -d'=' -f2-)
export HUMR_AWS_SECRET_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' .env | cut -d'=' -f2-)
export AWS_ACCESS_KEY_ID="${HUMR_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${HUMR_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
```

## ECS Service Status

```bash
# Check service status
aws ecs describe-services \
  --cluster humr-prod-cluster \
  --services humr-prod-app \
  --query 'services[0].{status:status,running:runningCount,desired:desiredCount,pending:pendingCount}'

# List recent tasks (running and stopped)
aws ecs list-tasks --cluster humr-prod-cluster --service-name humr-prod-app

# Describe a task (get stop reason if failed)
aws ecs describe-tasks \
  --cluster humr-prod-cluster \
  --tasks <task-arn> \
  --query 'tasks[0].{status:lastStatus,stopCode:stopCode,stopReason:stoppedReason}'
```

## CloudWatch Logs

**Log group:** `/humr/prod/ecs`

**Stream prefixes:**
- `humr/humr/` — App container logs
- `migrate/migrate/` — Migration init container logs

```bash
# List recent log streams (find latest task)
aws logs describe-log-streams \
  --log-group-name "/humr/prod/ecs" \
  --order-by LastEventTime \
  --descending \
  --limit 5 \
  --query 'logStreams[*].[logStreamName,lastEventTimestamp]' \
  --output text

# Get logs from a specific stream
aws logs get-log-events \
  --log-group-name "/humr/prod/ecs" \
  --log-stream-name "humr/humr/<TASK_ID>" \
  --limit 100 \
  --query 'events[*].message' \
  --output text

# Filter for errors
aws logs get-log-events \
  --log-group-name "/humr/prod/ecs" \
  --log-stream-name "humr/humr/<TASK_ID>" \
  --limit 100 \
  --query 'events[*].message' \
  --output text | grep -iE 'error|exception|traceback'

# Tail logs in real-time
aws logs tail /humr/prod/ecs --follow --filter-pattern humr
```

## ECS Exec (Shell Access)

```bash
# Find running task
TASK_ARN=$(aws ecs list-tasks --cluster humr-prod-cluster --service-name humr-prod-app --query 'taskArns[0]' --output text)

# Get a shell
aws ecs execute-command \
  --cluster humr-prod-cluster \
  --task ${TASK_ARN} \
  --container humr \
  --interactive \
  --command "/bin/bash"
```

## ALB Health Checks

```bash
# Get target group ARN
TG_ARN=$(aws elbv2 describe-target-groups \
  --names humr-prod-app-tg \
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
  --query 'StackSummaries[?starts_with(StackName,`humr-prod`)].StackName'

# Check stack status
aws cloudformation describe-stacks \
  --stack-name humr-prod-app \
  --query 'Stacks[0].{status:StackStatus,reason:StackStatusReason}'

# View recent stack events (for debugging failed deployments)
aws cloudformation describe-stack-events \
  --stack-name humr-prod-app \
  --query 'StackEvents[0:10].{time:Timestamp,status:ResourceStatus,resource:LogicalResourceId,reason:ResourceStatusReason}' \
  --output table
```

## Resource Names

Run this script to extract current resource names from CDK source:

```bash
python infra_humanityrules/extract_resources.py
```

Key resources:
- **Cluster:** `humr-prod-cluster`
- **Service:** `humr-prod-app`
- **Log group:** `/humr/prod/ecs`
- **Target group:** `humr-prod-app-tg`
