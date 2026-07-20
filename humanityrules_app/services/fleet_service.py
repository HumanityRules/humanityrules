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
from django.db.models import OuterRef, QuerySet, Subquery
from django.utils import timezone

import humanityrules_app.app_slugs as app_slugs
from humanityrules_app import models
from humanityrules_app.services.infra_customer import iam_utils

logger = logging.getLogger(__name__)

# CFN statuses that mean "settled and healthy" — anything else is worth surfacing.
_CFN_HEALTHY_STATUSES = {"CREATE_COMPLETE", "UPDATE_COMPLETE", "IMPORT_COMPLETE"}

_AWS_CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})

RECOVERY_STATUS_MESSAGE = "Marked failed by the fleet recovery action after a control plane interruption"

SKIP_APP_BUSY = "Deployment already in progress"
SKIP_APP_PENDING_REMOVAL = "App pending removal"
SKIP_ENVIRONMENT_NOT_READY = "Environment not ready"
SKIP_FAILED_NOT_INCLUDED = "Failed not included"
SKIP_INVALID_SUBDOMAIN = app_slugs.APP_HOSTNAME_LABEL_ERROR
SKIP_NEWER_DEPLOYMENT = "Newer deployment exists"
SKIP_NOT_REDEPLOYABLE = "Not redeployable"
SKIP_TORN_DOWN = "Torn down"


# --- Status -----------------------------------------------------------------


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
    latest_policy_proxy_activity = (
        models.AppEnvironmentActivity.objects
        .filter(
            organization_id=OuterRef("app__organization_id"),
            app_id=OuterRef("app_id"),
            environment_id=OuterRef("environment_id"),
        )
        .values("last_policy_proxy_activity_at")[:1]
    )
    deployments = (
        models.Deployment.objects
        .filter(id=Subquery(latest_per_app_env))
        .select_related("app", "environment")
        .annotate(
            live_service_url=Subquery(latest_succeeded_url),
            last_policy_proxy_activity_at=Subquery(latest_policy_proxy_activity),
        )
        .order_by("app__slug")
    )
    apps_with_in_progress_deployments = get_apps_with_in_progress_deployments()
    for deployment in deployments:
        deployment.redeploy_skip_reason = get_redeploy_skip_reason(
            source=deployment,
            apps_with_in_progress_deployments=apps_with_in_progress_deployments,
        )
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
    """Eligible source deployments plus exclusions discovered in one snapshot."""

    succeeded: list[models.Deployment]
    failed: list[models.Deployment]
    skipped_counts: dict[str, int]


def _latest_deployment_per_app_environment() -> QuerySet[models.Deployment]:
    """Return the newest deployment attempt for every app/environment pair."""
    latest_deployment_id = (
        models.Deployment.objects
        .filter(app_id=OuterRef("app_id"), environment_id=OuterRef("environment_id"))
        .order_by("-created_at")
        .values("id")[:1]
    )
    return (
        models.Deployment.objects
        .filter(id=Subquery(latest_deployment_id))
        .select_related("app", "environment")
        .order_by("app__organization__slug", "environment__slug", "app__slug")
    )


def get_apps_with_in_progress_deployments() -> set[UUID]:
    """Return app IDs that currently have a deployment attempt in progress."""
    return set(
        models.Deployment.objects.filter(status__in=models.Deployment.IN_PROGRESS_STATUSES).values_list("app_id", flat=True)
    )


def get_redeploy_skip_reason(source: models.Deployment, apps_with_in_progress_deployments: set[UUID]) -> str | None:
    """Return why a current fleet row cannot redeploy, or None when eligible."""
    if source.app.status == models.App.Status.PENDING_REMOVAL:
        return SKIP_APP_PENDING_REMOVAL
    if source.app_id in apps_with_in_progress_deployments:
        return SKIP_APP_BUSY
    if source.status == models.Deployment.Status.TORN_DOWN:
        return SKIP_TORN_DOWN
    if source.status not in (models.Deployment.Status.SUCCEEDED, models.Deployment.Status.FAILED):
        return SKIP_NOT_REDEPLOYABLE
    if source.environment.status != models.Environment.Status.READY:
        return SKIP_ENVIRONMENT_NOT_READY
    try:
        app_slugs.require_valid_app_hostname_label(value=source.subdomain or source.app.slug)
    except ValueError:
        return SKIP_INVALID_SUBDOMAIN
    return None


def _collect_candidates() -> _FleetRedeployCandidates:
    """Classify current fleet rows without mutating them."""
    apps_with_in_progress_deployments = get_apps_with_in_progress_deployments()
    succeeded: list[models.Deployment] = []
    failed: list[models.Deployment] = []
    skipped_counts: Counter[str] = Counter()

    for source in _latest_deployment_per_app_environment():
        skip_reason = get_redeploy_skip_reason(
            source=source,
            apps_with_in_progress_deployments=apps_with_in_progress_deployments,
        )
        if skip_reason is not None:
            skipped_counts[skip_reason] += 1
        elif source.status == models.Deployment.Status.SUCCEEDED:
            succeeded.append(source)
        else:
            failed.append(source)

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


