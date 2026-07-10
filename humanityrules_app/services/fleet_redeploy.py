"""Fleet-wide app redeploy planning and queueing."""

from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.db.models import OuterRef, QuerySet, Subquery
from django.utils import timezone

from humanityrules_app import models


SKIP_APP_BUSY = "Deployment already in progress"
SKIP_APP_PENDING_REMOVAL = "App pending removal"
SKIP_ENVIRONMENT_NOT_READY = "Environment not ready"
SKIP_FAILED_NOT_INCLUDED = "Failed not included"
SKIP_NOT_REDEPLOYABLE = "Not redeployable"
SKIP_TORN_DOWN = "Torn down"


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


def _collect_candidates() -> _FleetRedeployCandidates:
    """Classify current fleet rows without mutating them."""
    apps_with_in_progress_deployments = set(
        models.Deployment.objects
        .filter(status__in=models.Deployment.IN_PROGRESS_STATUSES)
        .values_list("app_id", flat=True)
    )
    succeeded: list[models.Deployment] = []
    failed: list[models.Deployment] = []
    skipped_counts: Counter[str] = Counter()

    for source in _latest_deployment_per_app_environment():
        if source.app.status == models.App.Status.PENDING_REMOVAL:
            skipped_counts[SKIP_APP_PENDING_REMOVAL] += 1
        elif source.app_id in apps_with_in_progress_deployments:
            skipped_counts[SKIP_APP_BUSY] += 1
        elif source.status == models.Deployment.Status.TORN_DOWN:
            skipped_counts[SKIP_TORN_DOWN] += 1
        elif source.status not in (models.Deployment.Status.SUCCEEDED, models.Deployment.Status.FAILED):
            skipped_counts[SKIP_NOT_REDEPLOYABLE] += 1
        elif source.environment.status != models.Environment.Status.READY:
            skipped_counts[SKIP_ENVIRONMENT_NOT_READY] += 1
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


def _queue_source(source: models.Deployment, created_by: models.User) -> None:
    """Clone a source deployment into the normal pending deployment queue."""
    models.Deployment.objects.create(
        blueprint_id=source.blueprint_id,
        app=source.app,
        environment=source.environment,
        subdomain=source.subdomain,
        git_ref=source.git_ref or source.app.branch,
        image_tag=_build_image_tag(source=source),
        status=models.Deployment.Status.PENDING,
        status_message="Fleet redeploy all triggered via web UI",
        created_by=created_by,
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
            _queue_source(source=source, created_by=created_by)

    return FleetRedeployResult(
        queued_count=len(selected_sources),
        skipped_counts=dict(skipped_counts),
        included_failed=include_failed,
    )
