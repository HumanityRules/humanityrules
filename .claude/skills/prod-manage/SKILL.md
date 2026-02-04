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

## Control Plane Operations (`doh_control`)

Operations that modify state for environments and deployments.

```bash
# Create environment (triggers provisioning)
./prod_manage.sh doh_control create-env \
    --aws-account "DevOps Hero AWS Account" \
    --name default \
    --region us-east-1 \
    --hosted-zone devopshero.co \
    --provision

# Re-provision existing environment
./prod_manage.sh doh_control provision-env \
    --slug default \
    --aws-account "DevOps Hero AWS Account"

# Tear down environment
./prod_manage.sh doh_control teardown-env \
    --slug default \
    --aws-account "DevOps Hero AWS Account"

# Retry a failed deployment
./prod_manage.sh doh_control retry-deployment --app simple-dashboard
```

## Ad-hoc Model Queries (`doh_query`)

Query any model without shell quoting issues. **Use this instead of `shell -c`** for inspecting data.

### Infrastructure

```bash
./prod_manage.sh doh_query Organization slug name
./prod_manage.sh doh_query AWSAccount name aws_account_id status
./prod_manage.sh doh_query Environment name slug aws_region status
```

### Apps and Deployments

```bash
./prod_manage.sh doh_query App name slug app_type build_strategy
./prod_manage.sh doh_query Deployment id status created_at --order created_at --desc --limit 10
./prod_manage.sh doh_query Deployment status --filter status=failed --limit 10
./prod_manage.sh doh_query DeploymentLog level source message --filter deployment_id=<uuid> --order created_at --desc
```

### Repositories

```bash
./prod_manage.sh doh_query Repository full_name default_branch
./prod_manage.sh doh_query Repository full_name --filter full_name__icontains=dashboard
```

### Utilities

```bash
# Discover available fields on a model
./prod_manage.sh doh_query Deployment --describe

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

Common models: `Organization`, `AWSAccount`, `Environment`, `App`, `Deployment`, `DeploymentLog`, `Repository`, `Workspace`, `Conversation`, `Message`

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
