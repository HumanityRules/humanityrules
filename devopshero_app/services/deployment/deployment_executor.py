"""
Deployment executor.

Orchestrates the actual deployment by calling CDK infrastructure code.
This is the main entry point called by the deployment worker.
"""

import logging

from django.conf import settings
from django.utils import timezone

from devopshero_app import models
from devopshero_app.services import infra_customer

from . import app_config_builder
from . import deployment_logging

logger = logging.getLogger(__name__)


def _get_aws_session(deployment: models.Deployment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = deployment.environment.aws_account

    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=deployment.app.workspace.aws_region,
    )


def _provision_environment(deployment: models.Deployment, session) -> bool:
    """Provision base infrastructure for an environment if needed."""
    environment = deployment.environment
    env_slug = environment.slug

    logger.info("Provisioning base infrastructure for environment '%(env_slug)s'", {"env_slug": env_slug})

    # Update environment status
    environment.status = models.Environment.Status.PROVISIONING
    environment.vpc_stack_name = f"devopshero-{env_slug}-vpc"
    environment.cluster_stack_name = f"devopshero-{env_slug}-cluster"
    environment.save()

    try:
        success = infra_customer.deploy_base.deploy(
            session=session,
            env_slug=env_slug,
            synth_only=False,
        )

        if success:
            environment.status = models.Environment.Status.READY
            environment.save()
            logger.info("Base infrastructure ready for environment '%(env_slug)s'", {"env_slug": env_slug})
            return True

        environment.status = models.Environment.Status.ERROR
        environment.status_message = "Base infrastructure deployment failed"
        environment.save()
        logger.error("Base infrastructure deployment failed for environment '%(env_slug)s'", {"env_slug": env_slug})
        return False

    except Exception as e:
        environment.status = models.Environment.Status.ERROR
        environment.status_message = str(e)
        environment.save()
        raise


def run_deployment(deployment_id: str) -> bool:
    """
    Execute a deployment.

    This is the main entry point called by the deployment worker.
    It orchestrates the full deployment flow:
    1. Load deployment and related models
    2. Update status to BUILDING
    3. Provision environment base infra if needed
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
            "app__workspace__aws_account",
            "app__datastore",
            "environment",
            "environment__aws_account",
        ).get(id=deployment_id)
    except models.Deployment.DoesNotExist:
        logger.error("Deployment %(deployment_id)s not found", {"deployment_id": deployment_id})
        return False

    environment = deployment.environment
    app = deployment.app
    workspace = app.workspace

    with deployment_logging.DeploymentLogContext(
        deployment=deployment,
        source_default=models.DeploymentLog.Source.APP,
    ):
        logger.info(
            "Starting deployment %(deployment_id)s for app '%(app_name)s' to environment '%(environment_name)s'",
            {"deployment_id": str(deployment_id), "app_name": app.name, "environment_name": environment.name},
        )

        # Update status to BUILDING
        deployment.status = models.Deployment.Status.BUILDING
        deployment.status_message = "Deployment started"
        deployment.started_at = timezone.now()
        deployment.save()

        logger.info(
            "Starting deployment of '%(app_name)s' to '%(environment_name)s' (git_ref=%(git_ref)s, image_tag=%(image_tag)s)",
            {"app_name": app.name, "environment_name": environment.name, "git_ref": deployment.git_ref, "image_tag": deployment.image_tag},
        )

        try:
            # Get AWS session
            session = _get_aws_session(deployment)

            # Provision environment if needed
            if environment.status == models.Environment.Status.PENDING:
                if not _provision_environment(deployment, session):
                    deployment.status = models.Deployment.Status.FAILED
                    deployment.status_message = "Environment provisioning failed"
                    deployment.completed_at = timezone.now()
                    deployment.save()
                    return False

            # Build AppConfig
            app_config = app_config_builder.build_app_config(
                app=app,
                environment=environment,
            )

            # Execute deployment
            deployment.status = models.Deployment.Status.DEPLOYING
            deployment.save()

            success = infra_customer.deploy_app.deploy(
                session=session,
                account_id=environment.aws_account.aws_account_id,
                region=workspace.aws_region,
                app_config=app_config,
                image_tag=deployment.image_tag,
                env_slug=environment.slug,
                synth_only=False,
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
