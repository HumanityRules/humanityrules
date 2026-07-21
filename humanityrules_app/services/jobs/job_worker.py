"""
Job worker.

Polls for pending jobs (deployments, environment provisioning, teardowns,
permissions applies) and spawns threads to execute them. This provides a simple, in-process
job execution mechanism for background tasks.
"""

import logging
import threading
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from humanityrules_app.models import App, AppPermissionRequest, AppRemovalJob, CostRefreshJob, Deployment, Environment, JobWorkerRun

from . import app_deployment_executor
from . import app_deployment_teardown_executor
from . import app_remove_executor
from . import environment_provisioning_executor
from . import environment_operation_gate
from . import environment_teardown_executor
from . import permissions_apply_executor
from . import stale_job_reaper
from humanityrules_app.services.cost import cost_refresh

logger = logging.getLogger(__name__)

# Global flag to stop the worker
_stop_flag = threading.Event()

# Track the worker thread
_worker_thread: threading.Thread | None = None

# Bound memory-heavy CDK work within this process. Jobs remain pending until a
# slot is available instead of accumulating claimed jobs or blocked threads.
_cdk_job_slots = threading.BoundedSemaphore(value=settings.HUMR_MAX_CONCURRENT_CDK_JOBS)

# How often the unscoped worker sweeps for jobs abandoned in executing states.
_STALE_REAP_INTERVAL_SECONDS = 60.0

# How often this worker run proves it is alive. Must stay well under the
# HUMR_DEAD_WORKER_TIMEOUT_MINUTES threshold the reaper uses.
_HEARTBEAT_INTERVAL_SECONDS = 15.0

# This worker incarnation's JobWorkerRun id, stamped on claimed jobs. Assigned
# on the first heartbeat of the loop; None before the worker has ever beaten.
_worker_run_id: uuid.UUID | None = None

# Worker label. Empty string = unscoped (main worker), claims only rows whose
# App has label="". A non-empty label scopes the worker to App rows with that
# exact label and skips env-level jobs entirely (those are reserved for the
# unscoped main worker).
_worker_label: str = ""


def _beat_worker_run(label: str) -> None:
    """Create or refresh this worker run's proof-of-life row."""
    global _worker_run_id
    if _worker_run_id is None:
        _worker_run_id = uuid.uuid7()
    JobWorkerRun.objects.update_or_create(
        id=_worker_run_id,
        defaults={"label": label, "heartbeat_at": timezone.now()},
    )


def _claim_pending_app_deployment(label: str) -> Deployment | None:
    """Claim a pending deployment without overlapping work for the same app."""
    with transaction.atomic():
        apps_with_executing_deployments = Deployment.objects.filter(
            status__in=environment_operation_gate.EXECUTING_DEPLOYMENT_STATUSES,
        ).values("app_id")
        deployment = (
            Deployment.objects
            .select_for_update(skip_locked=True)
            .filter(
                status=Deployment.Status.PENDING,
                app__label=label,
                app__status=App.Status.ACTIVE,
                app__environment__status=Environment.Status.READY,
            )
            .exclude(app_id__in=apps_with_executing_deployments)
            .select_related(
                "app",
                "app__workspace",
                "app__environment",
            )
            .first()
        )

        if deployment:
            deployment.status = Deployment.Status.BUILDING
            deployment.status_message = "Claimed by worker"
            deployment.claimed_by_run_id = _worker_run_id
            deployment.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed app deployment {deployment.id} for app '{deployment.app.name}'")
            return deployment

    return None


def _claim_pending_environment_provisioning() -> Environment | None:
    """Atomically claim a pending environment for provisioning. Unscoped only."""
    with transaction.atomic():
        environment = (
            Environment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Environment.Status.PENDING)
            .select_related("aws_account")
            .first()
        )

        if environment:
            environment.status = Environment.Status.PROVISIONING
            environment.status_message = "Claimed by worker"
            environment.claimed_by_run_id = _worker_run_id
            environment.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed environment provisioning {environment.id} '{environment.name}'")
            return environment

    return None


