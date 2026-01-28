---
name: prod-manage
description: Run Django management commands on the production ECS container. Use when you need to manage customer accounts, environments, deployments, or run any manage.py command on prod.
---

# Production Management

Run Django management commands on the production ECS container via `infra_devopshero/prod_manage.sh`.

## Usage

```bash
cd infra_devopshero
./prod_manage.sh <command> [args...]
```

## Customer Operations (`doh_customer`)

### Infrastructure

```bash
# List organizations, AWS accounts, and environments
./prod_manage.sh doh_customer list

# Create environment (triggers provisioning)
./prod_manage.sh doh_customer create-env \
    --aws-account "DevOps Hero AWS Account" \
    --name default \
    --region us-east-1 \
    --hosted-zone devopshero.co \
    --provision

# Re-provision existing environment
./prod_manage.sh doh_customer provision-env \
    --slug default \
    --aws-account "DevOps Hero AWS Account"
```

### Apps and Deployments

```bash
# List all apps
./prod_manage.sh doh_customer list-apps

# List recent deployments (default: 10)
./prod_manage.sh doh_customer list-deployments
./prod_manage.sh doh_customer list-deployments --limit 20

# Show deployment logs - most recent deployment globally
./prod_manage.sh doh_customer deployment-logs

# Show deployment logs - specific app
./prod_manage.sh doh_customer deployment-logs --app simple-dashboard
./prod_manage.sh doh_customer deployment-logs --app simple-dashboard --limit 50

# Retry a failed deployment
./prod_manage.sh doh_customer retry-deployment --app simple-dashboard
```

### Quick Status Check

```bash
# Fast check: what's the latest deployment doing?
./prod_manage.sh doh_customer deployment-logs --limit 5
```

Output includes:
- Deployment ID, status (color-coded), created/updated timestamps
- Status message (if any)
- Logs in reverse chronological order (newest first) with timestamps

## Other Commands

Any Django management command works:
```bash
./prod_manage.sh shell              # Django shell
./prod_manage.sh dbshell            # Database shell
./prod_manage.sh showmigrations     # Check migrations
```

## How It Works

The script:
1. Loads AWS credentials from `../.env`
2. Finds the running ECS task for `doh-prod-app`
3. Runs `aws ecs execute-command` with the management command
