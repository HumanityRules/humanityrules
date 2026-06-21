"""Configure Amazon Bedrock model invocation logging for a customer account.

Model invocation logging is an account + Region-level singleton configured through the Bedrock
control-plane API — there is no native CloudFormation resource for it. Because the configuration
is account/Region-scoped rather than per-environment, the CloudWatch log group and the IAM role
Bedrock assumes are shared across every DevOpsHero environment in the account: they use fixed,
env-agnostic names and are created idempotently on each environment deploy. Resources are left in
place on environment teardown (there is no account-offboarding hook to remove them, and sibling
environments may still rely on them).

Only invocation metadata and token counts are logged: all four data-delivery modalities (text,
image, embedding, video) are left disabled, so prompt/response content is never written.
"""

import json
import logging
import time

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Shared, env-agnostic resource names. The log group is regional (one per Region, same name); the
# IAM role is account-global and serves every Region via wildcard conditions.
LOG_GROUP_NAME = "/devopshero/bedrock-invocations"
LOG_STREAM_NAME = "aws/bedrock/modelinvocations"
ROLE_NAME = "devopshero-bedrock-logging-role"
LOG_GROUP_RETENTION_DAYS = 30

# Bedrock validates that it can assume the supplied role when the configuration is set. A freshly
# created role may not have propagated through IAM yet, surfacing as a ValidationException, so we
# retry a few times before giving up.
_ROLE_PROPAGATION_MAX_ATTEMPTS = 6
_ROLE_PROPAGATION_DELAY_SECONDS = 5


def ensure_invocation_logging(session) -> bool:
    """Idempotently enable account-scoped Bedrock invocation logging (metadata only) for the session's Region.

    Ensures the shared CloudWatch log group and IAM role exist, then applies the logging
    configuration. Safe to call on every environment deploy. Returns True on success.
    """
    account_id = session.client("sts").get_caller_identity()["Account"]
    _ensure_log_group(logs_client=session.client("logs"))
    role_arn = _ensure_logging_role(iam_client=session.client("iam"), account_id=account_id)
    return _put_logging_config(bedrock_client=session.client("bedrock"), role_arn=role_arn)


def _ensure_log_group(logs_client) -> None:
    """Create the shared Bedrock log group in this Region (if absent) and set its retention."""
    try:
        logs_client.create_log_group(logGroupName=LOG_GROUP_NAME)
        logger.info("Created Bedrock log group '%(log_group)s'", {"log_group": LOG_GROUP_NAME})
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass
    logs_client.put_retention_policy(logGroupName=LOG_GROUP_NAME, retentionInDays=LOG_GROUP_RETENTION_DAYS)


def _ensure_logging_role(iam_client, account_id: str) -> str:
    """Create (or reuse) the account-global IAM role Bedrock assumes to write logs; return its ARN."""
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
            # Confused-deputy guards; wildcard Region so one role serves every Region in the account.
            "Condition": {
                "StringEquals": {"aws:SourceAccount": account_id},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:*:{account_id}:*"},
            },
        }],
    }
    try:
        role = iam_client.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Allows Amazon Bedrock to write model invocation logs to CloudWatch.",
        )
        role_arn = role["Role"]["Arn"]
        logger.info("Created Bedrock logging role '%(role)s'", {"role": ROLE_NAME})
    except iam_client.exceptions.EntityAlreadyExistsException:
        role_arn = iam_client.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]

    permission_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            # Wildcard Region matches the same-named log group in whichever Region logging is enabled.
            "Resource": f"arn:aws:logs:*:{account_id}:log-group:{LOG_GROUP_NAME}:log-stream:{LOG_STREAM_NAME}",
        }],
    }
    # Overwrite on every call so policy changes self-heal on the next deploy.
    iam_client.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="bedrock-invocation-logging",
        PolicyDocument=json.dumps(permission_policy),
    )
    return role_arn


def _put_logging_config(bedrock_client, role_arn: str) -> bool:
    """Apply the CloudWatch logging configuration (all data modalities disabled), retrying on IAM lag."""
    logging_config = {
        "cloudWatchConfig": {"logGroupName": LOG_GROUP_NAME, "roleArn": role_arn},
        "textDataDeliveryEnabled": False,
        "imageDataDeliveryEnabled": False,
        "embeddingDataDeliveryEnabled": False,
        "videoDataDeliveryEnabled": False,
    }
    for attempt in range(1, _ROLE_PROPAGATION_MAX_ATTEMPTS + 1):
        try:
            bedrock_client.put_model_invocation_logging_configuration(loggingConfig=logging_config)
            logger.info(
                "Enabled Bedrock invocation logging to CloudWatch log group '%(log_group)s'",
                {"log_group": LOG_GROUP_NAME},
            )
            return True
        except ClientError as e:
            is_last_attempt = attempt == _ROLE_PROPAGATION_MAX_ATTEMPTS
            # A ValidationException here means Bedrock could not (yet) assume the just-created role.
            role_not_propagated = e.response["Error"]["Code"] == "ValidationException"
            if not role_not_propagated or is_last_attempt:
                logger.error("Failed to enable Bedrock invocation logging: %(error)s", {"error": str(e)})
                return False
            logger.info(
                "Bedrock rejected logging role (attempt %(attempt)d/%(max)d), retrying after IAM propagation",
                {"attempt": attempt, "max": _ROLE_PROPAGATION_MAX_ATTEMPTS},
            )
            time.sleep(_ROLE_PROPAGATION_DELAY_SECONDS)

    return False  # Defensive: the loop above always returns within its body.
