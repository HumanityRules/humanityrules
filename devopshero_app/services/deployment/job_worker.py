"""
Job worker.

Polls for pending jobs (deployments and environment provisioning) and
spawns threads to execute them. This provides a simple, in-process
job execution mechanism for background tasks.
"""

import logging
import threading
import time

from django.db import transaction

from devopshero_app.models import Deployment, Environment

from . import deployment_executor
from . import environment_executor

logger = logging.getLogger(__name__)

# Global flag to stop the worker
_stop_flag = threading.Event()

# Track the worker thread
_worker_thread: threading.Thread | None = None


def _claim_pending_deployment() -> Deployment | None:
    """
    Atomically claim a pending deployment.

    Uses select_for_update with skip_locked to prevent multiple workers
    from claiming the same deployment.

    Returns:
        The claimed Deployment, or None if no pending deployments.
    """
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
            # Claim it by updating status
            deployment.status = Deployment.Status.BUILDING
            deployment.status_message = "Claimed by worker"
            deployment.save(update_fields=["status", "status_message", "updated_at"])
            logger.info(f"Claimed deployment {deployment.id} for app '{deployment.app.name}'")
            return deployment

    return None


def _claim_pending_environment() -> Environment | None:
    """
    Atomically claim a pending environment for provisioning.

    Uses select_for_update with skip_locked to prevent multiple workers
    from claiming the same environment.

    Returns:
        The claimed Environment, or None if no pending environments.
    """
    with transaction.atomic():
        environment = (
            Environment.objects
            .select_for_update(skip_locked=True)
            .filter(status=Environment.Status.PENDING)
            .select_related("aws_account")
            .first()
        )

        if environment:
            # Claim it by updating status
            environment.status = Environment.Status.PROVISIONING
            environment.status_message = "Claimed by worker"
            environment.save(update_fields=["status", "status_message", "updated_at"])
            logger.info(f"Claimed environment {environment.id} '{environment.name}'")
            return environment

    return None


def _run_deployment_thread(deployment_id: str) -> None:
    """Thread target that runs a single deployment."""
    try:
        deployment_executor.run_deployment(deployment_id)
    except Exception:
        logger.exception(f"Unhandled error in deployment {deployment_id}")


def _run_environment_thread(environment_id: str) -> None:
    """Thread target that runs a single environment provisioning."""
    try:
        environment_executor.run_provisioning(environment_id)
    except Exception:
        logger.exception(f"Unhandled error in environment provisioning {environment_id}")


def _worker_loop() -> None:
    """Main worker loop that polls for pending jobs."""
    logger.info("Job worker started")

    while not _stop_flag.is_set():
        try:
            # Check for pending deployments
            deployment = _claim_pending_deployment()
            if deployment:
                thread = threading.Thread(
                    target=_run_deployment_thread,
                    args=(str(deployment.id),),
                    name=f"deployment-{deployment.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.debug(f"Spawned thread for deployment {deployment.id}")

            # Check for pending environments
            environment = _claim_pending_environment()
            if environment:
                thread = threading.Thread(
                    target=_run_environment_thread,
                    args=(str(environment.id),),
                    name=f"environment-{environment.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.debug(f"Spawned thread for environment {environment.id}")

        except Exception:
            logger.exception("Error in worker loop")

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
