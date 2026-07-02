"""
Fetch CloudWatch logs for a customer app deployed via HUMR.

Works for both running and crashed/stopped tasks. By default tries running
tasks first, then falls back to stopped tasks. Use --stopped to skip straight
to stopped tasks (useful when the app is crash-looping).

Usage:
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --stopped
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --container docker-dind
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --env prod --app my-app-slug --limit 200
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --head
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --all
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --org "Course Hero"
    uv run manage.py humr_app_logs --account "Humanity Rules Sandbox" --app my-app-slug --follow

Requires HUMR_AWS_ACCESS_KEY and HUMR_AWS_SECRET_KEY in .env
"""

import time

from django.core.management.base import BaseCommand, CommandError

from humanityrules_app.models import App

from . import humr_app_shell
from ._aws_account_resolver import add_aws_target_args, resolve_aws_target


def _find_task_arn(ecs_client, cluster: str, service: str, stopped: bool) -> str | None:
    """Find the most relevant task ARN for the service."""
    if not stopped:
        resp = ecs_client.list_tasks(cluster=cluster, serviceName=service, desiredStatus="RUNNING")
        if resp.get("taskArns"):
            return resp["taskArns"][0]

    resp = ecs_client.list_tasks(cluster=cluster, serviceName=service, desiredStatus="STOPPED")
    if resp.get("taskArns"):
        tasks = ecs_client.describe_tasks(cluster=cluster, tasks=resp["taskArns"])["tasks"]
        tasks.sort(key=lambda t: t.get("stoppedAt", t.get("createdAt", "")), reverse=True)
        return tasks[0]["taskArn"]

    return None


def _requested_container(options: dict) -> str | None:
    """Resolve CLI container options into a template container name."""
    if options.get("container") and options.get("policy_proxy"):
        raise CommandError("Use either --container or --policy-proxy, not both.")
    if options.get("policy_proxy"):
        return "policy-proxy"
    return options.get("container")


def _print_task_info(ecs_client, cluster: str, task_arn: str, stdout) -> None:
    """Print task status details (useful for crashed tasks)."""
    resp = ecs_client.describe_tasks(cluster=cluster, tasks=[task_arn])
    if not resp.get("tasks"):
        return
    task = resp["tasks"][0]
    status = task.get("lastStatus", "UNKNOWN")
    stdout.write(f"  Status:      {status}")
    if task.get("stoppedReason"):
        stdout.write(f"  Stop reason: {task['stoppedReason']}")
    if task.get("stopCode"):
        stdout.write(f"  Stop code:   {task['stopCode']}")


def _fetch_and_print_logs(logs_client, log_group: str, log_stream: str, limit: int, head: bool, fetch_all: bool, stdout) -> str | None:
    """Fetch log events and print them. Returns the nextForwardToken for follow mode."""
    start_from_head = head or fetch_all
    total_printed = 0
    next_token = None
    forward_token = None

    while True:
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": log_stream,
            "startFromHead": start_from_head,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        if not fetch_all:
            kwargs["limit"] = limit

        try:
            resp = logs_client.get_log_events(**kwargs)
        except logs_client.exceptions.ResourceNotFoundException:
            raise CommandError(f"Log stream not found: {log_stream}\nLog group: {log_group}")

        events = resp.get("events", [])
        for event in events:
            stdout.write(event.get("message", "").rstrip("\n"))
            total_printed += 1

        forward_token = resp.get("nextForwardToken")

        if not fetch_all:
            break

        if not events or forward_token == next_token:
            break
        next_token = forward_token

    if total_printed == 0:
        stdout.write("(no log events found)")

    return forward_token


def _follow_logs(logs_client, log_group: str, log_stream: str, forward_token: str, poll_interval: int, stdout) -> None:
    """Continuously poll for new log events until interrupted."""
    next_token = forward_token
    while True:
        time.sleep(poll_interval)
        kwargs = {
            "logGroupName": log_group,
            "logStreamName": log_stream,
            "nextToken": next_token,
        }
        resp = logs_client.get_log_events(**kwargs)
        for event in resp.get("events", []):
            stdout.write(event.get("message", "").rstrip("\n"))
        new_token = resp.get("nextForwardToken")
        if new_token:
            next_token = new_token


