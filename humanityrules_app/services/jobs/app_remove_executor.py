"""
App removal executor.

Runs an AppRemovalJob: optionally cleans persistent data (EFS app data + EC2 host
bind-mount data) and Secrets Manager secrets in the app's environment, then deletes
the App row (FK cascades handle deployments, logs, permissions, tags).
"""

import json
import logging
import time

from botocore.exceptions import ClientError
from django.conf import settings
from django.db import transaction

from humanityrules_app import models
from humanityrules_app.services import sandbox_service
from humanityrules_app.services.infra_customer import cloudformation_utils
from humanityrules_app.services.infra_customer import iam_utils
from humanityrules_app.services.infra_customer import secrets_utils

from . import app_deployment_teardown_executor
from . import tenant_consistency

logger = logging.getLogger(__name__)


# Deployment statuses that mean a deploy is mid-flight; we refuse to tear down
# from underneath an active deploy worker.
_IN_FLIGHT_DEPLOYMENT_STATUSES = frozenset({
    models.Deployment.Status.PENDING,
    models.Deployment.Status.BUILDING,
    models.Deployment.Status.PUSHING,
    models.Deployment.Status.DEPLOYING,
    models.Deployment.Status.STARTING,
    models.Deployment.Status.TEARING_DOWN,
})


def _find_live_deployment(app: "models.App") -> tuple[str, str] | None:
    """Return (deployment_id, status) for the app's latest deployment when it isn't torn down."""
    latest = models.Deployment.objects.filter(app=app).order_by("-created_at").values_list("id", "status").first()
    if latest is None:
        return None
    deployment_id, status = latest
    if status == models.Deployment.Status.TORN_DOWN:
        return None
    return str(deployment_id), status


def _teardown_live_deployment(live: tuple[str, str]) -> tuple[bool, str]:
    """Tear down the live deployment by calling run_teardown inline.

    Returns (ok, message). On failure, the deployment row already reflects
    the FAILED status (run_teardown writes it); we just propagate a message
    suitable for the AppRemovalJob status_message.
    """
    deployment_id, status = live
    if status in _IN_FLIGHT_DEPLOYMENT_STATUSES:
        return False, f"Cannot tear down: deployment in progress ({status}). Wait for it to finish."

    logger.info("Tearing down deployment %s as part of app removal", deployment_id)
    ok = app_deployment_teardown_executor.run_teardown(deployment_id=deployment_id)
    if not ok:
        return False, "Teardown failed for the app's deployment"
    return True, "ok"


CLEANUP_CONTAINER_NAME = "efs-remover"
CLEANUP_CONTAINER_IMAGE = "public.ecr.aws/amazonlinux/amazonlinux:2023"
CLEANUP_EFS_MOUNT_PATH = "/mnt/efs"
# Wait up to 15 minutes for the Fargate task to finish rm -rf
CLEANUP_MAX_WAIT_ITERATIONS = 180
CLEANUP_POLL_INTERVAL_SECONDS = 5


