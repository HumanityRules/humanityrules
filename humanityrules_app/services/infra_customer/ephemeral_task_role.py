"""
Lifecycle for the short-lived ECS task roles behind the EFS maintenance tasks
(``humr_efs_browse`` and the app-removal cleanup).

These roles carry EFS access over an entire filesystem, so each caller creates
one under a name unique to its invocation and deletes it in a ``finally`` block.
Nothing is left standing in the customer account between runs, and two callers
working in the same environment never share a role — deleting one caller's role
would revoke the credentials of the other's running task.

A process killed between create and delete leaves its role behind. Orphans are
identifiable by the ``humr:purpose`` tag and the name prefix.

IAM is eventually consistent, so a role created seconds ago is not yet passable
to ECS: ``run_task`` raises ClientException ("ECS was unable to assume the
role"). Callers match that with ``is_assume_role_failure`` and retry the call
rather than sleeping a fixed interval and hoping. Retrying is cheap — the task
never provisions, so nothing is wasted on a rejected attempt.
"""

import json
import logging
import secrets

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


ECS_TASKS_TRUST_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ecs-tasks.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
})

# ECS words the propagation race differently depending on which assume failed.
_ASSUME_ROLE_FAILURE_MARKERS = (
    "unable to assume",
    "sts:assumerole",
)


def unique_role_name(prefix: str) -> str:
    """Build a role name unique to one invocation, e.g. humr-sandbox-efs-browser-1a2b3c4d."""
    return f"{prefix}-{secrets.token_hex(4)}"


def create_role(iam_client, role_name: str, description: str, purpose: str, inline_policies: dict[str, dict]) -> str:
    """Create the task role with its inline policies attached. Returns the role ARN."""
    resp = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=ECS_TASKS_TRUST_POLICY,
        Description=description,
        Tags=[{"Key": "humr:purpose", "Value": purpose}],
    )
    for policy_name, policy_document in inline_policies.items():
        iam_client.put_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
        )
    return resp["Role"]["Arn"]


def delete_role(iam_client, role_name: str) -> None:
    """Delete the role and every inline policy on it. Best-effort: never raises."""
    try:
        for policy_name in iam_client.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam_client.delete_role(RoleName=role_name)
    except ClientError as e:
        logger.error("Could not delete ephemeral task role %s: %s", role_name, e)


def efs_access_policy(efs_filesystem_arn: str, allow_write: bool) -> dict:
    """Policy granting the task EFS access to one filesystem, root-squash disabled."""
    actions = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientRootAccess"]
    if allow_write:
        actions.append("elasticfilesystem:ClientWrite")
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": actions,
            "Resource": efs_filesystem_arn,
        }],
    }


def ecs_exec_ssm_policy() -> dict:
    """Policy granting the SSM channels that ECS Exec needs for an interactive session."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "ssmmessages:CreateControlChannel",
                "ssmmessages:CreateDataChannel",
                "ssmmessages:OpenControlChannel",
                "ssmmessages:OpenDataChannel",
            ],
            "Resource": "*",
        }],
    }


def is_assume_role_failure(stopped_reason: str) -> bool:
    """True when an ECS task stopped because it could not yet assume its freshly created role."""
    lowered = (stopped_reason or "").lower()
    return any(marker in lowered for marker in _ASSUME_ROLE_FAILURE_MARKERS)
