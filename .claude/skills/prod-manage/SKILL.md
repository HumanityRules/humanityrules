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

## Ad-hoc Model Queries (`doh_query`)

Query any model without shell quoting issues. **Use this instead of `shell -c`** for inspecting data.

```bash
# Discover available fields on a model
./prod_manage.sh doh_query Message --describe

# Basic usage: list all records with default fields
./prod_manage.sh doh_query Repository

# Specify fields to display
./prod_manage.sh doh_query Repository full_name default_branch clone_url

# Filter results (Django ORM syntax)
./prod_manage.sh doh_query Repository full_name --filter full_name__icontains=dashboard

# Multiple filters
./prod_manage.sh doh_query Deployment status created_at --filter status=failed --filter app__slug=my-app

# Order results (use --desc for descending)
./prod_manage.sh doh_query Deployment app status --limit 10 --order created_at --desc

# List available models (intentionally use wrong name)
./prod_manage.sh doh_query WrongModel
```

### Conversation Messages

```bash
# Get messages from a conversation (chronological)
./prod_manage.sh doh_query Message role content --filter conversation_id=<uuid> --order created_at --limit 100

# Get recent messages (newest first)
./prod_manage.sh doh_query Message role content --filter conversation_id=<uuid> --order created_at --desc --limit 20
```

Common models: `Repository`, `App`, `Deployment`, `DeploymentLog`, `Environment`, `AWSAccount`, `Organization`, `Workspace`, `Conversation`, `Message`

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

## Shell Quoting Limitations

**Avoid `shell -c` with complex Python code.** Commands pass through multiple shell layers (local → AWS CLI → ECS → bash → Python), causing quote mangling.

**Bad** (quotes get mangled):
```bash
./prod_manage.sh shell -c "from devopshero_app.models import Repository; print(Repository.objects.get(id='abc'))"
```

**Good** (use `doh_query` instead):
```bash
./prod_manage.sh doh_query Repository full_name default_branch --filter id=abc
```

If you must use `shell -c`, avoid string literals with quotes. This works:
```bash
./prod_manage.sh shell -c "from devopshero_app.models import Repository; [print(r.full_name) for r in Repository.objects.all()]"
```