def _get_env_session(environment: models.Environment):
    """Assume the HumanityRules role in the environment's customer AWS account."""
    aws_account = environment.aws_account
    return iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
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
        .select_related("workspace", "source_template", "environment", "environment__aws_account")
        .filter(id=job.app_id_snapshot)
        .first()
    )
    if app is None:
        _mark(
            job,
            models.AppRemovalJob.Status.SUCCEEDED,
            f"App row already gone for '{job.app_name_snapshot}' ({job.app_slug_snapshot}); nothing to do.",
        )
        return True

    live = _find_live_deployment(app)
    if live is not None:
        if not job.teardown_first:
            _fail(job, app, f"App became live again ({live[1]}); cannot remove.")
            return False
        logger.info("teardown_first=True: tearing down the live deployment before removal")
        ok, message = _teardown_live_deployment(live=live)
        if not ok:
            _fail(job, app, message)
            return False

    env = app.environment

    try:
        tenant_consistency.assert_app_owns_environment(app=app, environment=env)
    except tenant_consistency.TenantConsistencyError as exc:
        _fail(job, app, f"Refused: {exc}")
        return False

    try:
        if job.delete_persistent_data:
            template = app.source_template
            has_efs = bool(template and template.efs_config)
            host_path_templates = _template_host_path_templates(template) if template else []
            if not has_efs and not host_path_templates:
                logger.info("Skipping persistent-data cleanup: template has no EFS or host_mounts")
            else:
                if has_efs:
                    ok, message = _run_efs_cleanup_task(env=env, app_slug=app.slug)
                    if not ok:
                        _fail(job, app, f"EFS cleanup failed in '{env.slug}': {message}")
                        return False
                if host_path_templates:
                    host_paths = _resolve_host_paths(
                        path_templates=host_path_templates,
                        app_slug=app.slug,
                        env_slug=env.slug,
                    )
                    ok, message = _run_host_path_cleanup_ssm(
                        env=env, app_slug=app.slug, host_paths=host_paths,
                    )
                    if not ok:
                        _fail(job, app, f"Host-path cleanup failed in '{env.slug}': {message}")
                        return False

        if job.delete_secrets:
            session = _get_env_session(env)
            secrets_utils.delete_secrets_matching_prefix(
                session=session,
                subprefix=f"humr/{env.slug}/{app.slug}/",
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
        # Release the shared-sandbox slug claim (if any) so the name is free for reuse. It is
        # keyed by (slug, org) with no FK to App, so app.delete() does not cascade it. Removal,
        # not teardown, frees the slug — a torn-down app keeps its App row and can redeploy.
        sandbox_service.release_sandbox_app_slug(app_slug=app.slug, organization_id=app.organization_id)
        # Cascade deletes Deployment, DeploymentLog, AppPermissions, AppPermissionRequest,
        # and ResourceTag rows that point at this app. Policy has no FK to App; matching
        # rows are handled above when delete_policies is set.
        app.delete()

    _mark(job, models.AppRemovalJob.Status.SUCCEEDED, f"App removed: '{job.app_name_snapshot}' ({job.app_slug_snapshot}).")
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
    role_name = f"humr-{env.slug}-efs-remover-role"
    task_family = f"humr-{env.slug}-app-efs-remove"
    cluster_name = f"humr-{env.slug}-cluster"
    efs_filesystem_arn = (
        f"arn:aws:elasticfilesystem:{region}:{account_id}:file-system/{infra['efs_fs_id']}"
    )
    exec_role_arn = (
        f"arn:aws:iam::{account_id}:role/humr-{env.slug}-task-execution-role"
    )
    log_group = f"/humr/{env.slug}/ecs"

    task_def_arn = None
    task_arn = None
    try:
        task_role_arn = _ensure_remover_role(
            iam_client=iam_client,
            role_name=role_name,
            efs_filesystem_arn=efs_filesystem_arn,
        )
        # IAM role propagation — matches the sleep in humr_efs_browse
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
    vpc_stack = f"humr-{env_slug}-vpc"
    efs_stack = f"humr-{env_slug}-efs"
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
            Tags=[{"Key": "humr:purpose", "Value": "efs-remove"}],
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


# ---------------------------------------------------------------------------
# EC2 host bind-mount cleanup via SSM Run Command
# ---------------------------------------------------------------------------
#
# When a template declares per-container host_mounts (e.g., hermes_agent at
# /var/lib/humr/hermes-roots/{app_slug}), the underlying directory survives
# task teardown — it lives on the EC2 container instance's EBS volume, not in any
# AWS-managed filesystem. ECS may have scheduled the task across several instances
# over its lifetime, so we broadcast a `rm -rf` to every instance in the env's ECS
# container ASG via SSM Run Command. Instances that never ran the task harmlessly
# find no directory and exit 0 — idempotent.
#
# Permissions: the container instance role already attaches AmazonSSMManagedInstanceCore
# (see deploy_base.py EcsClusterStack), so the agent is present. HUMR's assumed role
# in the customer account needs ssm:SendCommand + ssm:GetCommandInvocation, which
# the customer-side HumanityRules admin role already provides.

# Only allow rm -rf under this top-level prefix. Belt-and-suspenders guard against
# a future template typo emitting a host path outside the HUMR-owned area.
SSM_HOST_PATH_ALLOWED_PREFIX = "/var/lib/humr/"
SSM_COMMAND_TIMEOUT_SECONDS = 300
SSM_POLL_INTERVAL_SECONDS = 5
SSM_MAX_WAIT_ITERATIONS = 60  # 60 * 5s = 5 min


def _template_host_path_templates(template: "models.AppTemplate") -> list[str]:
    """Return distinct {app_slug}/{env_slug}-style host_mount source paths across all containers."""
    seen: list[str] = []
    for container in (template.containers or []):
        for mount in (container.get("host_mounts") or []):
            source_path = mount.get("source_path")
            if source_path and source_path not in seen:
                seen.append(source_path)
    return seen


def _resolve_host_paths(path_templates: list[str], app_slug: str, env_slug: str) -> list[str]:
    """Substitute {app_slug}/{env_slug} placeholders and validate the result is under the allowed prefix."""
    resolved: list[str] = []
    for template_path in path_templates:
        path = template_path.format(app_slug=app_slug, env_slug=env_slug)
        if not path.startswith(SSM_HOST_PATH_ALLOWED_PREFIX):
            raise ValueError(
                f"Refusing to clean host path outside {SSM_HOST_PATH_ALLOWED_PREFIX!r}: {path!r}"
            )
        if app_slug not in path:
            raise ValueError(
                f"Refusing to clean host path that does not include the app slug {app_slug!r}: {path!r}"
            )
        resolved.append(path)
    return resolved


def _run_host_path_cleanup_ssm(
    env: models.Environment, app_slug: str, host_paths: list[str],
) -> tuple[bool, str]:
    """SSM-broadcast `rm -rf` for each host_path on every container instance in the env's ECS ASG."""
    if not host_paths:
        return True, "no host paths to clean"

    session = _get_env_session(env)
    ssm_client = session.client("ssm")

    asg_name = f"humr-{env.slug}-ecs-container-instances"
    quoted_paths = " ".join(f"'{p}'" for p in host_paths)
    command_script = (
        "set -e; "
        f"for p in {quoted_paths}; do "
        "  if [ -d \"$p\" ]; then rm -rf \"$p\" && echo \"removed $p\"; "
        "  else echo \"absent $p\"; fi; "
        "done"
    )

    try:
        resp = ssm_client.send_command(
            DocumentName="AWS-RunShellScript",
            Targets=[{"Key": "tag:aws:autoscaling:groupName", "Values": [asg_name]}],
            Parameters={"commands": [command_script]},
            TimeoutSeconds=SSM_COMMAND_TIMEOUT_SECONDS,
            Comment=f"humr host-path cleanup for app {app_slug}",
        )
    except ClientError as e:
        logger.exception("SSM SendCommand failed")
        return False, f"SendCommand failed: {e}"

    command_id = resp["Command"]["CommandId"]
    logger.info("SSM host-path cleanup command started: %s (asg=%s)", command_id, asg_name)
    return _wait_for_ssm_command(ssm_client=ssm_client, command_id=command_id)


def _wait_for_ssm_command(ssm_client, command_id: str) -> tuple[bool, str]:
    """Poll list_command_invocations until every invocation is in a terminal state."""
    terminal_success = {"Success"}
    terminal_failure = {"Cancelled", "Failed", "TimedOut", "Cancelling"}

    for _ in range(SSM_MAX_WAIT_ITERATIONS):
        try:
            resp = ssm_client.list_command_invocations(CommandId=command_id, Details=False)
        except ClientError as e:
            return False, f"list_command_invocations failed: {e}"

        invocations = resp.get("CommandInvocations", [])
        if not invocations:
            # ASG may have zero instances right now (min_capacity=0 + scaled to 0). Nothing to do.
            logger.info("SSM command %s has no targets — treating as success (ASG empty?)", command_id)
            return True, "no instances targeted"

        statuses = [inv.get("Status", "") for inv in invocations]
        if all(s in terminal_success or s in terminal_failure for s in statuses):
            failures = [(inv.get("InstanceId", "?"), inv.get("Status", "?")) for inv in invocations if inv.get("Status") in terminal_failure]
            if failures:
                detail = ", ".join(f"{i}={s}" for i, s in failures)
                return False, f"SSM cleanup failed on instances: {detail}"
            return True, "ok"
        time.sleep(SSM_POLL_INTERVAL_SECONDS)

    return False, "timed out waiting for SSM command to finish"
