"""
App deployment teardown executor.

Orchestrates the teardown of deployed applications by calling CDK infrastructure code.
Sets the deployment status to TORN_DOWN after successful teardown.
This is the entry point called by the job worker for teardown jobs.
"""

import logging

from django.conf import settings
from django.utils import timezone

from devopshero_app import models
from devopshero_app.services import infra_customer

from . import job_logging

logger = logging.getLogger(__name__)


def _get_aws_session(deployment: models.Deployment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = deployment.environment.aws_account

    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=deployment.environment.aws_region,
    )


def _dockerfile_ecr_repo_names(deployment: models.Deployment) -> list[str]:
    """ECR repo names for the app's dockerfile-built containers, for teardown cleanup.

    Prebuilt-container repos are per-env shared resources and are not torn
    down by per-app teardown. If the source template is gone (deleted after
    the deploy), fall back to the legacy app-level repo name so we still
    empty the right one.
    """
    app = deployment.app
    env_slug = deployment.environment.slug
    template = app.source_template

    if template and template.containers:
        return [
            f"doh/{env_slug}/{app.slug}-{tc['name']}"
            for tc in template.containers
            if tc["image_source"] == "dockerfile"
        ]
    return [f"doh/{env_slug}/{app.slug}"]


def run_teardown(deployment_id: str) -> bool:
    """
    Execute a teardown.

    This is the main entry point called by the job worker.
    It orchestrates the teardown flow:
    1. Load deployment and related models
    2. Update status to TEARING_DOWN
    3. Build minimal AppConfig for stack identification
    4. Execute CDK teardown
    5. Update deployment status

    Args:
        deployment_id: UUID of the Deployment to tear down.

    Returns:
        True if teardown succeeded, False otherwise.
    """
    try:
        deployment = models.Deployment.objects.select_related(
            "blueprint",
            "blueprint__datastore",
            "app",
            "app__workspace",
            "app__source_template",
            "environment",
            "environment__aws_account",
        ).get(id=deployment_id)
    except models.Deployment.DoesNotExist:
        logger.error("Deployment %(deployment_id)s not found", {"deployment_id": deployment_id})
        return False

    environment = deployment.environment
    app = deployment.app

    with job_logging.DeploymentLogContext(
        deployment=deployment,
        source_default=models.DeploymentLog.Source.APP,
    ):
        logger.info(
            "Starting teardown %(deployment_id)s for app '%(app_name)s' in environment '%(environment_name)s'",
            {"deployment_id": str(deployment_id), "app_name": app.name, "environment_name": environment.name},
        )

        # Update status to TEARING_DOWN
        deployment.status = models.Deployment.Status.TEARING_DOWN
        deployment.status_message = "Teardown started"
        deployment.save(update_fields=["status", "status_message", "updated_at"])

        try:
            session = _get_aws_session(deployment)

            logger.info(
                "Tearing down app '%(app_name)s' stacks",
                {"app_name": app.name},
            )

            success = infra_customer.deploy_app.teardown(
                session=session,
                env_slug=environment.slug,
                app_name=app.slug,
                has_database=deployment.blueprint.datastore_id is not None,
                dockerfile_ecr_repo_names=_dockerfile_ecr_repo_names(deployment),
            )

            if success:
                deployment.status = models.Deployment.Status.TORN_DOWN
                deployment.status_message = "Teardown completed successfully"
                deployment.service_url = ""
                deployment.completed_at = timezone.now()
                deployment.save()

                logger.info("Teardown %(deployment_id)s completed successfully", {"deployment_id": str(deployment_id)})
                return True

            deployment.status = models.Deployment.Status.FAILED
            deployment.status_message = "Teardown failed - some stacks may not have been deleted"
            deployment.completed_at = timezone.now()
            deployment.save()

            logger.error("Teardown failed")
            logger.error("Teardown %(deployment_id)s failed", {"deployment_id": str(deployment_id)})
            return False

        except Exception as e:
            logger.exception("Teardown error: %(error)s", {"error": str(e)})

            deployment.status = models.Deployment.Status.FAILED
            deployment.status_message = f"Teardown error: {e}"
            deployment.completed_at = timezone.now()
            deployment.save()
            return False
