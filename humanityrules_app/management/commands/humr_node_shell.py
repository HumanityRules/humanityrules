"""
Open an interactive shell on a customer EC2 container instance via SSM Session Manager.

For inspecting the EC2 *host* that runs ECS tasks — kernel, Docker / containerd
state, host bind-mount directories (e.g. /var/lib/humr/hermes-roots/),
disk space, journal logs. This is NOT for getting inside an app container —
use humr_app_shell or humr_app_exec for that.

Usage:
    # Default: shell into the sole running container instance in the ASG.
    uv run manage.py humr_node_shell --account "Humanity Rules Sandbox" --env default

    # List every container instance with its current tasks, then exit.
    uv run manage.py humr_node_shell --account "Humanity Rules Sandbox" --env default --list

    # Shell into whichever instance is currently hosting an app's RUNNING task.
    uv run manage.py humr_node_shell --account "Humanity Rules Sandbox" --env default --app hermesvmendi00

    # Shell into a specific instance (override autodetection).
    uv run manage.py humr_node_shell --account "Humanity Rules Sandbox" --env default --instance-id i-0abc...

Notes:
    - You log in as 'ssm-user'. Use `sudo` for privileged paths like
      /var/lib/humr/hermes-roots/.
    - Permissions: the customer's HumanityRules assumed role needs ssm:StartSession;
      the container instance role already attaches AmazonSSMManagedInstanceCore
      (see EcsClusterStack in deploy_base.py), so the SSM agent is present.

Requires:
    - AWS CLI v2
    - AWS Session Manager Plugin (brew install --cask session-manager-plugin)
    - HUMR_AWS_ACCESS_KEY and HUMR_AWS_SECRET_KEY in .env
"""

import os
import subprocess

from django.core.management.base import BaseCommand, CommandError

from ._aws_account_resolver import add_aws_target_args, resolve_aws_target


def _asg_name(env_slug: str) -> str:
    """The ASG name CDK emits for the env's container instances."""
    return f"humr-{env_slug}-ecs-container-instances"


def _cluster_name(env_slug: str) -> str:
    return f"humr-{env_slug}-cluster"


def _list_running_asg_instance_ids(session, env_slug: str) -> list[str]:
    """Return EC2 instance IDs that are InService in the env's ASG and running."""
    asg_client = session.client("autoscaling")
    resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[_asg_name(env_slug)])
    groups = resp.get("AutoScalingGroups") or []
    if not groups:
        raise CommandError(f"ASG '{_asg_name(env_slug)}' not found in this account/region.")
    in_service = [
        inst["InstanceId"]
        for inst in (groups[0].get("Instances") or [])
        if inst.get("LifecycleState") == "InService"
    ]
    if not in_service:
        return []

    ec2 = session.client("ec2")
    resp = ec2.describe_instances(InstanceIds=in_service)
    running: list[str] = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            if inst.get("State", {}).get("Name") == "running":
                running.append(inst["InstanceId"])
    return running


def _find_instance_running_app(session, env_slug: str, app_slug: str) -> str | None:
    """Return the EC2 instance ID currently hosting the app's RUNNING task, or None."""
    cluster = _cluster_name(env_slug)
    service = f"humr-{env_slug}-{app_slug}"
    ecs = session.client("ecs")

    task_arns = ecs.list_tasks(
        cluster=cluster, serviceName=service, desiredStatus="RUNNING",
    ).get("taskArns") or []
    if not task_arns:
        return None

    tasks = ecs.describe_tasks(cluster=cluster, tasks=task_arns).get("tasks") or []
    ci_arns = [t["containerInstanceArn"] for t in tasks if t.get("containerInstanceArn")]
    if not ci_arns:
        return None

    container_instances = ecs.describe_container_instances(
        cluster=cluster, containerInstances=ci_arns,
    ).get("containerInstances") or []
    if not container_instances:
        return None
    return container_instances[0].get("ec2InstanceId")


