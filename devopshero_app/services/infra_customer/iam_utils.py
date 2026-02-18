import logging

import boto3
from django.conf import settings

logger = logging.getLogger(__name__)


def get_assumed_role_session(
    access_key: str | None,
    secret_key: str | None,
    account_id: str,
    external_id: str,
    region: str,
) -> boto3.Session:
    """
    Assume the DevOpsHero role in the target account and return a boto3 session.

    If access_key/secret_key are provided, uses explicit credentials (local dev).
    If None, uses default credential chain (ECS task role in production).

    Raises:
        ClientError: If role assumption fails
    """
    # Create STS client - explicit credentials for local dev, task role for production
    if access_key and secret_key:
        sts_client = boto3.client(
            "sts",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
    else:
        sts_client = boto3.client("sts", region_name=region)

    # The role ARN follows the pattern from cf_install_template.json
    role_arn = f"arn:aws:iam::{account_id}:role/devopshero-{external_id}"

    print(f"🔑 Assuming role: {role_arn}")

    response = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName="devopshero-deployment",
        ExternalId=external_id,
        DurationSeconds=3600,  # 1 hour
    )

    credentials = response["Credentials"]

    # Create a new session with the assumed role credentials
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region,
    )

    # Verify we're in the right account
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    print(f"✅ Assumed role in account: {identity['Account']}")

    return session


def _get_aws_session_for_environment(environment):
    """Get an AWS session with assumed role credentials for the environment's account."""
    aws_account = environment.aws_account
    return get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def read_task_role_statements(environment, app) -> list[dict]:
    """Read inline IAM policies from the app's ECS task role in the customer's AWS account.

    Returns a list of statement dicts in our normalized format:
    [{"sid": "...", "service": "s3", "effect": "Allow", "actions": [...], "resources": [...]}]
    """
    resource_prefix = f"doh-{environment.slug}-{app.slug}"
    role_name = f"{resource_prefix}-task-role"[:64]

    try:
        session = _get_aws_session_for_environment(environment)
        iam_client = session.client("iam")
    except Exception:
        logger.exception("Failed to assume role for environment %s", environment.slug)
        return []

    statements = []
    try:
        policy_names_response = iam_client.list_role_policies(RoleName=role_name)
        policy_names = policy_names_response.get("PolicyNames", [])

        for policy_name in policy_names:
            policy_response = iam_client.get_role_policy(RoleName=role_name, PolicyName=policy_name)
            policy_document = policy_response.get("PolicyDocument", {})
            raw_statements = policy_document.get("Statement", [])

            for idx, raw_stmt in enumerate(raw_statements):
                actions = raw_stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]

                resources = raw_stmt.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]

                # Extract service prefix from the first action (e.g., "s3:GetObject" -> "s3")
                service = "unknown"
                if actions:
                    first_action = actions[0]
                    if ":" in first_action:
                        service = first_action.split(":")[0]

                # Strip service prefix from actions for display (e.g., "s3:GetObject" -> "GetObject")
                short_actions = []
                for action in actions:
                    short_actions.append(action.split(":")[-1] if ":" in action else action)

                statements.append({
                    "sid": raw_stmt.get("Sid", f"{policy_name}-stmt-{idx}"),
                    "service": service,
                    "effect": raw_stmt.get("Effect", "Allow"),
                    "actions": short_actions,
                    "resources": resources,
                })

    except iam_client.exceptions.NoSuchEntityException:
        logger.info("Task role %s not found — app may not be deployed yet", role_name)
    except Exception:
        logger.exception("Failed to read task role policies for %s", role_name)

    return statements
