"""Platform-wide fleet status, redeploy, and recovery operations."""

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from django.db import transaction
from django.db.models import OuterRef, Subquery

from humanityrules_app import models
from humanityrules_app.services.jobs import app_job_service

RECOVERY_STATUS_MESSAGE = "Marked failed by the fleet recovery action after a control plane interruption"

# Removability is defined once, next to the admission guard it mirrors. The three
# reasons it can return are re-bound here because the redeploy predicates below share
# the same vocabulary and the fleet page renders both through fleet_service.
SKIP_APP_BUSY = app_job_service.SKIP_APP_BUSY
SKIP_APP_PENDING_REMOVAL = app_job_service.SKIP_APP_PENDING_REMOVAL
SKIP_ENVIRONMENT_NOT_READY = app_job_service.SKIP_ENVIRONMENT_NOT_READY
SKIP_FAILED_NOT_INCLUDED = "Failed not included"
SKIP_NOT_REDEPLOYABLE = "Not redeployable"


# --- Status -----------------------------------------------------------------


@dataclass
class EnvGroup:
    """One org/env group for the fleet page: the env row plus its app rows."""
    organization: models.Organization
    environment: models.Environment
    apps: list[models.App] = field(default_factory=list)


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
        app.remove_skip_reason = app_job_service.get_remove_skip_reason(app=app)
        groups[app.environment_id].apps.append(app)

    for group in groups.values():
        group.apps.sort(key=lambda a: (not a.job_in_flight, a.slug))

    return sorted(groups.values(), key=lambda g: (g.organization.slug, g.environment.slug))


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
        .select_related("environment")
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
        .select_related("environment")
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


# --- Removal ----------------------------------------------------------------


@dataclass(frozen=True)
class FleetRemoveResult:
    """Outcome of queueing one fleet row's removal."""

    app_slug: str
    queued: bool
    skip_reason: str | None


def _refresh_remove_skip_reason(app: models.App) -> str:
    """Reclassify an admission failure from current App and Environment state."""
    app.refresh_from_db()
    app.environment.refresh_from_db()
    return app_job_service.get_remove_skip_reason(app=app) or SKIP_APP_BUSY


def queue_remove(app_id: UUID, created_by: models.User) -> FleetRemoveResult:
    """Queue one fleet row for removal: infra teardown, data purge, and deletion of its App row."""
    app = (
        models.App.objects
        .select_related("environment")
        .get(id=app_id)
    )
    skip_reason = app_job_service.get_remove_skip_reason(app=app)
    if skip_reason is None:
        try:
            app_job_service.queue_removal(app=app, created_by=created_by, label=None)
        except app_job_service.AppJobAdmissionError:
            skip_reason = _refresh_remove_skip_reason(app=app)

    return FleetRemoveResult(app_slug=app.slug, queued=skip_reason is None, skip_reason=skip_reason)


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
