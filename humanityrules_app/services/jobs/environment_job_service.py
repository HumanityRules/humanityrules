"""Environment job admission and conditional lifecycle transitions."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone

from humanityrules_app import models


class EnvironmentJobAdmissionError(ValueError):
    """Raised when an environment cannot accept a requested lifecycle operation."""


def transition_status(environment_id: UUID, expected_statuses: tuple[str, ...], new_status: str, status_message: str) -> bool:
    """Change status only while the environment remains in an expected source state."""
    updated = models.Environment.objects.filter(
        id=environment_id,
        status__in=expected_statuses,
    ).update(
        status=new_status,
        status_message=status_message,
        updated_at=timezone.now(),
    )
    return updated == 1


def queue_teardown(environment_id: UUID, status_message: str) -> str:
    """Enter TEARDOWN_PENDING only after all environment-owned work has settled."""
    with transaction.atomic():
        environment = models.Environment.objects.select_for_update().get(id=environment_id)
        if environment.status == models.Environment.Status.TEARDOWN_PENDING:
            raise EnvironmentJobAdmissionError(f"Environment '{environment.slug}' is already queued for teardown.")
        if environment.status == models.Environment.Status.TEARING_DOWN:
            raise EnvironmentJobAdmissionError(f"Environment '{environment.slug}' is already being torn down.")
        if environment.status not in (models.Environment.Status.READY, models.Environment.Status.ERROR):
            raise EnvironmentJobAdmissionError(
                f"Environment '{environment.slug}' is in '{environment.status}' state and cannot be torn down."
            )

        unsettled_app = (
            models.App.objects
            .filter(environment=environment)
            .exclude(job_status=models.App.JobStatus.IDLE)
            .order_by("created_at")
            .first()
        )
        if unsettled_app is not None:
            raise EnvironmentJobAdmissionError(
                f"App '{unsettled_app.slug}' has an unsettled job ({unsettled_app.job_status})."
            )

        applying_permission = models.AppPermissionRequest.objects.filter(
            app__environment=environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        ).select_related("app").first()
        if applying_permission is not None:
            raise EnvironmentJobAdmissionError(
                f"App '{applying_permission.app.slug}' has a permissions update in progress."
            )

        previous_status = environment.status
        environment.status = models.Environment.Status.TEARDOWN_PENDING
        environment.status_message = status_message
        environment.save(update_fields=["status", "status_message", "updated_at"])
        return previous_status
