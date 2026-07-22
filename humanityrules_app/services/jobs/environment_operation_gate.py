"""Coordinate environment lifecycle transitions with app-scoped work."""

from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from humanityrules_app import models

from . import app_job_service


TEARDOWNABLE_ENVIRONMENT_STATUSES = (
    models.Environment.Status.READY,
    models.Environment.Status.ERROR,
)

FORCE_TEARDOWNABLE_ENVIRONMENT_STATUSES = (
    models.Environment.Status.PENDING,
    models.Environment.Status.PROVISIONING,
    *TEARDOWNABLE_ENVIRONMENT_STATUSES,
)

# Deploy/teardown work currently executing in a worker thread.
EXECUTING_DEPLOY_JOB_STATUSES = (
    models.App.JobStatus.DEPLOYING,
    models.App.JobStatus.TEARING_DOWN,
)

EXECUTING_PERMISSION_STATUSES = (
    models.AppPermissionRequest.Status.APPLYING,
)

REASON_ACTIVE_APP_OPERATIONS = "active_app_operations"
REASON_ACTIVE_APP_REMOVAL = "active_app_removal"
REASON_ALREADY_PENDING = "already_pending"
REASON_ALREADY_RUNNING = "already_running"
REASON_NOT_TEARDOWNABLE = "not_teardownable"

FORCED_FAILURE_MESSAGE = "Force-failed by environment teardown"


@dataclass(frozen=True)
class EnvironmentTeardownQueueResult:
    """Result of attempting the environment lifecycle transition."""

    queued: bool
    previous_status: str
    reason: str


def transition_environment_status(environment_id: UUID, expected_statuses: tuple[str, ...], new_status: str, status_message: str) -> bool:
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


async def atransition_environment_status(
    environment_id: UUID,
    expected_statuses: tuple[str, ...],
    new_status: str,
    status_message: str,
) -> bool:
    """Async form of the conditional environment status transition."""
    updated = await models.Environment.objects.filter(
        id=environment_id,
        status__in=expected_statuses,
    ).aupdate(
        status=new_status,
        status_message=status_message,
        updated_at=timezone.now(),
    )
    return updated == 1


def lock_app_environment_for_removal(app_id: UUID) -> models.Environment:
    """Lock the app's environment so a concurrent teardown claim cannot interleave with app cleanup."""
    return models.Environment.objects.select_for_update().get(apps__id=app_id)


def _has_executing_deployment_or_permission(environment_id: UUID) -> bool:
    """Return whether ordinary teardown must wait for executing environment work."""
    if models.App.objects.filter(
        environment_id=environment_id,
        job_status__in=EXECUTING_DEPLOY_JOB_STATUSES,
    ).exists():
        return True
    return models.AppPermissionRequest.objects.filter(
        app__environment_id=environment_id,
        status__in=EXECUTING_PERMISSION_STATUSES,
    ).exists()


def _has_running_app_removal(environment_id: UUID) -> bool:
    """Return whether app cleanup is currently running in the environment."""
    return models.App.objects.filter(
        environment_id=environment_id,
        job_status=models.App.JobStatus.REMOVING,
    ).exists()


def _fail_executing_deployments_and_permissions(environment_id: UUID) -> None:
    """Conclude force-abandoned work before queuing environment teardown."""
    for app in models.App.objects.filter(environment_id=environment_id, job_status__in=EXECUTING_DEPLOY_JOB_STATUSES):
        app_job_service.settle_failure(app=app, error=FORCED_FAILURE_MESSAGE)
    models.AppPermissionRequest.objects.filter(
        app__environment_id=environment_id,
        status__in=EXECUTING_PERMISSION_STATUSES,
    ).update(
        status=models.AppPermissionRequest.Status.FAILED,
        status_message=FORCED_FAILURE_MESSAGE,
        updated_at=timezone.now(),
    )


def queue_environment_teardown(environment_id: UUID, status_message: str, force: bool) -> EnvironmentTeardownQueueResult:
    """Atomically queue teardown without racing environment-scoped worker claims."""
    with transaction.atomic():
        environment = models.Environment.objects.select_for_update().get(id=environment_id)
        if environment.status == models.Environment.Status.TEARDOWN_PENDING:
            return EnvironmentTeardownQueueResult(
                queued=False,
                previous_status=environment.status,
                reason=REASON_ALREADY_PENDING,
            )
        if environment.status == models.Environment.Status.TEARING_DOWN:
            return EnvironmentTeardownQueueResult(
                queued=False,
                previous_status=environment.status,
                reason=REASON_ALREADY_RUNNING,
            )

        allowed_statuses = FORCE_TEARDOWNABLE_ENVIRONMENT_STATUSES if force else TEARDOWNABLE_ENVIRONMENT_STATUSES
        if environment.status not in allowed_statuses:
            return EnvironmentTeardownQueueResult(
                queued=False,
                previous_status=environment.status,
                reason=REASON_NOT_TEARDOWNABLE,
            )

        if _has_running_app_removal(environment_id=environment.id):
            return EnvironmentTeardownQueueResult(
                queued=False,
                previous_status=environment.status,
                reason=REASON_ACTIVE_APP_REMOVAL,
            )

        has_executing_work = _has_executing_deployment_or_permission(environment_id=environment.id)
        if has_executing_work and not force:
            return EnvironmentTeardownQueueResult(
                queued=False,
                previous_status=environment.status,
                reason=REASON_ACTIVE_APP_OPERATIONS,
            )
        if force:
            _fail_executing_deployments_and_permissions(environment_id=environment.id)

        previous_status = environment.status
        environment.status = models.Environment.Status.TEARDOWN_PENDING
        environment.status_message = status_message
        environment.save(update_fields=["status", "status_message", "updated_at"])

    return EnvironmentTeardownQueueResult(
        queued=True,
        previous_status=previous_status,
        reason="",
    )