def _claim_pending_app_deployment_teardown(label: str) -> Deployment | None:
    """Atomically claim a pending app deployment teardown whose App matches `label`."""
    with transaction.atomic():
        deployment = (
            Deployment.objects
            .select_for_update(skip_locked=True)
            .filter(
                status=Deployment.Status.TEARDOWN_PENDING,
                app__label=label,
                app__status=App.Status.ACTIVE,
                app__environment__status=Environment.Status.READY,
            )
            .select_related(
                "app",
                "app__workspace",
                "app__environment",
            )
            .first()
        )

        if deployment:
            deployment.status = Deployment.Status.TEARING_DOWN
            deployment.status_message = "Claimed by worker"
            deployment.claimed_by_run_id = _worker_run_id
            deployment.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed app deployment teardown {deployment.id} for app '{deployment.app.name}'")
            return deployment

    return None


def _run_app_deployment_thread(deployment_id: str) -> None:
    """Thread target that runs a single app deployment."""
    try:
        app_deployment_executor.run_deployment(deployment_id)
    except Exception:
        logger.exception(f"Unhandled error in app deployment {deployment_id}")
    finally:
        connections.close_all()


def _run_app_deployment_with_cdk_slot(deployment_id: str) -> None:
    """Run a claimed deployment and always return its CDK capacity slot."""
    try:
        _run_app_deployment_thread(deployment_id=deployment_id)
    finally:
        _cdk_job_slots.release()


def _start_pending_app_deployment(label: str) -> None:
    """Start one pending app deployment when CDK capacity is available."""
    if not _cdk_job_slots.acquire(blocking=False):
        return

    slot_handed_off = False
    try:
        deployment = _claim_pending_app_deployment(label=label)
        if deployment is None:
            return

        thread = threading.Thread(
            target=_run_app_deployment_with_cdk_slot,
            args=(str(deployment.id),),
            name=f"app-deploy-{deployment.id.hex[:8]}",
            daemon=True,
        )
        thread.start()
        slot_handed_off = True
        logger.info(f"Spawned thread for app deployment {deployment.id}")
    finally:
        if not slot_handed_off:
            _cdk_job_slots.release()


def _run_environment_provisioning_thread(environment_id: str) -> None:
    """Thread target that runs a single environment provisioning."""
    try:
        environment_provisioning_executor.run_provisioning(environment_id)
    except Exception:
        logger.exception(f"Unhandled error in environment provisioning {environment_id}")
    finally:
        connections.close_all()


def _run_environment_provisioning_with_cdk_slot(environment_id: str) -> None:
    """Run claimed provisioning and always return its CDK capacity slot."""
    try:
        _run_environment_provisioning_thread(environment_id=environment_id)
    finally:
        _cdk_job_slots.release()


def _start_pending_environment_provisioning() -> None:
    """Start one pending environment provisioning job when CDK capacity is available."""
    if not _cdk_job_slots.acquire(blocking=False):
        return

    slot_handed_off = False
    try:
        environment = _claim_pending_environment_provisioning()
        if environment is None:
            return

        thread = threading.Thread(
            target=_run_environment_provisioning_with_cdk_slot,
            args=(str(environment.id),),
            name=f"env-provision-{environment.id.hex[:8]}",
            daemon=True,
        )
        thread.start()
        slot_handed_off = True
        logger.info(f"Spawned thread for environment provisioning {environment.id}")
    finally:
        if not slot_handed_off:
            _cdk_job_slots.release()


def _run_app_deployment_teardown_thread(deployment_id: str) -> None:
    """Thread target that runs a single app deployment teardown."""
    try:
        app_deployment_teardown_executor.run_teardown(deployment_id)
    except Exception:
        logger.exception(f"Unhandled error in app deployment teardown {deployment_id}")
    finally:
        connections.close_all()


