# DevOps Hero Control Plane Infrastructure

This directory contains the CDK infrastructure for the DevOps Hero control plane — the app that runs at humanityrules.io. This is distinct from `devopshero_app/services/infra_customer/`, which deploys customer apps to their AWS accounts.


## AWS Credentials Setup

**CRITICAL**: Before running any CDK or AWS CLI commands, load the DOH AWS credentials from `.env`:

```bash
# From infra_devopshero/ directory
source ../scripts/load_aws_env.sh   # If this script exists

# Or manually export (deploy.sh does this automatically):
export DOH_AWS_ACCESS_KEY=$(grep -E '^DOH_AWS_ACCESS_KEY=' ../.env | cut -d'=' -f2-)
export DOH_AWS_SECRET_KEY=$(grep -E '^DOH_AWS_SECRET_KEY=' ../.env | cut -d'=' -f2-)
export DOH_AWS_ACCOUNT_ID=$(grep -E '^DOH_AWS_ACCOUNT_ID=' ../.env | cut -d'=' -f2-)

# Map to AWS CLI expected names
export AWS_ACCESS_KEY_ID="${DOH_AWS_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${DOH_AWS_SECRET_KEY}"
export AWS_DEFAULT_REGION="us-east-1"
```

The `.env` file has `DOH_AWS_*` prefixed variables (not `AWS_*`) to avoid conflicts with other AWS credentials the user might have. The `deploy.sh` script handles this mapping automatically.


## Stack Architecture

Eight CDK stacks deployed in dependency order:

1. **cert_stack** — ACM wildcard certificate for `humanityrules.io` and `*.humanityrules.io` (must be us-east-1 for CloudFront)
2. **vpc_stack** — VPC with public/private subnets across 2 AZs, single NAT gateway
3. **storage_stack** — S3 buckets (public for CF templates, private for internal assets), ECR repository
4. **lambda_stack** — Install callback Lambda (handles customer AWS account connection)
5. **cluster_stack** — ECS Fargate cluster, ALB with HTTPS listener, task execution role
6. **database_stack** — Aurora Serverless v2 PostgreSQL
7. **app_stack** — ECS service with migration init container, task definition, secrets
8. **cdn_stack** — CloudFront distribution, Route53 A records

All stacks use `doh-prod-` prefix for resource names.


## Common Operations

### Full Deployment (First Time or Full Rebuild)

```bash
cd infra_devopshero
./deploy.sh
```

This script:
1. Loads DOH_AWS credentials from `.env`
2. Bootstraps CDK if needed
3. Syncs secrets to AWS Secrets Manager via `sync_secrets.py`
4. Deploys all CDK stacks in dependency order
5. Builds and pushes Docker image to ECR
6. Triggers ECS service update

### Deploy Code Changes Only (Most Common)

```bash
cd infra_devopshero
./deploy_app.sh                 # App only (~2-3 min)
./deploy_app.sh --sync-secrets  # Sync secrets first, then deploy
```

This skips CDK stack deployment and only builds/pushes the Docker image and triggers ECS update.

### Update Secrets

Secrets are synced from `.env` to AWS Secrets Manager. The mapping is defined in `sync_secrets.py`:

- `devopshero/prod/django` — DJANGO_SECRET_KEY, DJANGO_SUPERUSER_EMAIL
- `devopshero/prod/workos` — WORKOS_CLIENT_ID, WORKOS_API_KEY
- `devopshero/prod/github` — GitHub App credentials
- `devopshero/prod/bedrock` — Bedrock credentials for AI features
- `devopshero/prod/api` — DOH_API_SECRET_KEY
- `devopshero/prod/telegram` — TELEGRAM_MANAGER_BOT_TOKEN, TELEGRAM_MANAGER_BOT_USERNAME

**To update secrets:**

```bash
cd infra_devopshero
uv run python sync_secrets.py          # Sync all secrets
uv run python sync_secrets.py --dry-run # Preview changes
```

**Important**: `sync_secrets.py` overwrites secrets entirely based on `SECRET_DEFINITIONS`. If you manually add fields to a secret in AWS console, they'll be wiped on next sync. Always add new secret fields to both `.env` and `SECRET_DEFINITIONS`.

### Deploy Single Stack

```bash
cd infra_devopshero
./deploy_stack.sh doh-prod-storage
```

