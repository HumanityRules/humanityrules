"""
Browse EFS filesystem in a customer environment via ECS Exec.

Usage:
    uv run manage.py humr_efs_browse --account "Humanity Rules Sandbox" --env sandbox
    uv run manage.py humr_efs_browse --account "Humanity Rules Sandbox" --env sandbox --org "Humanity Rules"
    uv run manage.py humr_efs_browse --account "Humanity Rules Sandbox" --env sandbox --writable

    ./prod_manage.sh humr_efs_browse --account "Humanity Rules Sandbox" --env sandbox --org humanity-rules

Spins up a temporary Fargate task with the root EFS volume mounted (no access
point, so you see all app data), then opens an interactive bash shell via ECS
Exec (SSM Session Manager). On exit, the task, its task definition, and its IAM
role are all deleted.

The mount is read-only unless --writable is passed: the root of this filesystem
holds every agent's checkpoint, and a stray rm in that shell is unrecoverable.
--writable also adds elasticfilesystem:ClientWrite to the task role, so a
read-only session cannot write even if the mount were remounted.

The EFS root contains /deployments/<app-name>/ directories — one per deployed
app that uses the fargate_web_efs stack profile.

Requires:
    - AWS CLI v2
    - AWS Session Manager Plugin (brew install --cask session-manager-plugin)
    - HUMR_AWS_ACCESS_KEY and HUMR_AWS_SECRET_KEY in .env
"""

import os
import subprocess
import time

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.services.infra_customer import cloudformation_utils
from humanityrules_app.services.infra_customer import ephemeral_task_role

from ._aws_account_resolver import add_aws_target_args, resolve_aws_target


CONTAINER_NAME = "efs-browser"
CONTAINER_IMAGE = "public.ecr.aws/amazonlinux/amazonlinux:2023"
EFS_MOUNT_PATH = "/efs"

# RunTask rejects a role IAM has not propagated yet; retry rather than pre-sleeping.
TASK_START_ATTEMPTS = 6
ROLE_PROPAGATION_RETRY_SECONDS = 3