def _claim_pending_permissions_apply(label: str) -> AppPermissionRequest | None:
    """Claim one permission apply per app."""
    try:
        with transaction.atomic():
            applying_for_same_app = AppPermissionRequest.objects.filter(
                status=AppPermissionRequest.Status.APPLYING,
                app_id=OuterRef("app_id"),
            )
            apr = (
                AppPermissionRequest.objects
                .select_for_update(skip_locked=True, of=("self", "app"))
                .filter(
                    status=AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
                    app__label=label,
                    app__status=App.Status.ACTIVE,
                    app__environment__status=Environment.Status.READY,
                )
                .annotate(has_applying_for_app=Exists(applying_for_same_app))
                .filter(has_applying_for_app=False)
                .select_related("app", "app__environment")
                .first()
            )

            if apr:
                apr.status = AppPermissionRequest.Status.APPLYING
                apr.status_message = "Claimed by worker"
                apr.claimed_by_run_id = _worker_run_id
                apr.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
                logger.info(f"Claimed permissions apply {apr.id} for app '{apr.app.name}'")
                return apr
    except IntegrityError:
        logger.error("Permission apply claim lost a concurrent target claim")

    return None


def _claim_pending_environment_teardown() -> Environment | None:
    """Atomically claim a pending environment teardown. Unscoped only."""
    with transaction.atomic():
        executing_deployments = Deployment.objects.filter(
            app__environment_id=OuterRef("pk"),
            status__in=environment_operation_gate.EXECUTING_DEPLOYMENT_STATUSES,
        )
        executing_permission_applies = AppPermissionRequest.objects.filter(
            app__environment_id=OuterRef("pk"),
            status__in=environment_operation_gate.EXECUTING_PERMISSION_STATUSES,
        )
        active_removal_app_ids = AppRemovalJob.objects.filter(
            status__in=environment_operation_gate.ACTIVE_APP_REMOVAL_STATUSES,
        ).values("app_id_snapshot")
        environments_with_active_app_removals = App.objects.filter(
            id__in=active_removal_app_ids,
        ).values("environment_id")
        environment = (
            Environment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Environment.Status.TEARDOWN_PENDING)
            .exclude(id__in=environments_with_active_app_removals)
            .annotate(
                has_executing_deployment=Exists(executing_deployments),
                has_executing_permission_apply=Exists(executing_permission_applies),
            )
            .filter(
                has_executing_deployment=False,
                has_executing_permission_apply=False,
            )
            .select_related("aws_account")
            .first()
        )

        if environment:
            environment.status = Environment.Status.TEARING_DOWN
            environment.status_message = "Claimed by worker"
            environment.claimed_by_run_id = _worker_run_id
            environment.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed environment teardown {environment.id} '{environment.name}'")
            return environment

    return None


def _run_environment_teardown_thread(environment_id: str) -> None:
    """Thread target that runs a single environment teardown."""
    try:
        environment_teardown_executor.run_environment_teardown(environment_id)
    except Exception:
        logger.exception(f"Unhandled error in environment teardown {environment_id}")
    finally:
        connections.close_all()


def _claim_pending_app_removal(label: str) -> AppRemovalJob | None:
    """Claim one app removal after locking its App and cleanup environments.

    AppRemovalJob has no FK to App (only `app_id_snapshot`), so we filter via a
    subquery on App.id. The App row still exists at claim time — `app.delete()`
    runs at the very end of `run_removal`, long after the worker has claimed.
    """
    try:
        with transaction.atomic():
            apps_with_running_removals = AppRemovalJob.objects.filter(
                status=AppRemovalJob.Status.RUNNING,
            ).values("app_id_snapshot")
            apps_in_tearing_down_environments = App.objects.filter(
                environment__status=Environment.Status.TEARING_DOWN,
            ).values("id")
            matching_app = App.objects.filter(
                id=OuterRef("app_id_snapshot"),
                organization_id=OuterRef("organization_id"),
                label=label,
            )
            job = (
                AppRemovalJob.objects
                .select_for_update(skip_locked=True)
                .filter(status=AppRemovalJob.Status.PENDING)
                .annotate(has_matching_app=Exists(matching_app))
                .filter(has_matching_app=True)
                .exclude(app_id_snapshot__in=apps_with_running_removals)
                .exclude(app_id_snapshot__in=apps_in_tearing_down_environments)
                .first()
            )

            if job:
                app = App.objects.select_for_update().filter(
                    id=job.app_id_snapshot,
                    organization_id=job.organization_id,
                    label=label,
                ).first()
                if app is None:
                    return None
                if AppRemovalJob.objects.filter(
                    app_id_snapshot=job.app_id_snapshot,
                    status=AppRemovalJob.Status.RUNNING,
                ).exclude(id=job.id).exists():
                    return None

                environment = environment_operation_gate.lock_app_environment_for_removal(
                    app_id=job.app_id_snapshot,
                )
                if environment.status == Environment.Status.TEARING_DOWN:
                    return None

                job.status = AppRemovalJob.Status.RUNNING
                job.status_message = "Claimed by worker"
                job.claimed_by_run_id = _worker_run_id
                job.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
                logger.info(f"Claimed app removal {job.id} for app '{job.app_slug_snapshot}'")
                return job
    except IntegrityError:
        logger.error("App removal claim lost a concurrent App claim")

    return None


