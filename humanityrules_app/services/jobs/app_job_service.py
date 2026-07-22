"""App job attempt lifecycle: App.job_status transitions plus their DeploymentRecord events.

Every queue/settle transition on an app goes through here so the mutable runtime
truth (App fields) and the append-only audit trail (DeploymentRecord) stay in
step. Callers hold whatever App lock their flow requires; these helpers only
write.
"""

import uuid

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


def _open_attempt(app: models.App, job_status: str) -> None:
    """Assign a fresh attempt id and move the app into `job_status`."""
    app.job_status = job_status
    app.last_attempt_id = uuid.uuid7()
    app.last_attempt_error = ""
    app.save(update_fields=["job_status", "last_attempt_id", "last_attempt_error", "updated_at"])


def queue_deploy(app: models.App, created_by: models.User | None) -> None:
    """Open a deploy attempt on an idle app: DEPLOY_PENDING + started event."""
    _open_attempt(app=app, job_status=models.App.JobStatus.DEPLOY_PENDING)
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
        git_ref=app.repository.default_branch,
        created_by=created_by,
    )


async def aqueue_deploy(app: models.App, created_by: models.User | None) -> None:
    """Async form of queue_deploy."""
    app.job_status = models.App.JobStatus.DEPLOY_PENDING
    app.last_attempt_id = uuid.uuid7()
    app.last_attempt_error = ""
    await app.asave(update_fields=["job_status", "last_attempt_id", "last_attempt_error", "updated_at"])
    await models.DeploymentRecord.objects.acreate(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
        git_ref=app.repository.default_branch,
        created_by=created_by,
    )


def queue_teardown(app: models.App, created_by: models.User | None) -> None:
    """Open a teardown attempt on an idle app: TEARDOWN_PENDING + started event."""
    _open_attempt(app=app, job_status=models.App.JobStatus.TEARDOWN_PENDING)
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.TEARDOWN_STARTED,
        created_by=created_by,
    )


def start_inline_teardown(app: models.App, created_by: models.User | None) -> None:
    """Open a teardown attempt directly in TEARING_DOWN, for executors that run it synchronously."""
    _open_attempt(app=app, job_status=models.App.JobStatus.TEARING_DOWN)
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.TEARDOWN_STARTED,
        created_by=created_by,
    )


def queue_removal(app: models.App, created_by: models.User | None, delete_all_data: bool, teardown_first: bool) -> None:
    """Open a removal attempt on an idle app: REMOVAL_PENDING + started event."""
    app.removal_delete_all_data = delete_all_data
    app.removal_teardown_first = teardown_first
    app.save(update_fields=["removal_delete_all_data", "removal_teardown_first", "updated_at"])
    _open_attempt(app=app, job_status=models.App.JobStatus.REMOVAL_PENDING)
    models.DeploymentRecord.objects.create(
        app=app,
        attempt_id=app.last_attempt_id,
        event_type=models.DeploymentRecord.EventType.REMOVAL_STARTED,
        created_by=created_by,
    )


def settle_deploy_success(app: models.App, service_url: str, alb_dns: str) -> None:
    """Conclude a deploy attempt as succeeded: live outputs + IDLE + event."""
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
