"""Platform-wide fleet operations: status snapshot, live AWS state, redeploy, and recovery.

Two status tiers: a DB snapshot (instant, intent-level) and an on-demand live fetch
that assumes the environment's cross-account role and reads actual ECS/CFN state.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Subquery

from humanityrules_app import models
from humanityrules_app.services.infra_customer import iam_utils
from humanityrules_app.services.jobs import app_job_service

logger = logging.getLogger(__name__)

# CFN statuses that mean "settled and healthy" — anything else is worth surfacing.
_CFN_HEALTHY_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}

_AWS_CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})

RECOVERY_STATUS_MESSAGE = "Marked failed by the fleet recovery action after a control plane interruption"

SKIP_APP_BUSY = "Deployment or teardown already in progress"
SKIP_APP_PENDING_REMOVAL = "App pending removal"
SKIP_ENVIRONMENT_NOT_READY = "Environment not ready"
SKIP_FAILED_NOT_INCLUDED = "Failed not included"
SKIP_NOT_REDEPLOYABLE = "Not redeployable"
SKIP_TORN_DOWN = "Torn down"


# --- Status -----------------------------------------------------------------


@dataclass
class EnvGroup:
    """One org/env group for the fleet page: the env row plus its app rows."""
    organization: models.Organization
    environment: models.Environment
    apps: list[models.App] = field(default_factory=list)


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
    """Return the DB-tier fleet view: every environment (with or without apps), grouped by owning org."""
    groups: dict = {}
    environments = models.Environment.objects.select_related("aws_account__organization")
    for environment in environments:
        groups[environment.id] = EnvGroup(organization=environment.aws_account.organization, environment=environment)

    latest_policy_proxy_activity = (
        models.AppEnvironmentActivity.objects
        .filter(
            organization_id=OuterRef("organization_id"),
            app_id=OuterRef("pk"),
        )
        .values("last_policy_proxy_activity_at")[:1]
    )
    apps = (
        models.App.objects
        .filter(last_attempt_id__isnull=False)
        .select_related("environment")
        .annotate(
            last_policy_proxy_activity_at=Subquery(latest_policy_proxy_activity),
        )
        .order_by("slug")
    )
    for app in apps:
        app.redeploy_skip_reason = get_redeploy_skip_reason(app=app)
        groups[app.environment_id].apps.append(app)

    for group in groups.values():
        group.apps.sort(key=lambda a: (not a.job_in_flight, a.slug))

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


# --- Redeploy ---------------------------------------------------------------


@dataclass(frozen=True)
class FleetRedeployPreview:
    """Counts shown before a fleet redeploy is confirmed."""

    succeeded_count: int
    failed_count: int
    skipped_counts: dict[str, int]

    @property
    def eligible_count(self) -> int:
        """Return the number available when failed deployments are included."""
        return self.succeeded_count + self.failed_count

    @property
    def skipped_count(self) -> int:
        """Return the number that cannot be queued regardless of the checkbox."""
        return sum(self.skipped_counts.values())


@dataclass(frozen=True)
class FleetRedeployResult:
    """Outcome of queueing a fleet-wide redeploy."""

    queued_count: int
    skipped_counts: dict[str, int]
    included_failed: bool

    @property
    def skipped_count(self) -> int:
        """Return the number of fleet rows skipped by the operation."""
        return sum(self.skipped_counts.values())


@dataclass(frozen=True)
class _FleetRedeployCandidates:
    """Eligible apps plus exclusions discovered in one snapshot."""

    succeeded: list[models.App]
    failed: list[models.App]
    skipped_counts: dict[str, int]


def get_redeploy_skip_reason(app: models.App) -> str | None:
    """Return why a fleet row cannot redeploy, or None when eligible."""
    if app.job_status in models.App.REMOVAL_JOB_STATUSES:
        return SKIP_APP_PENDING_REMOVAL
    if app.job_status != models.App.JobStatus.IDLE:
        return SKIP_APP_BUSY
    if app.live_state == models.App.LiveState.TORN_DOWN:
        return SKIP_TORN_DOWN
    if app.last_attempt_id is None:
        return SKIP_NOT_REDEPLOYABLE
    if app.environment.status != models.Environment.Status.READY:
        return SKIP_ENVIRONMENT_NOT_READY
    return None


def _refresh_redeploy_skip_reason(app: models.App) -> str:
    """Reclassify an admission failure from current App and Environment state."""
    app.refresh_from_db()
    app.environment.refresh_from_db()
    return get_redeploy_skip_reason(app=app) or SKIP_APP_BUSY


def _collect_candidates() -> _FleetRedeployCandidates:
    """Classify current fleet rows without mutating them."""
    succeeded: list[models.App] = []
    failed: list[models.App] = []
    skipped_counts: Counter[str] = Counter()

    apps = (
        models.App.objects
        .filter(last_attempt_id__isnull=False)
        .select_related("environment", "repository")
        .order_by("organization__slug", "environment__slug", "slug")
    )
    for app in apps:
        skip_reason = get_redeploy_skip_reason(app=app)
        if skip_reason is not None:
            skipped_counts[skip_reason] += 1
        elif app.live_state == models.App.LiveState.DEPLOYED and not app.last_attempt_error:
            succeeded.append(app)
        else:
            failed.append(app)

    return _FleetRedeployCandidates(
        succeeded=succeeded,
        failed=failed,
        skipped_counts=dict(skipped_counts),
    )


def build_redeploy_all_preview() -> FleetRedeployPreview:
    """Build counts for the fleet redeploy confirmation modal."""
    candidates = _collect_candidates()
    return FleetRedeployPreview(
        succeeded_count=len(candidates.succeeded),
        failed_count=len(candidates.failed),
        skipped_counts=candidates.skipped_counts,
    )


def queue_redeploy(app_id: UUID, created_by: models.User) -> FleetRedeployResult:
    """Queue one fleet row when it remains eligible."""
    app = (
        models.App.objects
        .select_related("environment", "repository")
        .get(id=app_id)
    )
    skip_reason = get_redeploy_skip_reason(app=app)
    if skip_reason is not None:
        return FleetRedeployResult(
            queued_count=0,
            skipped_counts={skip_reason: 1},
            included_failed=False,
        )

    included_failed = app.live_state != models.App.LiveState.DEPLOYED or bool(app.last_attempt_error)
    try:
        app_job_service.queue_deploy(app=app, created_by=created_by)
    except app_job_service.AppJobAdmissionError:
        skip_reason = _refresh_redeploy_skip_reason(app=app)
        return FleetRedeployResult(
            queued_count=0,
            skipped_counts={skip_reason: 1},
            included_failed=False,
        )

    return FleetRedeployResult(
        queued_count=1,
        skipped_counts={},
        included_failed=included_failed,
    )


def queue_redeploy_all(created_by: models.User, include_failed: bool) -> FleetRedeployResult:
    """Queue eligible current fleet rows through the standard deployment worker."""
    candidates = _collect_candidates()
    selected = [*candidates.succeeded]
    skipped_counts = Counter(candidates.skipped_counts)

    if include_failed:
        selected.extend(candidates.failed)
    elif candidates.failed:
        skipped_counts[SKIP_FAILED_NOT_INCLUDED] += len(candidates.failed)

    queued_count = 0
    for app in selected:
        try:
            app_job_service.queue_deploy(app=app, created_by=created_by)
        except app_job_service.AppJobAdmissionError:
            skipped_counts[_refresh_redeploy_skip_reason(app=app)] += 1
        else:
            queued_count += 1

    return FleetRedeployResult(
        queued_count=queued_count,
        skipped_counts=dict(skipped_counts),
        included_failed=include_failed,
    )


# --- Recovery ---------------------------------------------------------------


@dataclass(frozen=True)
class FleetRecoveryResult:
    """Outcome of failing every app job left in an unsettled state."""

    failed_count: int


def count_unsettled_deployments() -> int:
    """Return the number of app jobs that the recovery action would fail."""
    return models.App.objects.exclude(job_status=models.App.JobStatus.IDLE).count()


def fail_unsettled_deployments() -> FleetRecoveryResult:
    """Atomically fail every app job not currently IDLE."""
    with transaction.atomic():
        apps = list(
            models.App.objects
            .select_for_update()
            .exclude(job_status=models.App.JobStatus.IDLE)
            .order_by("id")
        )
        for app in apps:
            app_job_service.settle_failure(app=app, error=RECOVERY_STATUS_MESSAGE)

    return FleetRecoveryResult(failed_count=len(apps))
