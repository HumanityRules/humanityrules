"""
App removal executor.

Runs an AppRemovalJob: optionally cleans EFS app data and Secrets Manager secrets
in every environment the app has a blueprint in, then deletes the App row (FK
cascades handle blueprints, deployments, logs, permissions, tags).
"""

import json
import logging
import time

from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction

from devopshero_app import models
from devopshero_app.services.infra_customer import cloudformation_utils
from devopshero_app.services.infra_customer import iam_utils
from devopshero_app.services.infra_customer import secrets_utils

logger = logging.getLogger(__name__)


def _find_live_deployments(app: "models.App") -> list[tuple[str, str]]:
    """Return (env_slug, deployment_status) for every env whose latest deployment isn't torn down."""
    latest_by_env: dict = {}
    for env_id, env_slug, status, created_at in models.Deployment.objects.filter(app=app).values_list(
        "environment_id", "environment__slug", "status", "created_at",
    ):
        existing = latest_by_env.get(env_id)
        if existing is None or created_at > existing[2]:
            latest_by_env[env_id] = (env_slug, status, created_at)
    return [
        (env_slug, status)
        for env_slug, status, _ in latest_by_env.values()
        if status != models.Deployment.Status.TORN_DOWN
    ]


CLEANUP_CONTAINER_NAME = "efs-remover"
CLEANUP_CONTAINER_IMAGE = "public.ecr.aws/amazonlinux/amazonlinux:2023"
CLEANUP_EFS_MOUNT_PATH = "/mnt/efs"
# Wait up to 15 minutes for the Fargate task to finish rm -rf
CLEANUP_MAX_WAIT_ITERATIONS = 180
CLEANUP_POLL_INTERVAL_SECONDS = 5


def _get_env_session(environment: models.Environment):
    """Assume the DevOpsHero role in the environment's customer AWS account."""
    aws_account = environment.aws_account
    return iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def run_removal(job_id: str) -> bool:
    """Main entry point called by the job worker."""
    try:
        job = models.AppRemovalJob.objects.get(id=job_id)
    except models.AppRemovalJob.DoesNotExist:
        logger.error("AppRemovalJob %s not found", job_id)
        return False

    app = (
        models.App.objects
        .select_related("workspace", "source_template")
        .filter(id=job.app_id_snapshot)
        .first()
    )
    if app is None:
        _mark(job, models.AppRemovalJob.Status.SUCCEEDED, "App row already gone; nothing to do.")
        return True

    live = _find_live_deployments(app)
    if live:
        detail = ", ".join(f"{env}={status}" for env, status in live)
        _fail(job, app, f"App became live again ({detail}); cannot remove.")
        return False

    environments = list(
        models.Environment.objects
        .filter(blueprints__app=app)
        .select_related("aws_account")
        .distinct()
    )

    try:
        if job.delete_efs_data:
            if not app.source_template or not app.source_template.efs_config:
                logger.info("Skipping EFS cleanup: app has no EFS config")
            else:
                for env in environments:
                    ok, message = _run_efs_cleanup_task(env=env, app_slug=app.slug)
                    if not ok:
                        _fail(job, app, f"EFS cleanup failed in '{env.slug}': {message}")
                        return False

        if job.delete_secrets:
            for env in environments:
                session = _get_env_session(env)
                secrets_utils.delete_secrets_matching_prefix(
                    session=session,
                    subprefix=f"devopshero/{env.slug}/{app.slug}/",
                    dry_run=False,
                    force_immediate=True,
                )
    except ClientError as e:
        logger.exception("AWS cleanup failed: %s", e)
        _fail(job, app, f"AWS cleanup failed: {e}")
        return False

    with transaction.atomic():
        if job.delete_policies:
            _delete_matching_policies(organization_id=app.organization_id, app_slug=app.slug)
        # Cascade deletes DeploymentBlueprint, Deployment, DeploymentLog, AppPermissions,
        # AppPermissionRequest, and ResourceTag rows that point at this app.
        # Conversation.context_app is SET_NULL. Policy has no FK to App; matching rows
        # are handled above when delete_policies is set.
        app.delete()

    _mark(job, models.AppRemovalJob.Status.SUCCEEDED, "App removed.")
    return True


