"""Simulated app deployment flow for local debug mode."""

import logging
import time

from django.utils import timezone

import humanityrules_app.models as models

logger = logging.getLogger(__name__)

DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS = 5


def _build_debug_service_url(deployment: models.Deployment) -> str:
    """Build a deterministic URL for simulated deployments."""
    app = deployment.app
    hosted_zone = app.environment.shared_alb_hosted_zone
    if hosted_zone:
        return f"https://{app.slug}.{hosted_zone}"
    return f"http://{app.slug}.localhost"


def _build_debug_alb_dns(deployment: models.Deployment) -> str:
    """Build a deterministic ALB hostname for simulated deployments."""
    return f"{deployment.app.slug}-{deployment.app.environment.slug}.debug-alb.local"


def run_debug_deployment(deployment: models.Deployment) -> bool:
    """Simulate a successful deployment without cloning or touching AWS."""
    deployment.status = models.Deployment.Status.DEPLOYING
    deployment.status_message = "Debug deployment: simulating deployment"
    deployment.started_at = timezone.now()
    deployment.save(update_fields=["status", "status_message", "started_at", "updated_at"])

    logger.info(
        "Debug deployment mode enabled for %(deployment_id)s; skipping repository clone and AWS calls",
        {"deployment_id": str(deployment.id)},
    )
    time.sleep(DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS)

    deployment.status = models.Deployment.Status.SUCCEEDED
    deployment.status_message = "Debug deployment completed successfully"
    deployment.completed_at = timezone.now()
    deployment.service_url = _build_debug_service_url(deployment=deployment)
    deployment.alb_dns = _build_debug_alb_dns(deployment=deployment)
    deployment.save(
        update_fields=[
            "status",
            "status_message",
            "completed_at",
            "service_url",
            "alb_dns",
            "updated_at",
        ],
    )

    logger.info(
        "Debug deployment %(deployment_id)s completed successfully at %(service_url)s",
        {"deployment_id": str(deployment.id), "service_url": deployment.service_url},
    )
    return True
