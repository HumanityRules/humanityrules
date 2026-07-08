"""Platform-wide fleet status for the staff-only fleet dashboard.

Two tiers: a DB snapshot (instant, intent-level) and an on-demand live fetch
that assumes the environment's cross-account role and reads actual ECS/CFN state.
"""

import logging
from dataclasses import dataclass, field

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db.models import OuterRef, Subquery

from humanityrules_app import models
from humanityrules_app.services.infra_customer import iam_utils

logger = logging.getLogger(__name__)

# CFN statuses that mean "settled and healthy" — anything else is worth surfacing.
_CFN_HEALTHY_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}

_AWS_CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})


@dataclass
class EnvGroup:
    """One org/env group for the fleet page: the env row plus its app rows."""
    organization: models.Organization
    environment: models.Environment
    deployments: list[models.Deployment] = field(default_factory=list)


@dataclass
class ServiceState:
    """Live state of one ECS service in an environment's cluster."""
    service_name: str
    status: str
    desired_count: int
    running_count: int
    pending_count: int
    rollout_state: str
    failure_reasons: list[str] = field(default_factory=list)


@dataclass
class StackState:
    """A CloudFormation stack in a non-settled or failed status."""
    stack_name: str
    stack_status: str
    status_reason: str


@dataclass
class EnvLiveState:
    """Result of an on-demand live fetch for one environment."""
    services: list[ServiceState] = field(default_factory=list)
    unhealthy_stacks: list[StackState] = field(default_factory=list)
    error: str = ""


def build_fleet_snapshot() -> list[EnvGroup]:
    """Return the DB-tier fleet view: every environment (with or without deployments), grouped by owning org."""
    groups: dict = {}
    environments = models.Environment.objects.select_related("aws_account__organization")
    for environment in environments:
        groups[environment.id] = EnvGroup(organization=environment.aws_account.organization, environment=environment)

    latest_per_app_env = (
        models.Deployment.objects
        .filter(app=OuterRef("app"), environment=OuterRef("environment"))
        .order_by("-created_at")
        .values("id")[:1]
    )
    # The app's live URL comes from its latest SUCCEEDED deploy — a failed or
    # in-progress redeploy must not hide the URL that is still serving.
    latest_succeeded_url = (
        models.Deployment.objects
        .filter(app=OuterRef("app"), environment=OuterRef("environment"), status=models.Deployment.Status.SUCCEEDED)
        .order_by("-created_at")
        .values("service_url")[:1]
    )
    deployments = (
        models.Deployment.objects
        .filter(id=Subquery(latest_per_app_env))
        .select_related("app", "environment")
        .annotate(live_service_url=Subquery(latest_succeeded_url))
        .order_by("app__slug")
    )
    for deployment in deployments:
        groups[deployment.environment_id].deployments.append(deployment)

    for group in groups.values():
        group.deployments.sort(key=lambda d: (not d.is_transient, d.app.slug))

    return sorted(groups.values(), key=lambda g: (g.organization.slug, g.environment.slug))


def fetch_env_live_state(environment: models.Environment) -> EnvLiveState:
    """Assume the env's cross-account role and read actual ECS service + CFN stack state."""
    aws_account = environment.aws_account
    try:
        session = iam_utils.get_assumed_role_session(
            access_key=settings.HUMR_AWS_ACCESS_KEY,
            secret_key=settings.HUMR_AWS_SECRET_KEY,
            account_id=aws_account.aws_account_id,
            external_id=str(aws_account.external_id),
            region=environment.aws_region,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Fleet: failed to assume role for env %s", environment.slug)
        return EnvLiveState(error=f"Could not assume role in account {aws_account.aws_account_id}: {exc}")

    state = EnvLiveState()
    cluster = environment.cluster_arn or f"humr-{environment.slug}-cluster"
    ecs_client = session.client("ecs", config=_AWS_CLIENT_CONFIG)
    try:
        state.services = _fetch_ecs_services(ecs_client=ecs_client, cluster=cluster)
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Fleet: ECS fetch failed for env %s", environment.slug)
        state.error = f"ECS query failed: {exc}"
        return state

    cf_client = session.client("cloudformation", config=_AWS_CLIENT_CONFIG)
    try:
        state.unhealthy_stacks = _fetch_unhealthy_stacks(cf_client=cf_client, env_slug=environment.slug)
    except (ClientError, BotoCoreError) as exc:
        logger.exception("Fleet: CFN fetch failed for env %s", environment.slug)
        state.error = f"CloudFormation query failed: {exc}"

    return state


def _fetch_ecs_services(ecs_client, cluster: str) -> list[ServiceState]:
    """List every service in the cluster with counts, rollout state, and recent task failures."""
    service_arns: list[str] = []
    paginator = ecs_client.get_paginator("list_services")
    for page in paginator.paginate(cluster=cluster):
        service_arns.extend(page["serviceArns"])

    services: list[ServiceState] = []
    for chunk_start in range(0, len(service_arns), 10):
        described = ecs_client.describe_services(cluster=cluster, services=service_arns[chunk_start:chunk_start + 10])
        for svc in described["services"]:
            rollout_state = ""
            for svc_deployment in svc.get("deployments", []):
                if svc_deployment.get("status") == "PRIMARY":
                    rollout_state = svc_deployment.get("rolloutState", "")
            service = ServiceState(
                service_name=svc["serviceName"],
                status=svc["status"],
                desired_count=svc["desiredCount"],
                running_count=svc["runningCount"],
                pending_count=svc["pendingCount"],
                rollout_state=rollout_state,
            )
            if service.running_count < service.desired_count or rollout_state == "FAILED":
                service.failure_reasons = _fetch_recent_task_failures(ecs_client=ecs_client, cluster=cluster, service_name=service.service_name)
            services.append(service)

    services.sort(key=lambda s: (s.running_count >= s.desired_count, s.service_name))
    return services


def _fetch_recent_task_failures(ecs_client, cluster: str, service_name: str) -> list[str]:
    """Return stop reasons for recently stopped tasks of a struggling service."""
    stopped = ecs_client.list_tasks(cluster=cluster, serviceName=service_name, desiredStatus="STOPPED")
    if not stopped["taskArns"]:
        return []

    reasons: list[str] = []
    details = ecs_client.describe_tasks(cluster=cluster, tasks=stopped["taskArns"][:5])
    for task in details["tasks"]:
        reason = task.get("stoppedReason", "")
        if reason:
            reasons.append(reason)
        for container in task.get("containers", []):
            if container.get("reason"):
                reasons.append(f"Container '{container['name']}': {container['reason']}")
    # Deduplicate while preserving order — crash-loops repeat the same reason.
    return list(dict.fromkeys(reasons))


def _fetch_unhealthy_stacks(cf_client, env_slug: str) -> list[StackState]:
    """Return this env's CFN stacks that are in-progress, failed, or otherwise not settled."""
    prefix = f"humr-{env_slug}-"
    unhealthy: list[StackState] = []
    paginator = cf_client.get_paginator("describe_stacks")
    for page in paginator.paginate():
        for stack in page["Stacks"]:
            if not stack["StackName"].startswith(prefix):
                continue
            if stack["StackStatus"] in _CFN_HEALTHY_STATUSES:
                continue
            unhealthy.append(StackState(
                stack_name=stack["StackName"],
                stack_status=stack["StackStatus"],
                status_reason=stack.get("StackStatusReason", ""),
            ))
    return unhealthy