def _print_inventory(session, env_slug: str, stdout) -> None:
    """Print every container instance in the env's cluster with its RUNNING tasks."""
    cluster = _cluster_name(env_slug)
    ecs = session.client("ecs")

    ci_arns = ecs.list_container_instances(cluster=cluster).get("containerInstanceArns") or []
    if not ci_arns:
        stdout.write("No container instances registered in cluster.")
        return
    container_instances = ecs.describe_container_instances(
        cluster=cluster, containerInstances=ci_arns,
    ).get("containerInstances") or []

    task_arns = ecs.list_tasks(cluster=cluster, desiredStatus="RUNNING").get("taskArns") or []
    tasks_by_ci: dict[str, list[str]] = {}
    if task_arns:
        for task in ecs.describe_tasks(cluster=cluster, tasks=task_arns).get("tasks") or []:
            ci_arn = task.get("containerInstanceArn")
            if not ci_arn:
                continue
            family = task.get("taskDefinitionArn", "").split("/")[-1].split(":")[0]
            tasks_by_ci.setdefault(ci_arn, []).append(family)

    for ci in container_instances:
        ec2_id = ci.get("ec2InstanceId", "?")
        status = ci.get("status", "?")
        agent_ok = "OK" if ci.get("agentConnected") else "DOWN"
        running = ci.get("runningTasksCount", 0)
        pending = ci.get("pendingTasksCount", 0)
        stdout.write(f"  {ec2_id}  status={status} agent={agent_ok} running={running} pending={pending}")
        for family in tasks_by_ci.get(ci["containerInstanceArn"], []):
            stdout.write(f"      task: {family}")


def _run_session(session, instance_id: str, region: str) -> int:
    """Spawn `aws ssm start-session` with the assumed-role creds injected via env."""
    creds = session.get_credentials().get_frozen_credentials()
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    if creds.token:
        env["AWS_SESSION_TOKEN"] = creds.token
    if region:
        env["AWS_DEFAULT_REGION"] = region

    cmd = ["aws", "ssm", "start-session", "--target", instance_id]
    return subprocess.run(cmd, env=env).returncode


class Command(BaseCommand):
    help = "Open an interactive shell on a customer EC2 container instance (SSM Session Manager)"

    def add_arguments(self, parser):
        add_aws_target_args(parser=parser, env_default=None)
        parser.add_argument(
            "--app",
            help="Open the shell on the EC2 instance currently hosting this app's RUNNING task (DB mode only).",
        )
        parser.add_argument(
            "--instance-id",
            dest="instance_id",
            help="Open the shell on this specific EC2 instance (skips ASG/app autodetect).",
        )
        parser.add_argument(
            "--list",
            dest="list_only",
            action="store_true",
            help="List container instances in the env's cluster (with the RUNNING tasks on each) and exit.",
        )

    def handle(self, *args, **options):
        target = resolve_aws_target(options=options)
        session = target.session
        env_slug = target.env_slug
        region = target.aws_region

        if options.get("list_only"):
            self.stdout.write(f"Account: {target.aws_account_id}")
            self.stdout.write(f"Region:  {region}")
            self.stdout.write(f"Cluster: {_cluster_name(env_slug)}")
            self.stdout.write("")
            _print_inventory(session=session, env_slug=env_slug, stdout=self.stdout)
            return

        instance_id = options.get("instance_id")
        if instance_id is None:
            app_slug = options.get("app")
            if app_slug:
                if target.aws_account is None:
                    raise CommandError(
                        "--app requires DB mode (--account/--env); raw mode cannot resolve app→instance."
                    )
                instance_id = _find_instance_running_app(
                    session=session, env_slug=env_slug, app_slug=app_slug,
                )
                if instance_id is None:
                    raise CommandError(
                        f"No RUNNING task for app '{app_slug}' in {_cluster_name(env_slug)}."
                    )
            else:
                instance_ids = _list_running_asg_instance_ids(session=session, env_slug=env_slug)
                if not instance_ids:
                    raise CommandError(
                        f"No running instances in ASG {_asg_name(env_slug)}."
                    )
                if len(instance_ids) > 1:
                    raise CommandError(
                        f"Multiple running instances in the ASG ({', '.join(instance_ids)}). "
                        f"Pick one with --instance-id, or scope with --app, or use --list to inspect."
                    )
                instance_id = instance_ids[0]

        self.stdout.write(f"Account:   {target.aws_account_id}")
        self.stdout.write(f"Region:    {region}")
        self.stdout.write(f"Cluster:   {_cluster_name(env_slug)}")
        self.stdout.write(f"Instance:  {instance_id}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "Connecting via SSM Session Manager. You'll land as ssm-user — use 'sudo' for "
            "/var/lib/humr/*. Type 'exit' to disconnect.\n"
        ))
        _run_session(session=session, instance_id=instance_id, region=region)
