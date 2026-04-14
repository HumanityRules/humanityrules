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


def _build_teardown_app_config(
    deployment: models.Deployment,
) -> infra_customer.appconfig.AppConfig:
    """Build a minimal AppConfig for teardown — only needs app_name, ecr_repo_name, database_config."""
    app = deployment.app
    environment = deployment.environment
    ecr_repo_name = f"doh/{environment.slug}/{app.slug}"

    database_config = None
    datastore = deployment.blueprint.datastore
    if datastore:
        database_config = infra_customer.appconfig.DatabaseConfig(
            name=datastore.database_name,
            engine=infra_customer.appconfig.EngineConfig(
                family=datastore.engine,
                version=None,
                auto_minor_version_upgrade=False,
            ),
            deployment=infra_customer.appconfig.DeploymentConfig(
                mode="aurora_serverless_v2",
                serverless_v2=infra_customer.appconfig.ServerlessV2Config(
                    min_acu=0.5,
                    max_acu=2.0,
                ),
                provisioned=None,
            ),
            backups=infra_customer.appconfig.BackupConfig(
                retention_days=1,
                copy_tags_to_snapshot=False,
            ),
            security=infra_customer.appconfig.SecurityConfig(
                storage_encrypted=True,
                deletion_protection=False,
            ),
            connection=infra_customer.appconfig.ConnectionConfig(
                env_var_name="DATABASE_URL",
            ),
        )

    cpu = deployment.blueprint.cpu
    memory = deployment.blueprint.memory

    return infra_customer.appconfig.AppConfig(
        app_name=app.slug,
        ecr_repo_name=ecr_repo_name,
        container_port=app.container_port,
        cpu=cpu,
        memory=memory,
        health_check_path=app.health_check_path,
        health_check_command=None,
        environment_variables=[],
        app_source_path=None,
        database_config=database_config,
        app_secrets=None,
    )


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
            # Get AWS session
            session = _get_aws_session(deployment)

            # Build minimal AppConfig for teardown
            app_config = _build_teardown_app_config(deployment=deployment)

            # Execute teardown
            logger.info(
                "Tearing down app '%(app_name)s' stacks",
                {"app_name": app.name},
            )

            success = infra_customer.deploy_app.teardown(
                session=session,
                app_config=app_config,
                env_slug=environment.slug,
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
