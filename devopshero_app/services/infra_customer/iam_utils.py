import logging

import boto3
from django.conf import settings
from policy_sentry.querying.actions import get_actions_with_access_level

logger = logging.getLogger(__name__)

ACCESS_LEVELS = ["Read", "Write", "List", "Tagging", "Permissions management"]


def _actions_to_access_levels(service: str, short_actions: list[str]) -> list[str]:
    """Reverse-map a list of short action names to their IAM access levels.

    An access level is included if ANY of its actions are present in the input list.
    """
    action_set = set(short_actions)
    matched_levels = []
    for level in ACCESS_LEVELS:
        try:
            level_actions = get_actions_with_access_level(service, level)
        except Exception:
            continue
        level_short = {a.split(":", 1)[1] for a in level_actions if ":" in a}
        if action_set & level_short:
            matched_levels.append(level)
    return matched_levels


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


def list_resources_for_services(environment, services: list[str]) -> dict[str, list[dict]]:
    """List actual resources for multiple AWS services, using a single assumed-role session.

    Returns {"s3": [{"arn": "...", "label": "..."}, ...], ...}.
    """
    try:
        session = _get_aws_session_for_environment(environment)
    except Exception:
        logger.exception("Failed to assume role for environment %s", environment.slug)
        return {service: [] for service in services}

    return {service: _list_resources_for_service(session, environment, service) for service in services}


def _list_resources_for_service(session, environment, service: str) -> list[dict]:
    """List actual resources in a customer account for a given AWS service.

    Returns [{"arn": "...", "label": "..."}, ...].
    For unsupported services, returns an empty list.
    """
    region = environment.aws_region
    account_id = environment.aws_account.aws_account_id

    listers = {
        "s3": lambda: _list_s3_resources(session),
        "sqs": lambda: _list_sqs_resources(session, region, account_id),
        "dynamodb": lambda: _list_dynamodb_resources(session, region, account_id),
        "secretsmanager": lambda: _list_secretsmanager_resources(session),
        "sns": lambda: _list_sns_resources(session),
        "kms": lambda: _list_kms_resources(session),
    }

    lister = listers.get(service)
    if not lister:
        return []

    try:
        return lister()
    except Exception:
        logger.exception("Failed to list %s resources for environment %s", service, environment.slug)
        return []


def _list_s3_resources(session) -> list[dict]:
    client = session.client("s3")
    buckets = client.list_buckets().get("Buckets", [])
    return [{"arn": f"arn:aws:s3:::{b['Name']}/*", "label": b["Name"]} for b in buckets]


def _list_sqs_resources(session, region, account_id) -> list[dict]:
    client = session.client("sqs")
    resp = client.list_queues()
    urls = resp.get("QueueUrls", [])
    results = []
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        arn = f"arn:aws:sqs:{region}:{account_id}:{name}"
        results.append({"arn": arn, "label": name})
    return results


def _list_dynamodb_resources(session, region, account_id) -> list[dict]:
    client = session.client("dynamodb")
    tables = client.list_tables().get("TableNames", [])
    return [{"arn": f"arn:aws:dynamodb:{region}:{account_id}:table/{t}", "label": t} for t in tables]


def _list_secretsmanager_resources(session) -> list[dict]:
    client = session.client("secretsmanager")
    secrets = client.list_secrets().get("SecretList", [])
    return [{"arn": s["ARN"], "label": s.get("Name", s["ARN"])} for s in secrets]


def _list_sns_resources(session) -> list[dict]:
    client = session.client("sns")
    topics = client.list_topics().get("Topics", [])
    results = []
    for t in topics:
        arn = t["TopicArn"]
        label = arn.rsplit(":", 1)[-1]
        results.append({"arn": arn, "label": label})
    return results


def _list_kms_resources(session) -> list[dict]:
    client = session.client("kms")
    aliases = client.list_aliases().get("Aliases", [])
    results = []
    for a in aliases:
        # Skip AWS-managed aliases
        if a.get("AliasName", "").startswith("alias/aws/"):
            continue
        target_key_arn = a.get("TargetKeyArn")
        if not target_key_arn:
            continue
        results.append({"arn": target_key_arn, "label": a.get("AliasName", target_key_arn)})
    return results


def read_app_permissions_policy(environment, app, policy_name: str) -> list[dict]:
    """Read a single inline policy from the app's ECS task role.

    Only reads the named policy, ignoring CDK-generated infrastructure policies.
    Returns a list of statement dicts in our normalized format:
    [{"service": "s3", "access_levels": ["Read", "Write"], "resources": [...]}]
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
        policy_response = iam_client.get_role_policy(
            RoleName=role_name, PolicyName=policy_name,
        )
        policy_document = policy_response.get("PolicyDocument", {})
        raw_statements = policy_document.get("Statement", [])

        for raw_stmt in raw_statements:
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

            # Strip service prefix and reverse-map to access levels
            short_actions = [a.split(":")[-1] if ":" in a else a for a in actions]
            access_levels = _actions_to_access_levels(service, short_actions)

            statements.append({
                "service": service,
                "access_levels": access_levels,
                "resources": resources,
            })

    except iam_client.exceptions.NoSuchEntityException:
        logger.info("Policy %s not found on role %s — editor starts blank", policy_name, role_name)
    except Exception:
        logger.exception("Failed to read policy %s from role %s", policy_name, role_name)

    return statements
