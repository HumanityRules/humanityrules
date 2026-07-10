"""Recovery actions for deployments stranded by a control plane interruption."""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from humanityrules_app import models


RECOVERY_STATUS_MESSAGE = "Marked failed by the fleet recovery action after a control plane interruption"


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
