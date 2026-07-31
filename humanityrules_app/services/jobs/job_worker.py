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

from humanityrules_app.models import App, AppPermissionRequest, CostRefreshJob, Environment, JobWorkerRun

from . import app_deployment_executor
from . import app_deployment_teardown_executor
from . import app_remove_executor
from . import environment_provisioning_executor
from . import environment_teardown_executor
from . import permissions_apply_executor
from . import stale_job_reaper
from humanityrules_app.services.billing import rating
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

# How often the unscoped worker rates metered usage into ledger charges. The
# broker's entitlement snapshot is only as fresh as this tick, so enforcement
# accuracy depends on the cadence.
_RATING_INTERVAL_SECONDS = 60.0

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


def _claim_pending_app_deployment(label: str) -> App | None:
    """Claim an app queued for deploy. One job_status per app makes overlap impossible."""
    with transaction.atomic():
        app = (
            App.objects
            .select_for_update(skip_locked=True, of=("self", "environment"))
            .filter(
                job_status=App.JobStatus.DEPLOY_PENDING,
                label=label,
                environment__status=Environment.Status.READY,
            )
            .select_related("workspace", "environment")
            .first()
        )

        if app:
            app.job_status = App.JobStatus.DEPLOYING
            app.may_have_infra = True
            app.claimed_by_run_id = _worker_run_id
            app.save(update_fields=["job_status", "may_have_infra", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed app deployment for app '{app.name}' (attempt {app.last_attempt_id})")
            return app

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
            environment.status_message = "Provisioning started"
            environment.claimed_by_run_id = _worker_run_id
            environment.save(update_fields=["status", "status_message", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed environment provisioning {environment.id} '{environment.name}'")
            return environment

    return None


def _claim_pending_app_deployment_teardown(label: str) -> App | None:
    """Atomically claim an app queued for teardown whose label matches."""
    with transaction.atomic():
        app = (
            App.objects
            .select_for_update(skip_locked=True, of=("self", "environment"))
            .filter(
                job_status=App.JobStatus.TEARDOWN_PENDING,
                label=label,
                environment__status=Environment.Status.READY,
            )
            .select_related("workspace", "environment")
            .first()
        )

        if app:
            app.job_status = App.JobStatus.TEARING_DOWN
            app.claimed_by_run_id = _worker_run_id
            app.save(update_fields=["job_status", "claimed_by_run", "updated_at"])
            logger.info(f"Claimed app deployment teardown for app '{app.name}' (attempt {app.last_attempt_id})")
            return app

    return None


def _run_app_deployment_thread(app_id: str) -> None:
    """Thread target that runs a single app deployment."""
    try:
        app_deployment_executor.run_deployment(app_id)
    except Exception:
        logger.exception(f"Unhandled error in app deployment for app {app_id}")
    finally:
        connections.close_all()


def _run_app_deployment_with_cdk_slot(app_id: str) -> None:
    """Run a claimed deployment and always return its CDK capacity slot."""
    try:
        _run_app_deployment_thread(app_id=app_id)
    finally:
        _cdk_job_slots.release()


def _start_pending_app_deployment(label: str) -> None:
    """Start one pending app deployment when CDK capacity is available."""
    if not _cdk_job_slots.acquire(blocking=False):
        return

    slot_handed_off = False
    try:
        app = _claim_pending_app_deployment(label=label)
        if app is None:
            return

        thread = threading.Thread(
            target=_run_app_deployment_with_cdk_slot,
            args=(str(app.id),),
            name=f"app-deploy-{app.id.hex[:8]}",
            daemon=True,
        )
        thread.start()
        slot_handed_off = True
        logger.info(f"Spawned thread for app deployment of '{app.slug}'")
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


def _run_app_deployment_teardown_thread(app_id: str) -> None:
    """Thread target that runs a single app deployment teardown."""
    try:
        app_deployment_teardown_executor.run_teardown(app_id)
    except Exception:
        logger.exception(f"Unhandled error in app deployment teardown for app {app_id}")
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
                .select_for_update(skip_locked=True, of=("self", "app", "app__environment"))
                .filter(
                    status=AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
                    app__label=label,
                    app__environment__status=Environment.Status.READY,
                )
                .exclude(app__job_status__in=App.REMOVAL_JOB_STATUSES)
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
    """Atomically claim an admitted environment teardown. Unscoped only."""
    with transaction.atomic():
        environment = (
            Environment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Environment.Status.TEARDOWN_PENDING)
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


def _claim_pending_app_removal(label: str) -> App | None:
    """Claim one app queued for removal after locking its environment against teardown."""
    try:
        with transaction.atomic():
            app = (
                App.objects
                .select_for_update(skip_locked=True, of=("self", "environment"))
                .filter(
                    job_status=App.JobStatus.REMOVAL_PENDING,
                    label=label,
                    environment__status=Environment.Status.READY,
                )
                .select_related("environment")
                .first()
            )

            if app:
                app.job_status = App.JobStatus.REMOVING
                app.claimed_by_run_id = _worker_run_id
                app.save(update_fields=["job_status", "claimed_by_run", "updated_at"])
                logger.info(f"Claimed app removal for app '{app.slug}'")
                return app
    except IntegrityError:
        logger.error("App removal claim lost a concurrent App claim")

    return None


def _run_app_removal_thread(app_id: str) -> None:
    """Thread target that runs a single app removal."""
    try:
        app_remove_executor.run_removal(app_id)
    except Exception as e:
        logger.exception(f"Unhandled error in app removal for app {app_id}")
        app_remove_executor.fail_from_worker(app_id, f"Unhandled worker error: {e}")
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
                )
                .exclude(app__job_status__in=App.REMOVAL_JOB_STATUSES)
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
    last_rating_monotonic: float | None = None

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

            # Usage rating is one global tick — only the main worker runs it.
            if not label and (last_rating_monotonic is None or time.monotonic() - last_rating_monotonic >= _RATING_INTERVAL_SECONDS):
                rating.rate_pending_events(now=timezone.now())
                last_rating_monotonic = time.monotonic()

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
