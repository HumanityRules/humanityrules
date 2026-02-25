"""
Job worker.

Polls for pending jobs (deployments, environment provisioning, teardowns,
permissions applies) and spawns threads to execute them. This provides a simple, in-process
job execution mechanism for background tasks.
"""

import logging
import threading
import time

from django.db import connections, transaction

from devopshero_app.models import AppPermissionRequest, Deployment, Environment

from . import app_deployment_executor
from . import app_deployment_teardown_executor
from . import environment_provisioning_executor
from . import environment_teardown_executor
from . import permissions_apply_executor

logger = logging.getLogger(__name__)

# Global flag to stop the worker
_stop_flag = threading.Event()

# Track the worker thread
_worker_thread: threading.Thread | None = None


def _claim_pending_app_deployment() -> Deployment | None:
    """Atomically claim a pending app deployment."""
    with transaction.atomic():
        deployment = (
            Deployment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Deployment.Status.PENDING)
            .select_related(
                "app",
                "app__workspace",
                "environment",
            )
            .first()
        )

        if deployment:
            deployment.status = Deployment.Status.BUILDING
            deployment.status_message = "Claimed by worker"
            deployment.save(update_fields=["status", "status_message", "updated_at"])
            logger.info(f"Claimed app deployment {deployment.id} for app '{deployment.app.name}'")
            return deployment

    return None


def _claim_pending_environment_provisioning() -> Environment | None:
    """Atomically claim a pending environment for provisioning."""
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
            environment.save(update_fields=["status", "status_message", "updated_at"])
            logger.info(f"Claimed environment provisioning {environment.id} '{environment.name}'")
            return environment

    return None


def _claim_pending_app_deployment_teardown() -> Deployment | None:
    """Atomically claim a pending app deployment teardown."""
    with transaction.atomic():
        deployment = (
            Deployment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Deployment.Status.TEARDOWN_PENDING)
            .select_related(
                "app",
                "app__workspace",
                "environment",
            )
            .first()
        )

        if deployment:
            deployment.status = Deployment.Status.TEARING_DOWN
            deployment.status_message = "Claimed by worker"
            deployment.save(update_fields=["status", "status_message", "updated_at"])
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


def _run_environment_provisioning_thread(environment_id: str) -> None:
    """Thread target that runs a single environment provisioning."""
    try:
        environment_provisioning_executor.run_provisioning(environment_id)
    except Exception:
        logger.exception(f"Unhandled error in environment provisioning {environment_id}")
    finally:
        connections.close_all()


def _run_app_deployment_teardown_thread(deployment_id: str) -> None:
    """Thread target that runs a single app deployment teardown."""
    try:
        app_deployment_teardown_executor.run_teardown(deployment_id)
    except Exception:
        logger.exception(f"Unhandled error in app deployment teardown {deployment_id}")
    finally:
        connections.close_all()


def _claim_pending_permissions_apply() -> AppPermissionRequest | None:
    """Atomically claim a pending permissions apply."""
    with transaction.atomic():
        apr = (
            AppPermissionRequest.objects
            .select_for_update(skip_locked=True)
            .filter(status=AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
            .select_related("app", "environment")
            .first()
        )

        if apr:
            apr.status = AppPermissionRequest.Status.APPLYING
            apr.status_message = "Claimed by worker"
            apr.save(update_fields=["status", "status_message", "updated_at"])
            logger.info(f"Claimed permissions apply {apr.id} for app '{apr.app.name}'")
            return apr

    return None


def _claim_pending_environment_teardown() -> Environment | None:
    """Atomically claim a pending environment teardown."""
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
            environment.save(update_fields=["status", "status_message", "updated_at"])
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


def _run_permissions_apply_thread(app_permission_request_id: str) -> None:
    """Thread target that runs a single permissions apply."""
    try:
        permissions_apply_executor.run_apply(app_permission_request_id)
    except Exception:
        logger.exception(f"Unhandled error in permissions apply {app_permission_request_id}")
    finally:
        connections.close_all()


def _worker_loop() -> None:
    """Main worker loop that polls for pending jobs."""
    logger.info("Job worker started")

    while not _stop_flag.is_set():
        try:
            # Check for pending app deployments
            app_deployment = _claim_pending_app_deployment()
            if app_deployment:
                thread = threading.Thread(
                    target=_run_app_deployment_thread,
                    args=(str(app_deployment.id),),
                    name=f"app-deploy-{app_deployment.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for app deployment {app_deployment.id}")

            # Check for pending environment provisioning
            env_provisioning = _claim_pending_environment_provisioning()
            if env_provisioning:
                thread = threading.Thread(
                    target=_run_environment_provisioning_thread,
                    args=(str(env_provisioning.id),),
                    name=f"env-provision-{env_provisioning.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for environment provisioning {env_provisioning.id}")

            # Check for pending app deployment teardowns
            app_deployment_teardown = _claim_pending_app_deployment_teardown()
            if app_deployment_teardown:
                thread = threading.Thread(
                    target=_run_app_deployment_teardown_thread,
                    args=(str(app_deployment_teardown.id),),
                    name=f"app-deploy-teardown-{app_deployment_teardown.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for app deployment teardown {app_deployment_teardown.id}")

            # Check for pending environment teardowns
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

            # Check for pending permissions applies
            permissions_apply = _claim_pending_permissions_apply()
            if permissions_apply:
                thread = threading.Thread(
                    target=_run_permissions_apply_thread,
                    args=(str(permissions_apply.id),),
                    name=f"permissions-apply-{permissions_apply.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.info(f"Spawned thread for permissions apply {permissions_apply.id}")

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


def start_worker() -> None:
    """
    Start the job worker in a background thread.

    This is safe to call multiple times - only one worker will run.
    Call this from AppConfig.ready() or a management command.
    """
    global _worker_thread

    if _worker_thread is not None and _worker_thread.is_alive():
        logger.error("Job worker already running")
        return

    _stop_flag.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        name="job-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Job worker thread started")


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
