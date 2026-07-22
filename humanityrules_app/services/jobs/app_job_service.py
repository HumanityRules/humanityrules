"""App job attempt lifecycle: App.job_status transitions plus their DeploymentRecord events.

Every queue/settle transition on an app goes through here so the mutable runtime
truth (App fields) and the append-only audit trail (DeploymentRecord) stay in
step. Queue functions own admission and locking; executors call the settle
helpers after already owning an in-flight attempt.
"""

import uuid

from django.db import transaction
from django.utils import timezone

from humanityrules_app import models

# Which failure event concludes an attempt abandoned in a given job status.
FAILURE_EVENT_BY_JOB_STATUS = {
    models.App.JobStatus.DEPLOY_PENDING: models.DeploymentRecord.EventType.DEPLOY_FAILED,
    models.App.JobStatus.DEPLOYING: models.DeploymentRecord.EventType.DEPLOY_FAILED,
    models.App.JobStatus.TEARDOWN_PENDING: models.DeploymentRecord.EventType.TEARDOWN_FAILED,
    models.App.JobStatus.TEARING_DOWN: models.DeploymentRecord.EventType.TEARDOWN_FAILED,
    models.App.JobStatus.REMOVAL_PENDING: models.DeploymentRecord.EventType.REMOVAL_FAILED,
    models.App.JobStatus.REMOVING: models.DeploymentRecord.EventType.REMOVAL_FAILED,
}


class AppJobAdmissionError(ValueError):
    """Raised when an environment or app cannot accept a new lifecycle operation."""


def _open_attempt(app: models.App, job_status: str) -> None:
    """Assign a fresh attempt id and move the app into `job_status`."""
    app.job_status = job_status
    app.last_attempt_id = uuid.uuid7() 
    app.last_attempt_error = ""
    app.save(update_fields=["job_status", "last_attempt_id", "last_attempt_error", "updated_at"])


def _lock_app_for_admission(app: models.App) -> models.App:
    """Lock the environment lifecycle boundary before locking its app."""
    environment = models.Environment.objects.select_for_update().get(id=app.environment_id)
    if environment.status != models.Environment.Status.READY:
        raise AppJobAdmissionError(
            f"Environment '{environment.name}' is not ready for app operations (status: {environment.status})."
        )
    return (
        models.App.objects
        .select_for_update(of=("self",))
        .get(id=app.id, environment_id=environment.id)
    )


def _require_idle(app: models.App) -> None:
    """Reject a new operation while another App job owns the row."""
    if app.job_status != models.App.JobStatus.IDLE:
        raise AppJobAdmissionError(f"App '{app.slug}' has a job in progress ({app.job_status}).")


def queue_deploy(app: models.App, created_by: models.User | None) -> models.App:
    """Atomically admit and queue a deploy attempt."""
    with transaction.atomic():
        locked_app = _lock_app_for_admission(app=app)
        _require_idle(app=locked_app)
        _open_attempt(app=locked_app, job_status=models.App.JobStatus.DEPLOY_PENDING)
        models.DeploymentRecord.objects.create(
            app=locked_app,
            attempt_id=locked_app.last_attempt_id,
            event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
            created_by=created_by,
        )
    return locked_app


def queue_teardown(app: models.App, created_by: models.User | None, label: str | None) -> models.App:
    """Atomically admit and queue an App infrastructure teardown."""
    with transaction.atomic():
        locked_app = _lock_app_for_admission(app=app)
        _require_idle(app=locked_app)
        if not locked_app.may_have_infra:
            raise AppJobAdmissionError(f"App '{locked_app.slug}' has no infrastructure to tear down.")
        if label is not None:
            locked_app.label = label
            locked_app.save(update_fields=["label", "updated_at"])
        _open_attempt(app=locked_app, job_status=models.App.JobStatus.TEARDOWN_PENDING)
        models.DeploymentRecord.objects.create(
            app=locked_app,
            attempt_id=locked_app.last_attempt_id,
            event_type=models.DeploymentRecord.EventType.TEARDOWN_STARTED,
            created_by=created_by,
        )
    return locked_app


def queue_removal(
    app: models.App,
    created_by: models.User | None,
    delete_all_data: bool,
    teardown_first: bool,
    label: str | None,
) -> models.App:
    """Atomically admit and queue an App removal."""
    with transaction.atomic():
        locked_app = _lock_app_for_admission(app=app)
        _require_idle(app=locked_app)
        if locked_app.may_have_infra and not teardown_first:
            raise AppJobAdmissionError(f"App '{locked_app.slug}' may still have deployed infrastructure; tear it down first.")
        locked_app.removal_delete_all_data = delete_all_data
        locked_app.removal_teardown_first = teardown_first
        if label is not None:
            locked_app.label = label
        locked_app.save(update_fields=[
            "removal_delete_all_data", "removal_teardown_first", "label", "updated_at",
        ])
        _open_attempt(app=locked_app, job_status=models.App.JobStatus.REMOVAL_PENDING)
        models.DeploymentRecord.objects.create(
            app=locked_app,
            attempt_id=locked_app.last_attempt_id,
            event_type=models.DeploymentRecord.EventType.REMOVAL_STARTED,
            created_by=created_by,
        )
    return locked_app


def settle_deploy_success(app: models.App, service_url: str, alb_dns: str, image_hashes: dict[str, str]) -> None:
    """Conclude a deploy attempt as succeeded: live outputs + IDLE + event stamped with the deployed image versions."""
    app.job_status = models.App.JobStatus.IDLE
    app.live_state = models.App.LiveState.DEPLOYED
    app.service_url = service_url
    app.alb_dns = alb_dns
    app.last_deployed_at = timezone.now()
    app.last_attempt_error = ""
    app.save(update_fields=[
        "job_status", "live_state", "service_url", "alb_dns",
        "last_deployed_at", "last_attempt_error", "updated_at",
    ])
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.DEPLOY_SUCCEEDED,
        details={"image_hashes": image_hashes} if image_hashes else None,
    )


def settle_teardown_success(app: models.App) -> None:
    """Conclude a teardown attempt as succeeded: clear live outputs + IDLE + event."""
    app.job_status = models.App.JobStatus.IDLE
    app.live_state = models.App.LiveState.TORN_DOWN
    app.may_have_infra = False
    app.service_url = ""
    app.alb_dns = ""
    app.last_attempt_error = ""
    app.save(update_fields=[
        "job_status", "live_state", "may_have_infra", "service_url",
        "alb_dns", "last_attempt_error", "updated_at",
    ])
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.TEARDOWN_SUCCEEDED,
    )


def settle_failure(app: models.App, error: str) -> None:
    """Conclude the in-flight attempt as failed, whatever kind it is: IDLE + error + event."""
    event_type = FAILURE_EVENT_BY_JOB_STATUS.get(app.job_status)
    app.job_status = models.App.JobStatus.IDLE
    app.last_attempt_error = error
    app.save(update_fields=["job_status", "last_attempt_error", "updated_at"])
    if event_type is not None:
        models.DeploymentRecord.objects.create(
            app=app,
            attempt_id=app.last_attempt_id,
            event_type=event_type,
            error=error,
        )
