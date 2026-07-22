"""Simulated app deployment flow for local debug mode."""

import logging
import time

import humanityrules_app.models as models

from . import app_job_service

logger = logging.getLogger(__name__)

DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS = 5


def _build_debug_service_url(app: models.App) -> str:
    """Build a deterministic URL for simulated deployments."""
    hosted_zone = app.environment.shared_alb_hosted_zone
    if hosted_zone:
        return f"https://{app.slug}.{hosted_zone}"
    return f"http://{app.slug}.localhost"


def _build_debug_alb_dns(app: models.App) -> str:
    """Build a deterministic ALB hostname for simulated deployments."""
    return f"{app.slug}-{app.environment.slug}.debug-alb.local"


def run_debug_deployment(app: models.App) -> bool:
    """Simulate a successful deployment without cloning or touching AWS."""
    logger.info(
        "Debug deployment mode enabled for '%(app_slug)s'; skipping repository clone and AWS calls",
        {"app_slug": app.slug},
    )
    time.sleep(DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS)

    app_job_service.settle_deploy_success(
        app=app,
        service_url=_build_debug_service_url(app=app),
        alb_dns=_build_debug_alb_dns(app=app),
    )

    logger.info(
        "Debug deployment of '%(app_slug)s' completed successfully at %(service_url)s",
        {"app_slug": app.slug, "service_url": app.service_url},
    )
    return True