def _run_app_removal_thread(job_id: str) -> None:
    """Thread target that runs a single app removal."""
    try:
        app_remove_executor.run_removal(job_id)
    except Exception as e:
        logger.exception(f"Unhandled error in app removal {job_id}")
        app_remove_executor.fail_from_worker(job_id, f"Unhandled worker error: {e}")
    finally:
        connections.close_all()


def _run_permissions_apply_thread(app_permission_request_id: str) -> None:
    """Thread target that runs a single permissions apply."""
    try:
        permissions_apply_executor.run_apply(app_permission_request_id)
    except Exception:
        logger.exception(f"Unhandled error in permissions apply {app_permission_request_id}")
    finally:
        connections.close_all()


def _claim_pending_cost_refresh(label: str) -> CostRefreshJob | None:
    """Claim one pending cost refresh per App."""
    try:
        with transaction.atomic():
            apps_with_running_refreshes = CostRefreshJob.objects.filter(
                status=CostRefreshJob.Status.RUNNING,
            ).values("app_id")
            job = (
                CostRefreshJob.objects
                .select_for_update(skip_locked=True, of=("self", "app"))
                .filter(
                    status=CostRefreshJob.Status.PENDING,
                    app__label=label,
                    app__status=App.Status.ACTIVE,
                )
                .exclude(app_id__in=apps_with_running_refreshes)
                .select_related("app")
                .first()
            )

            if job:
                job.status = CostRefreshJob.Status.RUNNING
                job.status_message = "Claimed by worker"
                job.claimed_by_run_id = _worker_run_id
                job.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
                logger.info(f"Claimed cost refresh {job.id} for app '{job.app.slug}'")
                return job
    except IntegrityError:
        logger.error("Cost refresh claim lost a concurrent App claim")

    return None


def _run_cost_refresh_thread(job_id: str) -> None:
    """Thread target that runs a single cost refresh."""
    try:
        cost_refresh.run_refresh(job_id)
    except Exception:
        logger.exception(f"Unhandled error in cost refresh {job_id}")
    finally:
        connections.close_all()