class Command(BaseCommand):
    help = "Fetch CloudWatch logs for a customer app (works for running and crashed tasks)"

    def add_arguments(self, parser):
        add_aws_target_args(parser=parser, env_default="default")
        parser.add_argument("--app", required=True, help="App slug")
        parser.add_argument(
            "--container",
            help=(
                "Template container name to fetch logs for. Defaults to the sole container, "
                "or the template's ALB target when multiple containers exist."
            ),
        )
        parser.add_argument("--stopped", action="store_true", help="Look at stopped/crashed tasks (skip running)")
        parser.add_argument("--policy-proxy", action="store_true", dest="policy_proxy", help="Shortcut for --container policy-proxy")
        parser.add_argument("--limit", type=int, default=100, help="Number of log events to fetch (default: 100)")
        parser.add_argument("--head", action="store_true", help="Read from the beginning instead of the tail")
        parser.add_argument("--all", action="store_true", dest="fetch_all", help="Fetch all log events (paginate until exhausted)")
        parser.add_argument("--follow", action="store_true", help="Continuously poll for new log events (Ctrl+C to stop)")
        parser.add_argument("--follow-interval", type=int, default=2, dest="follow_interval", help="Seconds between polls in follow mode (default: 2)")

    def handle(self, *args, **options):
        app_slug = options["app"]

        target = resolve_aws_target(options=options)
        if target.aws_account is None:
            raise CommandError("humr_app_logs requires DB mode (--account/--env); raw mode is not supported.")
        aws_account = target.aws_account
        session = target.session
        env_slug = target.env_slug

        try:
            app = App.objects.select_related("source_template").get(
                organization=aws_account.organization,
                slug=app_slug,
            )
        except App.DoesNotExist:
            raise CommandError(f"No app with slug '{app_slug}' in organization '{aws_account.organization.name}'.")

        cluster_name = f"humr-{env_slug}-cluster"
        service_name = f"humr-{env_slug}-{app_slug}"
        log_group = f"/humr/{env_slug}/ecs"
        container_name = humr_app_shell._resolve_ecs_container_name(
            app=app,
            requested_container=_requested_container(options=options),
        )

        ecs_client = session.client("ecs")

        task_arn = _find_task_arn(
            ecs_client=ecs_client,
            cluster=cluster_name,
            service=service_name,
            stopped=options["stopped"],
        )
        if not task_arn:
            raise CommandError(
                f"No tasks found for service '{service_name}' in cluster '{cluster_name}'. "
                f"Is the app deployed in this environment?"
            )

        task_id = task_arn.split("/")[-1]
        log_stream = f"{container_name}/{container_name}/{task_id}"

        self.stdout.write(f"Cluster:    {cluster_name}")
        self.stdout.write(f"Service:    {service_name}")
        self.stdout.write(f"Task:       {task_id}")
        self.stdout.write(f"Container:  {container_name}")
        self.stdout.write(f"Log stream: {log_stream}")

        _print_task_info(ecs_client=ecs_client, cluster=cluster_name, task_arn=task_arn, stdout=self.stdout)
        self.stdout.write("")

        logs_client = session.client("logs")
        forward_token = _fetch_and_print_logs(
            logs_client=logs_client,
            log_group=log_group,
            log_stream=log_stream,
            limit=options["limit"],
            head=options["head"],
            fetch_all=options["fetch_all"],
            stdout=self.stdout,
        )

        if options["follow"]:
            if not forward_token:
                raise CommandError("Cannot follow: no log stream token available")
            self.stdout.write("\n--- following (Ctrl+C to stop) ---\n")
            try:
                _follow_logs(
                    logs_client=logs_client,
                    log_group=log_group,
                    log_stream=log_stream,
                    forward_token=forward_token,
                    poll_interval=options["follow_interval"],
                    stdout=self.stdout,
                )
            except KeyboardInterrupt:
                self.stdout.write("\n")