def _delete_matching_policies(organization_id, app_slug: str) -> None:
    """Delete Policy rows whose resource_conditions target app-name=<app_slug>."""
    candidates = models.Policy.objects.filter(
        organization_id=organization_id,
        resource_type=models.Policy.ResourceType.APP,
    )
    matching_ids = [
        p.id for p in candidates
        if any(
            c.get("key") == "app-name" and c.get("value") == app_slug
            for c in (p.resource_conditions or [])
        )
    ]
    if matching_ids:
        deleted, _ = models.Policy.objects.filter(id__in=matching_ids).delete()
        logger.info("Deleted %d policies matching app-name=%s", deleted, app_slug)


def _mark(job: models.AppRemovalJob, status: str, message: str) -> None:
    job.status = status
    job.status_message = message
    job.save(update_fields=["status", "status_message", "updated_at"])
    logger.info("AppRemovalJob %s -> %s: %s", job.id, status, message)


def _fail(job: models.AppRemovalJob, app: "models.App", message: str) -> None:
    """Mark the job as failed and revert the app out of PENDING_REMOVAL so the user can retry."""
    with transaction.atomic():
        _mark(job, models.AppRemovalJob.Status.FAILED, message)
        if app.status == models.App.Status.PENDING_REMOVAL:
            app.status = models.App.Status.ACTIVE
            app.save(update_fields=["status", "updated_at"])


def fail_from_worker(job_id: str, message: str) -> None:
    """Called from the worker's top-level exception handler; best-effort revert of app state."""
    try:
        job = models.AppRemovalJob.objects.get(id=job_id)
    except models.AppRemovalJob.DoesNotExist:
        return
    app = models.App.objects.filter(id=job.app_id_snapshot).first()
    if app is None:
        _mark(job, models.AppRemovalJob.Status.FAILED, message)
    else:
        _fail(job, app, message)


# ---------------------------------------------------------------------------
# EFS cleanup via one-shot Fargate task
# ---------------------------------------------------------------------------


def _run_efs_cleanup_task(env: models.Environment, app_slug: str) -> tuple[bool, str]:
    """Run a one-shot Fargate task that rm -rf's /deployments/{app_slug} on the env's EFS."""
    session = _get_env_session(env)
    cf_client = session.client("cloudformation")
    ecs_client = session.client("ecs")
    iam_client = session.client("iam")

    try:
        infra = _get_infra_info(cf_client=cf_client, env_slug=env.slug)
    except RuntimeError as e:
        return False, str(e)

    account_id = env.aws_account.aws_account_id
    region = env.aws_region
    role_name = f"devopshero-{env.slug}-efs-remover-role"
    task_family = f"devopshero-{env.slug}-app-efs-remove"
    cluster_name = f"devopshero-{env.slug}-cluster"
    efs_filesystem_arn = (
        f"arn:aws:elasticfilesystem:{region}:{account_id}:file-system/{infra['efs_fs_id']}"
    )
    exec_role_arn = (
        f"arn:aws:iam::{account_id}:role/devopshero-{env.slug}-task-execution-role"
    )
    log_group = f"/devopshero/{env.slug}/ecs"

    task_def_arn = None
    task_arn = None
    try:
        task_role_arn = _ensure_remover_role(
            iam_client=iam_client,
            role_name=role_name,
            efs_filesystem_arn=efs_filesystem_arn,
        )
        # IAM role propagation — matches the sleep in doh_efs_browse
        time.sleep(10)

        task_def_arn = _register_remover_task_definition(
            ecs_client=ecs_client,
            family=task_family,
            task_role_arn=task_role_arn,
            execution_role_arn=exec_role_arn,
            efs_fs_id=infra["efs_fs_id"],
            log_group=log_group,
            region=region,
            app_slug=app_slug,
        )

        resp = ecs_client.run_task(
            cluster=cluster_name,
            taskDefinition=task_def_arn,
            launchType="FARGATE",
            enableExecuteCommand=False,
            platformVersion="LATEST",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [infra["subnet_1"], infra["subnet_2"]],
                    "securityGroups": [infra["default_sg"], infra["efs_sg"]],
                    "assignPublicIp": "DISABLED",
                },
            },
        )
        failures = resp.get("failures", [])
        if failures:
            reasons = ", ".join(f["reason"] for f in failures)
            return False, f"run_task failed: {reasons}"
        task_arn = resp["tasks"][0]["taskArn"]
        logger.info("EFS cleanup task started: %s", task_arn)

        return _wait_for_stopped(ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn)
    except ClientError as e:
        logger.exception("EFS cleanup task error")
        return False, str(e)
    finally:
        if task_def_arn:
            try:
                ecs_client.deregister_task_definition(taskDefinition=task_def_arn)
                ecs_client.delete_task_definitions(taskDefinitions=[task_def_arn])
            except ClientError:
                pass


