"""
Open an interactive shell in a deployed customer app container via ECS Exec.

ECS Exec uses the SSM Session Manager plugin under the hood (same tooling as
doh_efs_browse). This command connects to an existing Fargate task for the app
service — it does not start a temporary task.

Usage:
    uv run manage.py doh_app_shell --account "CH Sandbox" --app my-app-slug
    uv run manage.py doh_app_shell --account "CH Sandbox" --env prod --app my-app-slug
    uv run manage.py doh_app_shell --account "CH Sandbox" --org "Course Hero" --app my-app-slug

Requires:
    - AWS CLI v2
    - AWS Session Manager Plugin (brew install --cask session-manager-plugin)
    - DOH_AWS_ACCESS_KEY and DOH_AWS_SECRET_KEY in .env
"""

import os
import subprocess
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from devopshero_app.models import AWSAccount, App, Environment, Organization
from devopshero_app.services.infra_customer import iam_utils


def _get_aws_account(identifier: str, org_slug: str | None) -> AWSAccount:
    """Look up an AWSAccount by name or 12-digit account ID, optionally scoped to an org."""
    qs = AWSAccount.objects.all()

    if org_slug:
        org = Organization.objects.filter(slug=org_slug).first() or Organization.objects.filter(name=org_slug).first()
        if not org:
            raise CommandError(f"No organization matching '{org_slug}' found (tried slug and name).")
        qs = qs.filter(organization=org)

    if identifier.isdigit() and len(identifier) == 12:
        try:
            return qs.get(aws_account_id=identifier)
        except AWSAccount.DoesNotExist:
            raise CommandError(f"AWS account with ID '{identifier}' not found")
        except AWSAccount.MultipleObjectsReturned:
            raise CommandError(f"Multiple accounts with ID '{identifier}'. Use --org to disambiguate.")
    try:
        return qs.get(name=identifier)
    except AWSAccount.DoesNotExist:
        raise CommandError(f"AWS account '{identifier}' not found")
    except AWSAccount.MultipleObjectsReturned:
        raise CommandError(
            f"Multiple accounts named '{identifier}'. Use --org or the 12-digit account ID to disambiguate."
        )


def _wait_for_task_running(ecs_client, cluster: str, task_arn: str, stdout) -> None:
    """Poll until the task reaches RUNNING state."""
    stdout.write("Waiting for task to reach RUNNING...")
    for i in range(60):
        resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = resp["tasks"][0]
        status = task["lastStatus"]

        if status == "RUNNING":
            stdout.write("Task is RUNNING.")
            return

        if status in ("STOPPED", "DEPROVISIONING"):
            reason = task.get("stoppedReason", "Unknown")
            raise CommandError(f"Task stopped before RUNNING: {reason}")

        if i % 6 == 0:
            stdout.write(f"  Status: {status}")
        time.sleep(5)

    raise CommandError("Timed out waiting for task to reach RUNNING (5 minutes)")


def _wait_for_exec_agent(ecs_client, cluster: str, task_arn: str, container_name: str, stdout) -> None:
    """Wait for the ECS Exec (SSM) managed agent to reach RUNNING for the app container."""
    stdout.write("Waiting for ECS Exec agent...")
    for i in range(36):
        resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
        task = resp["tasks"][0]

        if not task.get("enableExecuteCommand"):
            raise CommandError(
                "This task does not have ECS Exec enabled (enableExecuteCommand is false). "
                "Redeploy the app with a current stack; customer app services use enable_execute_command=True."
            )

        for container in task.get("containers", []):
            if container["name"] != container_name:
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


def _run_execute_command(
    session,
    cluster: str,
    task_id: str,
    container_name: str,
    command: str,
    stdout,
) -> None:
    """Open an interactive shell via aws ecs execute-command, with retries."""
    creds = session.get_credentials().get_frozen_credentials()

    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    env["AWS_SESSION_TOKEN"] = creds.token
    region = session.region_name
    if region:
        env["AWS_DEFAULT_REGION"] = region

    cmd = [
        "aws",
        "ecs",
        "execute-command",
        "--cluster",
        cluster,
        "--task",
        task_id,
        "--container",
        container_name,
        "--interactive",
        "--command",
        command,
    ]

    max_retries = 3
    for attempt in range(max_retries):
        result = subprocess.run(cmd, env=env)
        if result.returncode == 0:
            return
        if attempt < max_retries - 1:
            stdout.write(f"Connection failed (attempt {attempt + 1}/{max_retries}), retrying in 10s...")
            time.sleep(10)


class Command(BaseCommand):
    help = "Open an interactive shell in a customer app container (ECS Exec / SSM)"

    def add_arguments(self, parser):
        parser.add_argument("--account", required=True, help="AWS account name or 12-digit account ID")
        parser.add_argument("--org", help="Organization name or slug (required when account name is ambiguous)")
        parser.add_argument("--env", default="default", help="Environment slug (default: default)")
        parser.add_argument("--app", required=True, help="App slug (same as ECS container name)")
        parser.add_argument(
            "--command",
            default="/bin/bash",
            help="Executable to run inside the container (default: /bin/bash)",
        )

    def handle(self, *args, **options):
        env_slug = options["env"]
        app_slug = options["app"]
        shell_command = options["command"]

        access_key = settings.DOH_AWS_ACCESS_KEY
        secret_key = settings.DOH_AWS_SECRET_KEY
        if not access_key or not secret_key:
            raise CommandError("Missing DOH_AWS_ACCESS_KEY and/or DOH_AWS_SECRET_KEY in .env")

        aws_account = _get_aws_account(identifier=options["account"], org_slug=options.get("org"))

        try:
            environment = Environment.objects.get(aws_account=aws_account, slug=env_slug)
        except Environment.DoesNotExist:
            raise CommandError(f"No environment with slug '{env_slug}' for AWS account '{aws_account.name}'.")

        try:
            app = App.objects.get(organization=aws_account.organization, slug=app_slug)
        except App.DoesNotExist:
            raise CommandError(f"No app with slug '{app_slug}' in organization '{aws_account.organization.name}'.")

        region = environment.aws_region
        session = iam_utils.get_assumed_role_session(
            access_key=access_key,
            secret_key=secret_key,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=region,
        )

        cluster_name = f"devopshero-{env_slug}-cluster"
        service_name = f"doh-{env_slug}-{app.slug}"
        container_name = app.slug

        ecs_client = session.client("ecs")

        list_resp = ecs_client.list_tasks(cluster=cluster_name, serviceName=service_name, desiredStatus="RUNNING")
        task_arns = list_resp.get("taskArns") or []
        if not task_arns:
            raise CommandError(
                f"No RUNNING tasks for service '{service_name}' in cluster '{cluster_name}'. "
                f"Is the app deployed and healthy in this environment?"
            )

        task_arn = task_arns[0]
        task_id = task_arn.split("/")[-1]

        self.stdout.write(f"Cluster:   {cluster_name}")
        self.stdout.write(f"Service:   {service_name}")
        self.stdout.write(f"Task:      {task_id}")
        self.stdout.write(f"Container: {container_name}")
        self.stdout.write("")

        _wait_for_task_running(ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, stdout=self.stdout)
        _wait_for_exec_agent(
            ecs_client=ecs_client,
            cluster=cluster_name,
            task_arn=task_arn,
            container_name=container_name,
            stdout=self.stdout,
        )

        self.stdout.write(self.style.SUCCESS("Connected. Type 'exit' to disconnect.\n"))

        _run_execute_command(
            session=session,
            cluster=cluster_name,
            task_id=task_id,
            container_name=container_name,
            command=shell_command,
            stdout=self.stdout,
        )
