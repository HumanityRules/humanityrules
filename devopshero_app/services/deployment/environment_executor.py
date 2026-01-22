"""
Environment executor.

Orchestrates environment provisioning by calling CDK infrastructure code.
This is the main entry point called by the job worker.
"""

import logging

from django.conf import settings

from devopshero_app import models
from devopshero_app.services import infra_customer

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
        region="us-east-1",  # Default region for base infrastructure
    )


def run_provisioning(environment_id: str) -> bool:
    """
    Execute environment provisioning.

    This is the main entry point called by the job worker.
    It orchestrates the full provisioning flow:
    1. Load environment and related models
    2. Update status to PROVISIONING
    3. Execute CDK deployment (VPC + ECS cluster + shared ALB)
    4. Update environment status

    Args:
        environment_id: UUID of the Environment to provision.

    Returns:
        True if provisioning succeeded, False otherwise.
    """
    try:
        environment = models.Environment.objects.select_related(
            "aws_account",
        ).get(id=environment_id)
    except models.Environment.DoesNotExist:
        logger.error("Environment %(environment_id)s not found", {"environment_id": environment_id})
        return False

    aws_account = environment.aws_account

    with job_logging.EnvironmentLogContext(
        environment=environment,
        source_default=models.EnvironmentLog.Source.SYSTEM,
    ):
        logger.info(
            "Starting provisioning for environment '%(environment_name)s' in account '%(account_name)s'",
            {"environment_name": environment.name, "account_name": aws_account.name},
        )

        # Update status to PROVISIONING
        environment.status = models.Environment.Status.PROVISIONING
        environment.status_message = "Provisioning started"
        environment.save()

        try:
            # Get AWS session
            session = _get_aws_session(environment)

            # Determine shared ALB hosted zone
            shared_alb_hosted_zone = environment.shared_alb_hosted_zone or None

            logger.info(
                "Deploying base infrastructure (VPC, ECS cluster, shared ALB) for environment '%(env_slug)s'",
                {"env_slug": environment.slug},
            )

            if shared_alb_hosted_zone:
                logger.info(
                    "HTTPS enabled with wildcard cert for '%(hosted_zone)s'",
                    {"hosted_zone": shared_alb_hosted_zone},
                )

            # Execute CDK deployment
            success = infra_customer.deploy_base.deploy(
                session=session,
                env_slug=environment.slug,
                synth_only=False,
                shared_alb_hosted_zone=shared_alb_hosted_zone,
            )

            if success:
                environment.status = models.Environment.Status.READY
                environment.status_message = "Provisioning completed successfully"
                environment.save()

                logger.info(
                    "Environment '%(environment_name)s' provisioned successfully",
                    {"environment_name": environment.name},
                )
                return True

            environment.status = models.Environment.Status.ERROR
            environment.status_message = "Infrastructure deployment failed. Check CloudFormation console for details."
            environment.save()

            logger.error(
                "Environment '%(environment_name)s' provisioning failed",
                {"environment_name": environment.name},
            )
            return False

        except Exception as e:
            logger.exception("Provisioning error: %(error)s", {"error": str(e)})

            environment.status = models.Environment.Status.ERROR
            environment.status_message = f"Provisioning error: {e}"
            environment.save()
            return False
