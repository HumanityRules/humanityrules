"""
Deployment worker.

Polls for pending deployments and spawns threads to execute them.
This provides a simple, in-process deployment execution mechanism.
"""

import logging
import threading
import time

from django.db import transaction

from devopshero_app.models import Deployment

from . import deployment_executor

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


def _run_deployment_thread(deployment_id: str) -> None:
    """Thread target that runs a single deployment."""
    try:
        deployment_executor.run_deployment(deployment_id)
    except Exception:
        logger.exception(f"Unhandled error in deployment {deployment_id}")


def _worker_loop() -> None:
    """Main worker loop that polls for pending deployments."""
    logger.info("Deployment worker started")

    while not _stop_flag.is_set():
        try:
            deployment = _claim_pending_deployment()

            if deployment:
                # Spawn a thread for this deployment
                thread = threading.Thread(
                    target=_run_deployment_thread,
                    args=(str(deployment.id),),
                    name=f"deployment-{deployment.id.hex[:8]}",
                    daemon=True,
                )
                thread.start()
                logger.debug(f"Spawned thread for deployment {deployment.id}")

        except Exception:
            logger.exception("Error in worker loop")

        # Sleep before next poll
        _stop_flag.wait(timeout=1.0)

    logger.info("Deployment worker stopped")


def start_worker() -> None:
    """
    Start the deployment worker in a background thread.

    This is safe to call multiple times - only one worker will run.
    Call this from AppConfig.ready() or a management command.
    """
    global _worker_thread

    if _worker_thread is not None and _worker_thread.is_alive():
        logger.error("Deployment worker already running")
        return

    _stop_flag.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop,
        name="deployment-worker",
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Deployment worker thread started")


def stop_worker() -> None:
    """
    Stop the deployment worker.

    This signals the worker to stop after the current poll cycle.
    Running deployments will continue to completion.
    """
    global _worker_thread

    if _worker_thread is None or not _worker_thread.is_alive():
        logger.error("Deployment worker not running")
        return

    logger.info("Stopping deployment worker...")
    _stop_flag.set()
    _worker_thread.join(timeout=5.0)

    if _worker_thread.is_alive():
        logger.error("Deployment worker did not stop cleanly")
    else:
        logger.info("Deployment worker stopped")

    _worker_thread = None


def is_running() -> bool:
    """Check if the deployment worker is running."""
    return _worker_thread is not None and _worker_thread.is_alive()