def _get_infra_info(cf_client, env_slug: str) -> dict[str, str]:
    vpc_stack = f"devopshero-{env_slug}-vpc"
    efs_stack = f"devopshero-{env_slug}-efs"
    lookups = {
        "efs_fs_id": (efs_stack, "EfsFileSystemId"),
        "efs_sg": (efs_stack, "EfsSecurityGroupId"),
        "subnet_1": (vpc_stack, "PrivateSubnet1Id"),
        "subnet_2": (vpc_stack, "PrivateSubnet2Id"),
        "default_sg": (vpc_stack, "DefaultSecurityGroupId"),
    }
    result: dict[str, str] = {}
    for key, (stack, output_key) in lookups.items():
        value = cloudformation_utils.get_stack_output(cf_client, stack, output_key)
        if not value:
            raise RuntimeError(f"Missing CloudFormation output {output_key} on stack {stack}")
        result[key] = value
    return result


def _ensure_remover_role(iam_client, role_name: str, efs_filesystem_arn: str) -> str:
    trust_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    })
    try:
        resp = iam_client.get_role(RoleName=role_name)
        role_arn = resp["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        resp = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description="One-shot EFS cleanup task role",
            Tags=[{"Key": "devopshero:purpose", "Value": "efs-remove"}],
        )
        role_arn = resp["Role"]["Arn"]

    # Grant root EFS access so rm -rf can descend into the per-app access-point subtree
    efs_policy = json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "elasticfilesystem:ClientMount",
                "elasticfilesystem:ClientWrite",
                "elasticfilesystem:ClientRootAccess",
            ],
            "Resource": efs_filesystem_arn,
        }],
    })
    iam_client.put_role_policy(RoleName=role_name, PolicyName="efs-root-access", PolicyDocument=efs_policy)
    return role_arn


def _register_remover_task_definition(
    ecs_client,
    family: str,
    task_role_arn: str,
    execution_role_arn: str,
    efs_fs_id: str,
    log_group: str,
    region: str,
    app_slug: str,
) -> str:
    target_path = f"{CLEANUP_EFS_MOUNT_PATH}/deployments/{app_slug}"
    command = (
        "set -e; "
        f"if [ -d '{target_path}' ]; then rm -rf '{target_path}'; echo removed; "
        f"else echo 'already absent'; fi"
    )
    resp = ecs_client.register_task_definition(
        family=family,
        taskRoleArn=task_role_arn,
        executionRoleArn=execution_role_arn,
        networkMode="awsvpc",
        requiresCompatibilities=["FARGATE"],
        cpu="256",
        memory="512",
        runtimePlatform={"cpuArchitecture": "X86_64", "operatingSystemFamily": "LINUX"},
        volumes=[{
            "name": "efs-root",
            "efsVolumeConfiguration": {
                "fileSystemId": efs_fs_id,
                "transitEncryption": "ENABLED",
                "authorizationConfig": {"iam": "ENABLED"},
            },
        }],
        containerDefinitions=[{
            "name": CLEANUP_CONTAINER_NAME,
            "image": CLEANUP_CONTAINER_IMAGE,
            "essential": True,
            "command": ["sh", "-c", command],
            "mountPoints": [{
                "containerPath": CLEANUP_EFS_MOUNT_PATH,
                "sourceVolume": "efs-root",
                "readOnly": False,
            }],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": log_group,
                    "awslogs-region": region,
                    "awslogs-stream-prefix": "efs-remove",
                },
            },
            "linuxParameters": {"initProcessEnabled": True},
        }],
    )
    return resp["taskDefinition"]["taskDefinitionArn"]


def _wait_for_stopped(ecs_client, cluster: str, task_arn: str) -> tuple[bool, str]:
    for _ in range(CLEANUP_MAX_WAIT_ITERATIONS):
        resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
        if not resp["tasks"]:
            return False, "describe_tasks returned no task"
        task = resp["tasks"][0]
        if task["lastStatus"] == "STOPPED":
            containers = task.get("containers", [])
            if not containers:
                return False, f"task stopped with no containers: {task.get('stoppedReason', '')}"
            exit_code = containers[0].get("exitCode")
            if exit_code == 0:
                return True, "ok"
            return False, task.get("stoppedReason") or f"exit={exit_code}"
        time.sleep(CLEANUP_POLL_INTERVAL_SECONDS)
    return False, "timed out waiting for task to stop"
