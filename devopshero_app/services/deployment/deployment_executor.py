"""
Deployment executor.

Orchestrates the actual deployment by calling CDK infrastructure code.
This is the main entry point called by the job worker.
"""

import logging

from django.conf import settings
from django.utils import timezone

from devopshero_app import models
from devopshero_app.services import infra_customer
from devopshero_app.services.gitproviders import repo_service

from . import app_config_builder
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


def run_deployment(deployment_id: str) -> bool:
    """
    Execute a deployment.

    This is the main entry point called by the job worker.
    It orchestrates the full deployment flow:
    1. Load deployment and related models
    2. Verify environment is READY (agent must create it first)
    3. Update status to BUILDING
    4. Build AppConfig from Django models
    5. Execute CDK deployment
    6. Update deployment status and outputs

    Args:
        deployment_id: UUID of the Deployment to execute.

    Returns:
        True if deployment succeeded, False otherwise.
    """
    try:
        deployment = models.Deployment.objects.select_related(
            "app",
            "app__workspace",
            "app__repository",
            "app__datastore",
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
            "Starting deployment %(deployment_id)s for app '%(app_name)s' to environment '%(environment_name)s'",
            {"deployment_id": str(deployment_id), "app_name": app.name, "environment_name": environment.name},
        )

        # Verify environment is READY (agent must create it via create_environment tool)
        if environment.status != models.Environment.Status.READY:
            error_msg = f"Environment '{environment.name}' is not ready (status: {environment.status}). Use create_environment to provision it first."
            logger.error(error_msg)
            deployment.status = models.Deployment.Status.FAILED
            deployment.status_message = error_msg
            deployment.completed_at = timezone.now()
            deployment.save()
            return False

        # Update status to BUILDING
        deployment.status = models.Deployment.Status.BUILDING
        deployment.status_message = "Deployment started"
        deployment.started_at = timezone.now()
        deployment.save()

        logger.info(
            "Starting deployment of '%(app_name)s' to '%(environment_name)s' (git_ref=%(git_ref)s, image_tag=%(image_tag)s)",
            {"app_name": app.name, "environment_name": environment.name, "git_ref": deployment.git_ref, "image_tag": deployment.image_tag},
        )

        # Clone the repository
        repo_path = repo_service.clone_repository(
            repository=app.repository,
            branch=deployment.git_ref,
            clone_id=str(deployment_id),
        )

        try:
            # Get AWS session
            session = _get_aws_session(deployment)

            # Build AppConfig with cloned repo path
            app_config = app_config_builder.build_app_config(
                app=app,
                environment=environment,
                repo_path=repo_path,
            )

            # Execute deployment
            deployment.status = models.Deployment.Status.DEPLOYING
            deployment.save()

            success = infra_customer.deploy_app.deploy(
                session=session,
                account_id=environment.aws_account.aws_account_id,
                region=environment.aws_region,
                app_config=app_config,
                image_tag=deployment.image_tag,
                env_slug=environment.slug,
                synth_only=False,
                shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
            )

            if success:
                deployment.status = models.Deployment.Status.RUNNING
                deployment.status_message = "Deployment completed successfully"
                deployment.completed_at = timezone.now()
                # TODO: Extract service_url and alb_dns from CDK outputs
                deployment.save()

                logger.info("Deployment completed successfully")
                logger.info("Deployment %(deployment_id)s completed successfully", {"deployment_id": str(deployment_id)})
                return True

            deployment.status = models.Deployment.Status.FAILED
            deployment.status_message = "CDK deployment failed"
            deployment.completed_at = timezone.now()
            deployment.save()

            logger.error("Deployment failed")
            logger.error("Deployment %(deployment_id)s failed", {"deployment_id": str(deployment_id)})
            return False

        except Exception as e:
            logger.exception("Deployment error: %(error)s", {"error": str(e)})

            deployment.status = models.Deployment.Status.FAILED
            deployment.status_message = f"Deployment error: {e}"
            deployment.completed_at = timezone.now()
            deployment.save()
            return False

        finally:
            # Always cleanup the cloned repository
            repo_service.cleanup_repository(repo_path=repo_path)