def _lock_deployed_apps() -> None:
    """Serialize fleet redeploy submissions that target the same app rows."""
    deployed_app_ids = models.Deployment.objects.values("app_id").distinct()
    list(
        models.App.objects
        .select_for_update()
        .filter(id__in=Subquery(deployed_app_ids))
        .order_by("id")
        .values_list("id", flat=True)
    )


def _build_image_tag(source: models.Deployment) -> str:
    """Build a fresh image tag that remains distinct across app environments."""
    git_ref = source.git_ref or source.app.branch
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S%f")
    return f"{source.app.slug}-{short_ref}-{timestamp}-{source.id.hex[:8]}"


def _queue_source(source: models.Deployment, created_by: models.User, status_message: str) -> None:
    """Clone a source deployment into the normal pending deployment queue."""
    app_slugs.require_valid_app_hostname_label(value=source.subdomain or source.app.slug)
    models.Deployment.objects.create(
        blueprint_id=source.blueprint_id,
        app=source.app,
        environment=source.environment,
        subdomain=source.subdomain,
        git_ref=source.git_ref or source.app.branch,
        image_tag=_build_image_tag(source=source),
        status=models.Deployment.Status.PENDING,
        status_message=status_message,
        created_by=created_by,
    )


def queue_redeploy(source_id: UUID, created_by: models.User) -> FleetRedeployResult:
    """Queue one current fleet row when it remains eligible."""
    with transaction.atomic():
        source_identity = models.Deployment.objects.only("app_id", "environment_id").get(id=source_id)
        models.App.objects.select_for_update().get(id=source_identity.app_id)
        source = (
            models.Deployment.objects
            .filter(app_id=source_identity.app_id, environment_id=source_identity.environment_id)
            .select_related("app", "environment")
            .order_by("-created_at")
            .first()
        )
        if source is None or source.id != source_id:
            return FleetRedeployResult(
                queued_count=0,
                skipped_counts={SKIP_NEWER_DEPLOYMENT: 1},
                included_failed=False,
            )

        skip_reason = get_redeploy_skip_reason(
            source=source,
            apps_with_in_progress_deployments=get_apps_with_in_progress_deployments(),
        )
        if skip_reason is not None:
            return FleetRedeployResult(
                queued_count=0,
                skipped_counts={skip_reason: 1},
                included_failed=False,
            )

        _queue_source(
            source=source,
            created_by=created_by,
            status_message="Fleet redeploy triggered via web UI",
        )

    return FleetRedeployResult(
        queued_count=1,
        skipped_counts={},
        included_failed=source.status == models.Deployment.Status.FAILED,
    )


def queue_redeploy_all(created_by: models.User, include_failed: bool) -> FleetRedeployResult:
    """Queue eligible current fleet rows through the standard deployment worker."""
    with transaction.atomic():
        _lock_deployed_apps()
        candidates = _collect_candidates()
        selected_sources = [*candidates.succeeded]
        skipped_counts = Counter(candidates.skipped_counts)

        if include_failed:
            selected_sources.extend(candidates.failed)
        elif candidates.failed:
            skipped_counts[SKIP_FAILED_NOT_INCLUDED] += len(candidates.failed)

        for source in selected_sources:
            _queue_source(
                source=source,
                created_by=created_by,
                status_message="Fleet redeploy all triggered via web UI",
            )

    return FleetRedeployResult(
        queued_count=len(selected_sources),
        skipped_counts=dict(skipped_counts),
        included_failed=include_failed,
    )


# --- Recovery ---------------------------------------------------------------


@dataclass(frozen=True)
class FleetRecoveryResult:
    """Outcome of failing every deployment left in a transient state."""

    failed_count: int


def count_transient_deployments() -> int:
    """Return the number of deployment jobs that the recovery action would fail."""
    return models.Deployment.objects.filter(status__in=models.Deployment.TRANSIENT_STATUSES).count()


def fail_transient_deployments() -> FleetRecoveryResult:
    """Atomically mark every currently transient deployment as failed."""
    with transaction.atomic():
        deployment_ids = list(
            models.Deployment.objects
            .select_for_update()
            .filter(status__in=models.Deployment.TRANSIENT_STATUSES)
            .order_by("id")
            .values_list("id", flat=True)
        )
        if not deployment_ids:
            return FleetRecoveryResult(failed_count=0)

        completed_at = timezone.now()
        failed_count = models.Deployment.objects.filter(
            id__in=deployment_ids,
            status__in=models.Deployment.TRANSIENT_STATUSES,
        ).update(
            status=models.Deployment.Status.FAILED,
            status_message=RECOVERY_STATUS_MESSAGE,
            completed_at=completed_at,
            updated_at=completed_at,
        )

    return FleetRecoveryResult(failed_count=failed_count)