def _worker_loop() -> None:
    """Main worker loop that polls for pending jobs."""
    label = _worker_label
    scope_desc = f"label={label!r}" if label else "unscoped (label='')"
    logger.info(f"Job worker started ({scope_desc})")

    last_beat_monotonic: float | None = None
    last_reap_monotonic: float | None = None

    while not _stop_flag.is_set():
        try:
            # Heartbeat first: claims stamp _worker_run_id, so the run row must
            # exist before any claim references it.
            if last_beat_monotonic is None or time.monotonic() - last_beat_monotonic >= _HEARTBEAT_INTERVAL_SECONDS:
                _beat_worker_run(label=label)
                last_beat_monotonic = time.monotonic()

            # Stale-job reaping is unscoped — only the main worker handles it.
            if not label and (last_reap_monotonic is None or time.monotonic() - last_reap_monotonic >= _STALE_REAP_INTERVAL_SECONDS):
                stale_job_reaper.reap_stale_jobs(
                    no_progress_timeout=timedelta(minutes=settings.HUMR_STALE_JOB_TIMEOUT_MINUTES),
                    dead_worker_timeout=timedelta(minutes=settings.HUMR_DEAD_WORKER_TIMEOUT_MINUTES),
                )
                last_reap_monotonic = time.monotonic()

            # Check for pending app deployments
            _start_pending_app_deployment(label=label)

            # Environment provisioning is unscoped — only the main worker handles it.
            if not label:
                _start_pending_environment_provisioning()

            # Check for pending app deployment teardowns
            app_deployment_teardown = _claim_pending_app_deployment_teardown(label=label)
            if app_deployment_teardown:
                thread = threading.Thread(
                    target=_run_app_deployment_teardown_thread,
                    args=(str(app_deployment_teardown.id),),
                    name=f"app-deploy-teardown-{app_deployment_teardown.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for app deployment teardown {app_deployment_teardown.id}")

            # Environment teardown is unscoped — only the main worker handles it.
            if not label:
                env_teardown = _claim_pending_environment_teardown()
                if env_teardown:
                    thread = threading.Thread(
                        target=_run_environment_teardown_thread,
                        args=(str(env_teardown.id),),
                        name=f"env-teardown-{env_teardown.id.hex[:8]}",
                        daemon=True,
                    )
                    thread.start()
                    logger.info(f"Spawned thread for environment teardown {env_teardown.id}")

            # Check for pending app removals
            app_removal = _claim_pending_app_removal(label=label)
            if app_removal:
                thread = threading.Thread(
                    target=_run_app_removal_thread,
                    args=(str(app_removal.id),),
                    name=f"app-remove-{app_removal.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for app removal {app_removal.id}")

            # Check for pending permissions applies
            permissions_apply = _claim_pending_permissions_apply(label=label)
            if permissions_apply:
                thread = threading.Thread(
                    target=_run_permissions_apply_thread,
                    args=(str(permissions_apply.id),),
                    name=f"permissions-apply-{permissions_apply.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for permissions apply {permissions_apply.id}")

            # Check for pending cost refreshes
            cost_refresh = _claim_pending_cost_refresh(label=label)
            if cost_refresh:
                thread = threading.Thread(
                    target=_run_cost_refresh_thread,
                    args=(str(cost_refresh.id),),
                    name=f"cost-refresh-{cost_refresh.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for cost refresh {cost_refresh.id}")

        except Exception:
            logger.exception("Error in worker loop")
        finally:
            # Return connections to the pool after each poll cycle.
            # Without this, the worker thread holds a connection for its entire
            # lifetime, reducing the pool available for web requests and agent sessions.
            connections.close_all()

        # Sleep before next poll
        _stop_flag.wait(timeout=1.0)

    logger.info("Job worker stopped")


def start_worker(label: str) -> None:
    """Start the job worker in a background thread, scoped to `label` (empty = unscoped).

    Safe to call multiple times — only one worker will run per process.
    Call this from AppConfig.ready() or a management command.
    """
    global _worker_thread, _worker_label, _worker_run_id

    if _worker_thread is not None and _worker_thread.is_alive():
        logger.error("Job worker already running")
        return

    _worker_label = label
    _worker_run_id = None
    _stop_flag.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        name="job-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info(f"Job worker thread started (label={label!r})")


def stop_worker() -> None:
    """
    Stop the job worker.

    This signals the worker to stop after the current poll cycle.
    Running jobs will continue to completion.
    """
    global _worker_thread

    if _worker_thread is None or not _worker_thread.is_alive():
        logger.error("Job worker not running")
        return

    logger.info("Stopping job worker...")
    _stop_flag.set()
    _worker_thread.join(timeout=5.0)

    if _worker_thread.is_alive():
        logger.error("Job worker did not stop cleanly")
    else:
        logger.info("Job worker stopped")

    _worker_thread = None


def is_running() -> bool:
    """Check if the job worker is running."""
    return _worker_thread is not None and _worker_thread.is_alive()
