---
name: prod-manage
description: Run Django management commands on the production ECS container. Use when you need to manage customer accounts, environments, deployments, or run any manage.py command on prod.
---

# Production Management

Run Django management commands against production via `infra_humanityrules/prod_manage.sh`. Most commands ECS-exec into the prod app container (DB ops, control-plane). A few **local-execution** commands run on the operator's machine but resolve their target from prod's DB — the script dispatches transparently.

For a list of all available commands and what they do, see the `manage-commands` skill. This skill focuses on prod-specific execution details and examples.

## Usage

```bash
cd infra_humanityrules
./prod_manage.sh <command> [args...]
```

## Two execution modes (transparent)

`prod_manage.sh` picks one based on the command:

- **ECS-exec mode** (default, most commands): the command runs inside the prod app container via `aws ecs execute-command`. Uses prod's DB and prod's identity. Examples: `humr_query`, `humr_control teardown-env`, `seed_app_templates`, `humr_secrets`.
- **Local-exec mode** (commands listed in `LOCAL_EXEC_COMMANDS` inside `prod_manage.sh`): the command runs on the operator's laptop (needs Docker daemon, interactive stdin, or a large local source tree), but the AWS target metadata is fetched from prod's DB first. Implemented by `prod_dispatch_local.py`. Currently in this list: `humr_build_prebuilt_image`. Add more as their use cases arise.

The operator never picks the mode — the same `--account NAME --env SLUG` UX works for both. Behind the scenes for local-exec the dispatcher converts those into raw-mode args (`--aws-account-id / --aws-external-id / --aws-region / --env-slug`) on the Django command, which all `add_aws_target_args`-using commands accept.

## Local-exec example: build & push a HUMR-owned image into a customer env

The image is pushed to `humr/{env_slug}/{ecr_repo}:{version}` in the customer's per-env ECR. The four target values are resolved from prod's DB.

```bash
./prod_manage.sh humr_build_prebuilt_image \
    --account "Humanity Rules Sandbox" \
    --env production \
    --source-dir template_repos/humr_dind \
    --ecr-repo humr-dind \
    --tag 0.2.5
```

Output begins with `[prod_dispatch] resolving --account=… --env=… via prod_manage.sh humr_query …` then `[prod_dispatch] exec (cwd=…): uv run manage.py … --aws-account-id … --aws-external-id … --aws-region … --env-slug …`. After that the regular Django command output appears.

If multiple AWSAccount rows match (same 12-digit account onboarded into multiple HUMR organizations), the dispatcher errors with a list and asks for `--org`.

## Control Plane Operations (`humr_control`)

Operations that modify state for environments and deployments.

```bash
# Create environment (triggers provisioning)
./prod_manage.sh humr_control create-env \
    --aws-account "Humanity Rules AWS Account" \
    --name default \
    --region us-east-1 \
    --hosted-zone humanityrules.io

# Tear down environment
./prod_manage.sh humr_control teardown-env \
    --slug default \
    --aws-account "Humanity Rules AWS Account"

# Re-queue an environment for provisioning
./prod_manage.sh humr_control redeploy-env \
    --slug default \
    --aws-account "Humanity Rules AWS Account"

# Tear down an app's deployment
./prod_manage.sh humr_control teardown-app --app ai-detector-and-humanizer

# Redeploy an app (clones the latest concluded deployment into a new PENDING row,
# rebuilding the image; mirrors the UI's "Redeploy" button)
./prod_manage.sh humr_control redeploy-app --app simple-dashboard

# Pick a specific environment when the app has been deployed to more than one
./prod_manage.sh humr_control redeploy-app --app simple-dashboard --env default

# Or redeploy from an exact source deployment
./prod_manage.sh humr_control redeploy-app --app simple-dashboard --deployment <uuid>
```

## Ad-hoc Model Queries (`humr_query`)

For one-off inspection, prefer `humr_query` over `shell -c` — you get filtering, ordering, JSON output, and a tidy table view without writing Python.

### Infrastructure

```bash
./prod_manage.sh humr_query Organization slug name
./prod_manage.sh humr_query AWSAccount name aws_account_id status
./prod_manage.sh humr_query Environment name slug aws_region status
```

### Apps and Deployments

```bash
./prod_manage.sh humr_query App name slug app_type build_strategy
./prod_manage.sh humr_query Deployment id status created_at --order created_at --desc --limit 10
./prod_manage.sh humr_query Deployment status --filter status=failed --limit 10
./prod_manage.sh humr_query DeploymentLog level source message --filter deployment_id=<uuid> --order created_at --desc
```

### Repositories

```bash
./prod_manage.sh humr_query Repository full_name default_branch
./prod_manage.sh humr_query Repository full_name --filter full_name__icontains=dashboard
```

### Utilities

```bash
# Discover available fields on a model
./prod_manage.sh humr_query Deployment --describe

# List available models (intentionally use wrong name)
./prod_manage.sh humr_query WrongModel
```

### Conversation Messages

```bash
# Get messages from a conversation (chronological)
./prod_manage.sh humr_query Message role content --filter conversation_id=<uuid> --order created_at --limit 100

# Get recent messages (newest first)
./prod_manage.sh humr_query Message role content --filter conversation_id=<uuid> --order created_at --desc --limit 20
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

For ECS-exec mode (default), the script:
1. Loads AWS credentials from `../.env`
2. Finds the running ECS task for `humr-prod-app`
3. Runs `aws ecs execute-command` with the management command

For local-exec mode (commands in `LOCAL_EXEC_COMMANDS`), the script:
1. Hands off to `prod_dispatch_local.py`
2. Dispatcher parses `--account/--env/--org` from the args
3. Dispatcher calls back into `prod_manage.sh humr_query AWSAccount … --format json` and `… Environment … --format json` to fetch `aws_account_id`, `external_id`, `aws_region`, `slug`
4. Dispatcher `exec`s `uv run manage.py <cmd>` LOCALLY with those four values injected as raw-mode args, replacing the original `--account/--env/--org`

## Shell Quoting

`prod_manage.sh` base64-encodes the full command before handing it to `aws ecs execute-command`, so quotes, spaces, `$`, backticks, backslashes, and newlines all pass through untouched. Anything you can type into a normal Django `manage.py shell -c "..."` works here too.

Examples that all work:

```bash
./prod_manage.sh shell -c "from humanityrules_app.models import Repository; print(Repository.objects.get(id='abc'))"

./prod_manage.sh shell -c "import json; print(json.dumps({'a': 1, 'b': [\"two\", 'three']}))"

./prod_manage.sh shell -c "from humanityrules_app.models import User
for u in User.objects.filter(email__icontains='example.com'):
    print(u.email)"
```

`humr_query` is still the more ergonomic choice for plain reads (no Python, filters via `--filter key=value`, formatted output). `shell -c` is the right tool when you need real Python — ad-hoc joins, custom logic, bulk updates, or destructive operations like `.delete()`.