class Command(BaseCommand):
    help = "Browse EFS filesystem via ECS Exec (interactive shell)"

    def add_arguments(self, parser):
        add_aws_target_args(parser=parser, env_default=None)
        parser.add_argument(
            "--writable",
            action="store_true",
            help="Mount the filesystem read-write (default: read-only)",
        )

    def handle(self, *args, **options):
        target = resolve_aws_target(options=options)
        session = target.session
        env_slug = target.env_slug
        region = target.aws_region
        account_id = target.aws_account_id
        writable = options["writable"]

        cf_client = session.client("cloudformation")
        ecs_client = session.client("ecs")
        iam_client = session.client("iam")

        infra = _get_infra_info(cf_client=cf_client, env_slug=env_slug, stdout=self.stdout)

        cluster_name = f"humr-{env_slug}-cluster"
        role_name = ephemeral_task_role.unique_role_name(prefix=f"humr-{env_slug}-efs-browser")
        task_family = f"humr-{env_slug}-efs-browser"

        task_arn = None
        task_def_arn = None
        role_created = False

        try:
            self.stdout.write(f"Creating task role: {role_name}")
            task_role_arn = ephemeral_task_role.create_role(
                iam_client=iam_client,
                role_name=role_name,
                description="EFS browser task role for ECS Exec",
                purpose="efs-browser",
                inline_policies={
                    "ecs-exec-ssm": ephemeral_task_role.ecs_exec_ssm_policy(),
                    "efs-access": ephemeral_task_role.efs_access_policy(
                        efs_filesystem_arn=f"arn:aws:elasticfilesystem:{region}:{account_id}:file-system/{infra['efs_fs_id']}",
                        allow_write=writable,
                    ),
                },
            )
            role_created = True

            exec_role_arn = f"arn:aws:iam::{account_id}:role/humr-{env_slug}-task-execution-role"
            task_def_arn = _register_task_definition(
                ecs_client=ecs_client,
                family=task_family,
                task_role_arn=task_role_arn,
                execution_role_arn=exec_role_arn,
                efs_fs_id=infra["efs_fs_id"],
                log_group=f"/humr/{env_slug}/ecs",
                region=region,
                writable=writable,
                stdout=self.stdout,
            )

            task_arn = _start_task(
                ecs_client=ecs_client,
                cluster=cluster_name,
                task_def_arn=task_def_arn,
                subnets=[infra["subnet_1"], infra["subnet_2"]],
                security_groups=[infra["default_sg"], infra["efs_sg"]],
                stdout=self.stdout,
            )

            _wait_for_exec_agent(ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, stdout=self.stdout)

            mode = "read-write" if writable else "read-only"
            self.stdout.write(self.style.SUCCESS(f"\nEFS mounted at {EFS_MOUNT_PATH} ({mode})"))
            self.stdout.write("App data lives under /efs/deployments/<app-name>/")
            self.stdout.write("Type 'exit' to disconnect and stop the task.\n")

            _exec_interactive(session=session, cluster=cluster_name, task_arn=task_arn, region=region, stdout=self.stdout)

        except KeyboardInterrupt:
            self.stdout.write("\nInterrupted.")
        finally:
            if task_arn:
                self.stdout.write("\nStopping task...")
                _stop_task(ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, stdout=self.stdout)
            if task_def_arn:
                _deregister_task_def(ecs_client=ecs_client, task_def_arn=task_def_arn)
            if role_created:
                ephemeral_task_role.delete_role(iam_client=iam_client, role_name=role_name)
                self.stdout.write(f"Deleted task role: {role_name}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_infra_info(cf_client, env_slug: str, stdout) -> dict[str, str]:
    """Fetch EFS, VPC, and security group IDs from CloudFormation stack outputs."""
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
            raise CommandError(f"Missing CloudFormation output: {output_key} from stack {stack}")
        result[key] = value

    stdout.write(f"EFS filesystem: {result['efs_fs_id']}")
    return result


def _register_task_definition(ecs_client, family: str, task_role_arn: str, execution_role_arn: str, efs_fs_id: str, log_group: str, region: str, writable: bool, stdout) -> str:
    """Register a Fargate task definition with the root EFS volume."""
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
            "name": CONTAINER_NAME,
            "image": CONTAINER_IMAGE,
            "essential": True,
            "command": [
                "sh",
                "-c",
                "set -e; dnf install -y vim-minimal less tree findutils tar gzip procps-ng; exec sleep infinity",
            ],
            "mountPoints": [{
                "containerPath": EFS_MOUNT_PATH,
                "sourceVolume": "efs-root",
                "readOnly": not writable,
            }],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": log_group,
                    "awslogs-region": region,
                    "awslogs-stream-prefix": "efs-browser",
                },
            },
            "linuxParameters": {"initProcessEnabled": True},
        }],
    )
    arn = resp["taskDefinition"]["taskDefinitionArn"]
    stdout.write(f"Registered task definition: {family}")
    return arn


def _start_task(ecs_client, cluster: str, task_def_arn: str, subnets: list[str], security_groups: list[str], stdout) -> str:
    """Start the task, retrying while IAM has not yet propagated the new task role."""
    for attempt in range(TASK_START_ATTEMPTS):
        try:
            task_arn = _run_task(
                ecs_client=ecs_client,
                cluster=cluster,
                task_def_arn=task_def_arn,
                subnets=subnets,
                security_groups=security_groups,
                stdout=stdout,
            )
        except ClientError as e:
            if not ephemeral_task_role.is_assume_role_failure(stopped_reason=str(e)):
                raise
            stdout.write(f"Task role not usable yet (attempt {attempt + 1}/{TASK_START_ATTEMPTS}), retrying...")
            time.sleep(ROLE_PROPAGATION_RETRY_SECONDS)
            continue

        stopped_reason = _wait_for_running(ecs_client=ecs_client, cluster=cluster, task_arn=task_arn, stdout=stdout)
        if stopped_reason is None:
            return task_arn
        raise CommandError(f"Task stopped before reaching RUNNING: {stopped_reason}")

    raise CommandError(f"Task role was still not assumable after {TASK_START_ATTEMPTS} attempts")