This script loads AWS credentials from `.env` and deploys the specified stack.

### View CDK Diff

```bash
cdk diff doh-prod-app
```

### Debugging Production

**Log group:** `/devopshero/prod/ecs`

**Stream prefixes:**
- `devopshero/devopshero/` — App container logs
- `migrate/migrate/` — Migration init container logs

**List recent log streams (find latest task):**

```bash
aws logs describe-log-streams \
  --log-group-name "/devopshero/prod/ecs" \
  --order-by LastEventTime \
  --descending \
  --limit 5 \
  --query 'logStreams[*].[logStreamName,lastEventTimestamp]' \
  --output text
```

**Get logs from a specific stream:**

```bash
# Replace STREAM_NAME with actual stream from above
aws logs get-log-events \
  --log-group-name "/devopshero/prod/ecs" \
  --log-stream-name "devopshero/devopshero/TASK_ID" \
  --limit 100 \
  --query 'events[*].message' \
  --output text
```

**Filter for errors or specific patterns:**

```bash
aws logs get-log-events \
  --log-group-name "/devopshero/prod/ecs" \
  --log-stream-name "devopshero/devopshero/TASK_ID" \
  --limit 100 \
  --query 'events[*].message' \
  --output text | grep -iE 'error|exception|worker'
```

**Tail logs in real-time:**

```bash
# Everything (app + migration + any sidecar)
aws logs tail /devopshero/prod/ecs --follow

# App container only (stream prefix scoping)
aws logs tail /devopshero/prod/ecs --follow --log-stream-name-prefix devopshero

# Migration container only
aws logs tail /devopshero/prod/ecs --follow --log-stream-name-prefix migrate

# Filter on message content (NOT stream name)
aws logs tail /devopshero/prod/ecs --follow --filter-pattern '?ERROR ?Exception ?Traceback'
```

`--filter-pattern` matches log message body, not stream names. To scope by
container, use `--log-stream-name-prefix`. The streams are
`devopshero/devopshero/<task>` and `migrate/migrate/<task>`.

There's also a wrapper that handles credential loading and defaults to
`--follow`: `./tail_prod.sh [aws-logs-tail-flags...]` (pass `--no-follow` for a
one-shot dump).

### ECS Exec (SSH Alternative)

Requires the AWS Session Manager Plugin (already installed and in PATH).

```bash
# Find running task
TASK_ARN=$(aws ecs list-tasks --cluster doh-prod-cluster --service-name doh-prod-app --query 'taskArns[0]' --output text)

# Execute command
aws ecs execute-command --cluster doh-prod-cluster --task ${TASK_ARN} --container devopshero --interactive --command "/bin/bash"
```


## Customer AWS Account Connection Flow

When customers connect their AWS account to DevOps Hero:

1. **Customer clicks "Connect AWS Account"** in the UI
2. **Quick Create Stack link** opens AWS Console with `cf_install_template.json` from S3
3. **CloudFormation creates**:
   - IAM role with trust policy for DOH account (555553041615)
   - ExternalId for confused deputy protection
4. **Custom Resource triggers Lambda** (`doh-prod-install-callback`)
5. **Lambda POSTs to DOH backend** (`/api/aws/install-account-callback`)
6. **DOH backend stores** the role ARN and external ID

**Key files:**
- `cf_install_template.json` — CloudFormation template customers run (uploaded to `devopshero-public` S3 bucket via `BucketDeployment`)
- `install_callback_lambda.py` — Lambda handler code (deployed inline via CDK)


## ECS Deployment Flow

On every deployment:

1. ECS starts new task
2. **Migration container** runs `migrate --noinput && ensure_superuser`
3. Migration exits (code 0)
4. **App container** starts (was blocked on migration SUCCESS dependency)
5. Health check passes at `/health/`
6. ALB routes traffic to new task

The migration container has `essential=False` so the task continues after it exits. The app container has a `ContainerDependency` with `condition=SUCCESS`.


## Architecture Decisions

**Why CloudFront in front of ALB?**
- SSL termination at CloudFront edge locations
- Future: static asset caching, WAF integration
- Consistent HTTPS everywhere without ALB cert management per region



## Deprecated Files

The `_old/` directory contains files kept for historical reference but should not be used.
