"""
Environment teardown executor.

Orchestrates the teardown of an entire environment:
1. Tears down all deployments (sequentially, stop on first failure)
2. Deletes the cluster stack (CloudFormation)
3. Deletes the VPC stack (CloudFormation)

This is the entry point called by the job worker for environment teardown jobs.
"""

import logging

from django.conf import settings

from humanityrules_app import models
from humanityrules_app.services import infra_customer

from . import app_deployment_teardown_executor
from . import job_logging

logger = logging.getLogger(__name__)


def _get_aws_session(environment: models.Environment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = environment.aws_account

    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.DOH_AWS_ACCESS_KEY,
        secret_key=settings.DOH_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def _teardown_all_deployments(environment: models.Environment) -> bool:
    """
    Tear down and delete all deployments in the environment.

    Iterates through all deployments and tears them down sequentially.
    Each deployment is deleted after successful teardown. Stops on first failure.

    Returns True if all deployments were torn down successfully, False otherwise.
    """
    deployments = list(
        models.Deployment.objects
        .filter(environment=environment)
        .select_related("app", "app__workspace", "environment", "environment__aws_account")
        .order_by("created_at")
    )

    if not deployments:
        logger.info("No active deployments to tear down in environment '%(env_name)s'", {"env_name": environment.name})
        return True

    logger.info(
        "Tearing down %(count)d deployment(s) in environment '%(env_name)s'",
        {"count": len(deployments), "env_name": environment.name},
    )

    for deployment in deployments:
        logger.info(
            "Tearing down deployment for app '%(app_name)s' (%(deployment_id)s)",
            {"app_name": deployment.app.name, "deployment_id": str(deployment.id)},
        )

        # Set directly to TEARING_DOWN so the job worker won't claim this deployment
        # (it only polls for TEARDOWN_PENDING). run_teardown() is called synchronously below.
        deployment.status = models.Deployment.Status.TEARING_DOWN
        deployment.status_message = "Teardown as part of environment teardown"
        deployment.save(update_fields=["status", "status_message", "updated_at"])

        # Run the teardown synchronously
        success = app_deployment_teardown_executor.run_teardown(deployment_id=str(deployment.id))

        if not success:
            logger.error(
                "Failed to tear down deployment for app '%(app_name)s' - stopping environment teardown",
                {"app_name": deployment.app.name},
            )
            return False

        logger.info(
            "Successfully tore down deployment for app '%(app_name)s'",
            {"app_name": deployment.app.name},
        )

    logger.info("All deployments torn down successfully")
    return True


def run_environment_teardown(environment_id: str) -> bool:
    """
    Execute environment teardown.

    This is the main entry point called by the job worker.
    It orchestrates the full teardown flow:
    1. Load environment and related models
    2. Update status to TEARING_DOWN
    3. Tear down all deployments (each deleted after teardown)
    4. Delete cluster/builder/VPC CloudFormation stacks
    5. Delete the environment record from the database

    Returns True if teardown succeeded, False otherwise.
    """
    try:
        environment = models.Environment.objects.select_related("aws_account").get(id=environment_id)
    except models.Environment.DoesNotExist:
        logger.error("Environment %(environment_id)s not found", {"environment_id": environment_id})
        return False

    with job_logging.EnvironmentLogContext(
        environment=environment,
        source_default=models.EnvironmentLog.Source.SYSTEM,
    ):
        logger.info(
            "Starting teardown for environment '%(env_name)s' in account '%(account_name)s'",
            {"env_name": environment.name, "account_name": environment.aws_account.name},
        )

        # Update status to TEARING_DOWN
        environment.status = models.Environment.Status.TEARING_DOWN
        environment.status_message = "Teardown started"
        environment.save(update_fields=["status", "status_message", "updated_at"])

        try:
            # Step 1: Tear down all deployments
            if not _teardown_all_deployments(environment):
                environment.status = models.Environment.Status.ERROR
                environment.status_message = "Environment teardown failed: could not tear down all deployments"
                environment.save(update_fields=["status", "status_message", "updated_at"])
                return False

            # Step 2: Get AWS session and delete infrastructure stacks
            session = _get_aws_session(environment)

            logger.info(
                "Deleting infrastructure stacks for environment '%(env_slug)s'",
                {"env_slug": environment.slug},
            )

            success = infra_customer.deploy_base.teardown(
                session=session,
                env_slug=environment.slug,
            )

            if success:
                env_name = environment.name
                logger.info(
                    "Environment '%(env_name)s' torn down and deleted successfully",
                    {"env_name": env_name},
                )
                # Delete torn-down deployment/blueprint records so the PROTECT FKs allow environment deletion
                models.Deployment.objects.filter(environment=environment).delete()
                models.DeploymentBlueprint.objects.filter(environment=environment).delete()
                environment.delete()
                return True

            environment.status = models.Environment.Status.ERROR
            environment.status_message = "Infrastructure stack deletion failed. Check CloudFormation console for details."
            environment.save(update_fields=["status", "status_message", "updated_at"])

            logger.error(
                "Environment '%(env_name)s' teardown failed",
                {"env_name": environment.name},
            )
            return False

        except Exception as e:
            logger.exception("Environment teardown error: %(error)s", {"error": str(e)})

            environment.status = models.Environment.Status.ERROR
            environment.status_message = f"Teardown error: {e}"
            environment.save(update_fields=["status", "status_message", "updated_at"])
            return False