def _run_task(ecs_client, cluster: str, task_def_arn: str, subnets: list[str], security_groups: list[str], stdout) -> str:
    """Run the Fargate task with ECS Exec enabled."""
    resp = ecs_client.run_task(
        cluster=cluster,
        taskDefinition=task_def_arn,
        launchType="FARGATE",
        enableExecuteCommand=True,
        platformVersion="LATEST",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": security_groups,
                "assignPublicIp": "DISABLED",
            },
        },
    )

    failures = resp.get("failures", [])
    if failures:
        reasons = ", ".join(f["reason"] for f in failures)
        raise CommandError(f"Failed to start task: {reasons}")

    task_arn = resp["tasks"][0]["taskArn"]
    stdout.write(f"Task started: {task_arn.split('/')[-1]}")
    return task_arn


def _wait_for_running(ecs_client, cluster: str, task_arn: str, stdout) -> str | None:
    """Poll until the task reaches RUNNING. Returns None on success, else the stopped reason."""
    stdout.write("Waiting for task to start (pulling image, mounting EFS)...")
    for i in range(60):
        resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = resp["tasks"][0]
        status = task["lastStatus"]

        if status == "RUNNING":
            stdout.write("Task is running.")
            return None

        if status in ("STOPPED", "DEPROVISIONING"):
            return task.get("stoppedReason", "Unknown")

        if i % 6 == 0:
            stdout.write(f"  Status: {status}")
        time.sleep(5)

    raise CommandError("Timed out waiting for task to start (5 minutes)")


def _wait_for_exec_agent(ecs_client, cluster: str, task_arn: str, stdout) -> None:
    """Wait for the ECS Exec (SSM) managed agent to reach RUNNING inside the container."""
    stdout.write("Waiting for ECS Exec agent to initialize...")
    for i in range(36):
        resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = resp["tasks"][0]

        if not task.get("enableExecuteCommand"):
            raise CommandError("Task was started without enableExecuteCommand — this is a bug")

        for container in task.get("containers", []):
            if container["name"] != CONTAINER_NAME:
                continue
            for agent in container.get("managedAgents", []):
                if agent["name"] == "ExecuteCommandAgent":
                    agent_status = agent["lastStatus"]
                    if agent_status == "RUNNING":
                        stdout.write("ECS Exec agent is ready.")
                        return
                    if i % 4 == 0 and i > 0:
                        stdout.write(f"  Agent status: {agent_status}")

        time.sleep(5)

    raise CommandError("Timed out waiting for ECS Exec agent (3 minutes)")


def _exec_interactive(session, cluster: str, task_arn: str, region: str, stdout) -> None:
    """Open interactive bash shell via aws ecs execute-command, with retries."""
    creds = session.get_credentials().get_frozen_credentials()
    task_id = task_arn.split("/")[-1]

    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    env["AWS_SESSION_TOKEN"] = creds.token
    env["AWS_DEFAULT_REGION"] = region

    cmd = [
        "aws", "ecs", "execute-command",
        "--cluster", cluster,
        "--task", task_id,
        "--container", CONTAINER_NAME,
        "--interactive",
        "--command", "/bin/bash",
    ]

    max_retries = 3
    for attempt in range(max_retries):
        result = subprocess.run(cmd, env=env)
        if result.returncode == 0:
            return
        if attempt < max_retries - 1:
            stdout.write(f"Connection failed (attempt {attempt + 1}/{max_retries}), retrying in 10s...")
            time.sleep(10)


def _stop_task(ecs_client, cluster: str, task_arn: str, stdout) -> None:
    """Stop the Fargate task."""
    try:
        ecs_client.stop_task(cluster=cluster, task=task_arn, reason="EFS browsing session ended")
        stdout.write("Task stopped.")
    except ClientError as e:
        stdout.write(f"Could not stop task: {e}")


def _deregister_task_def(ecs_client, task_def_arn: str) -> None:
    """Deregister and delete the task definition revision."""
    try:
        ecs_client.deregister_task_definition(taskDefinition=task_def_arn)
        ecs_client.delete_task_definitions(taskDefinitions=[task_def_arn])
    except ClientError:
        pass
