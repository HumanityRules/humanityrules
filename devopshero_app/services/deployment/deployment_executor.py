"""
Deployment executor.

Orchestrates the actual deployment by calling CDK infrastructure code.
This is the main entry point called by the deployment worker.
"""

import logging
import traceback

from django.conf import settings
from django.utils import timezone

import deploy_app as deploy_app_module
import deploy_base as deploy_base_module
import iam_utils

from devopshero_app.models import Deployment, DeploymentLog, Environment

from . import app_config_builder

logger = logging.getLogger(__name__)


def _create_log(deployment: Deployment, phase: str, level: str, message: str, details: dict | None = None) -> None:
    """Create a deployment log entry."""
    DeploymentLog.objects.create(
        deployment=deployment,
        phase=phase,
        level=level,
        message=message,
        details=details,
    )


def _make_log_callback(deployment: Deployment):
    """Create a log callback function for CDK operations."""
    def log_callback(phase: str, level: str, message: str) -> None:
        _create_log(
            deployment=deployment,
            phase=phase,
            level=level,
            message=message,
        )
    return log_callback


def _get_aws_session(deployment: Deployment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = deployment.environment.aws_account

    return iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=deployment.app.workspace.aws_region,
    )


def _provision_environment(deployment: Deployment, session) -> bool:
    """Provision base infrastructure for an environment if needed."""
    environment = deployment.environment
    env_slug = environment.slug

    _create_log(
        deployment=deployment,
        phase=DeploymentLog.Phase.DEPLOY,
        level=DeploymentLog.Level.INFO,
        message=f"Provisioning base infrastructure for environment '{env_slug}'",
    )

    # Update environment status
    environment.status = Environment.Status.PROVISIONING
    environment.vpc_stack_name = f"devopshero-{env_slug}-vpc"
    environment.cluster_stack_name = f"devopshero-{env_slug}-cluster"
    environment.save()

    try:
        success = deploy_base_module.deploy(
            session=session,
            env_slug=env_slug,
            synth_only=False,
            log_callback=_make_log_callback(deployment),
        )

        if success:
            environment.status = Environment.Status.READY
            environment.save()
            _create_log(
                deployment=deployment,
                phase=DeploymentLog.Phase.DEPLOY,
                level=DeploymentLog.Level.INFO,
                message=f"Base infrastructure ready for environment '{env_slug}'",
            )
            return True
        else:
            environment.status = Environment.Status.ERROR
            environment.status_message = "Base infrastructure deployment failed"
            environment.save()
            return False

    except Exception as e:
        environment.status = Environment.Status.ERROR
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
        deployment = Deployment.objects.select_related(
            "app",
            "app__workspace",
            "app__workspace__aws_account",
            "app__datastore",
            "environment",
            "environment__aws_account",
        ).get(id=deployment_id)
    except Deployment.DoesNotExist:
        logger.error(f"Deployment {deployment_id} not found")
        return False

    environment = deployment.environment
    app = deployment.app
    workspace = app.workspace

    logger.info(f"Starting deployment {deployment_id} for app '{app.name}' to environment '{environment.name}'")

    # Update status to BUILDING
    deployment.status = Deployment.Status.BUILDING
    deployment.status_message = "Deployment started"
    deployment.started_at = timezone.now()
    deployment.save()

    _create_log(
        deployment=deployment,
        phase=DeploymentLog.Phase.INIT,
        level=DeploymentLog.Level.INFO,
        message=f"Starting deployment of '{app.name}' to '{environment.name}'",
        details={
            "app_slug": app.slug,
            "workspace_slug": workspace.slug,
            "environment_slug": environment.slug,
            "git_ref": deployment.git_ref,
            "image_tag": deployment.image_tag,
        },
    )

    try:
        # Get AWS session
        session = _get_aws_session(deployment)

        # Provision environment if needed
        if environment.status == Environment.Status.PENDING:
            if not _provision_environment(deployment, session):
                deployment.status = Deployment.Status.FAILED
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
        _create_log(
            deployment=deployment,
            phase=DeploymentLog.Phase.DEPLOY,
            level=DeploymentLog.Level.INFO,
            message=f"Deploying app '{app.name}'",
        )

        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()

        success = deploy_app_module.deploy(
            session=session,
            account_id=environment.aws_account.aws_account_id,
            region=workspace.aws_region,
            app_config=app_config,
            image_tag=deployment.image_tag,
            env_slug=environment.slug,
            workspace_slug=workspace.slug,
            synth_only=False,
            log_callback=_make_log_callback(deployment),
        )

        if success:
            deployment.status = Deployment.Status.RUNNING
            deployment.status_message = "Deployment completed successfully"
            deployment.completed_at = timezone.now()
            # TODO: Extract service_url and alb_dns from CDK outputs
            deployment.save()

            _create_log(
                deployment=deployment,
                phase=DeploymentLog.Phase.COMPLETE,
                level=DeploymentLog.Level.INFO,
                message="Deployment completed successfully",
            )
            logger.info(f"Deployment {deployment_id} completed successfully")
            return True
        else:
            deployment.status = Deployment.Status.FAILED
            deployment.status_message = "CDK deployment failed"
            deployment.completed_at = timezone.now()
            deployment.save()

            _create_log(
                deployment=deployment,
                phase=DeploymentLog.Phase.COMPLETE,
                level=DeploymentLog.Level.ERROR,
                message="Deployment failed",
            )
            logger.error(f"Deployment {deployment_id} failed")
            return False

    except Exception as e:
        error_msg = f"Deployment error: {e}"
        logger.exception(error_msg)

        deployment.status = Deployment.Status.FAILED
        deployment.status_message = error_msg
        deployment.completed_at = timezone.now()
        deployment.save()

        _create_log(
            deployment=deployment,
            phase=DeploymentLog.Phase.COMPLETE,
            level=DeploymentLog.Level.ERROR,
            message=error_msg,
            details={"traceback": traceback.format_exc()},
        )
        return False
