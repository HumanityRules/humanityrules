---
name: customer-debug
description: Debug customer app deployments in customer AWS accounts. Use when an app is crashing, a deployment failed, or you need to check ECS tasks, logs, or CloudFormation in a customer account (e.g., "CH Sandbox"). NOT for HUMR's own infrastructure — use prod-controlplane-debug for that.
---

# Debug Customer App Deployments

For investigating issues with apps deployed into customer AWS accounts — ECS task crashes, deployment failures, container errors, CloudFormation stack issues.

**Localhost by default.** Unless the user explicitly says "in production," assume the HUMR control plane is running locally and the customer exists in the local DB:

- **Localhost:** `uv run manage.py <command> ...` — queries the local DB, uses credentials from `.env`
- **Production:** `cd infra_humanityrules && ./prod_manage.sh <command> ...` — runs the same command on the production ECS container, queries the production DB

All commands below work identically in both modes — just swap the prefix.

Customer accounts are accessed via IAM role assumption. The `.env` file in the project root has `HUMR_AWS_ACCESS_KEY` and `HUMR_AWS_SECRET_KEY` — these are HUMR's control plane IAM credentials, loaded into `django.conf.settings`. They're used to STS-assume `arn:aws:iam::{account_id}:role/humr-{external_id}` in the customer account. All management commands handle this internally via `iam_utils.get_assumed_role_session()`. The `account_id` and `external_id` come from the `AWSAccount` model in the DB.


## Step 1: Gather Context from the DB

Always start by querying the DB with `humr_query` (see the `manage-commands` skill) to understand what you're dealing with. Key models: `AWSAccount` (get `aws_account_id` and `external_id`), `Environment` (get `slug` and `aws_region`), `App`, `Deployment`, `DeploymentLog`.


## Step 2: Access the Customer's AWS Account

Check the `manage-commands` skill first — commands like `humr_app_shell`, `humr_app_exec`, `humr_app_logs`, and `humr_efs_browse` handle the assume-role dance internally.

For non-interactive probes, use `humr_app_exec` (stdin script, structured output, `--as <user>`):

```bash
uv run manage.py humr_app_exec --account "CH Sandbox" --env default --app my-app --as hermeswebui --format json <<'EOF'
/app/venv/bin/python -c "..."
EOF
```

For ad-hoc AWS CLI calls not covered by existing commands, assume the customer role and export the temporary credentials. Use `account_id` and `external_id` from Step 1:

```bash
# Load HUMR control plane credentials
export AWS_ACCESS_KEY_ID=$(grep -E '^HUMR_AWS_ACCESS_KEY=' .env | cut -d'=' -f2-)
export AWS_SECRET_ACCESS_KEY=$(grep -E '^HUMR_AWS_SECRET_KEY=' .env | cut -d'=' -f2-)
export AWS_DEFAULT_REGION="us-east-1"

# Assume the customer role (substitute account_id and external_id from humr_query)
CREDS=$(aws sts assume-role \
  --role-arn "arn:aws:iam::<account_id>:role/humr-<external_id>" \
  --role-session-name "debug-session" \
  --external-id "<external_id>" \
  --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
  --output text)

export AWS_ACCESS_KEY_ID=$(echo $CREDS | awk '{print $1}')
export AWS_SECRET_ACCESS_KEY=$(echo $CREDS | awk '{print $2}')
export AWS_SESSION_TOKEN=$(echo $CREDS | awk '{print $3}')

# Now use AWS CLI normally — you're in the customer account
aws ecs list-tasks --cluster humr-default-cluster
aws logs describe-log-streams --log-group-name /humr/default/ecs --order-by LastEventTime --descending --limit 5
```


## Customer Resource Naming Conventions

All names are deterministic from `env_slug` and `app_slug`:

- **Cluster:** `humr-{env_slug}-cluster`
- **ECS service:** `humr-{env_slug}-{app_slug}`
- **Container name:** `{app_slug}`
- **Log group:** `/humr/{env_slug}/ecs`
- **Log stream:** `{app_slug}/{app_slug}/{ecs_task_id}`
- **Task role:** `humr-{env_slug}-{app_slug}-task-role`
- **CDK stack:** `humr-{env_slug}-{app_slug}-app-cdk`
- **Resource prefix:** `humr-{env_slug}-{app_slug}` (used for most resource names)
