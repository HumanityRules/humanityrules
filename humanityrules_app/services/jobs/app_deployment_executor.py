"""
Deployment executor.

Orchestrates the actual deployment by calling CDK infrastructure code.
This is the main entry point called by the job worker, which has already
moved the app to DEPLOYING and stamped its claim.
"""

import logging

from django.conf import settings

from humanityrules_app import models
from humanityrules_app.services import infra_customer
import humanityrules_app.services.jobs.app_deployment_debug_simulator as app_deployment_debug_simulator

from . import app_config_builder
from . import app_job_service
from . import job_logging
from . import tenant_consistency

logger = logging.getLogger(__name__)


def _get_aws_session(environment: models.Environment):
    """Get an AWS session with assumed role credentials for the target account."""
    aws_account = environment.aws_account

    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def run_deployment(app_id: str) -> bool:
    """Execute the deploy attempt for an app the worker claimed. Returns success."""
    try:
        app = models.App.objects.select_related(
            "workspace",
            "source_template",
            "environment",
            "environment__aws_account",
        ).get(id=app_id)
    except models.App.DoesNotExist:
        logger.error("App %(app_id)s not found", {"app_id": app_id})
        return False

    environment = app.environment

    try:
        tenant_consistency.assert_app_owns_environment(app=app, environment=environment)
    except tenant_consistency.TenantConsistencyError as exc:
        logger.error("Refusing to run deployment: %(msg)s", {"msg": str(exc)})
        app_job_service.settle_failure(app=app, error=f"Refused: {exc}")
        return False

    with job_logging.DeploymentLogContext(
        app=app,
        attempt_id=app.last_attempt_id,
        source_default=models.DeploymentLog.Source.APP,
    ):
        logger.info(
            "Starting deployment for app '%(app_name)s' to environment '%(environment_name)s'",
            {"app_name": app.name, "environment_name": environment.name},
        )

        # Verify environment is READY (agent must provision it via provision_environment tool)
        if environment.status != models.Environment.Status.READY:
            error_msg = f"Environment '{environment.name}' is not ready (status: {environment.status}). Use provision_environment to provision it first."
            logger.error(error_msg)
            app_job_service.settle_failure(app=app, error=error_msg)
            return False

        if settings.HUMR_DEBUG_DEPLOYMENTS:
            return app_deployment_debug_simulator.run_debug_deployment(app=app)

        try:
            session = _get_aws_session(environment=environment)

            app_config = app_config_builder.build_app_config_from_app(app=app)

            # Progress marker for the no-progress tier of the stale-job reaper.
            app.save(update_fields=["updated_at"])

            result = infra_customer.deploy_app.deploy(
                session=session,
                account_id=environment.aws_account.aws_account_id,
                region=environment.aws_region,
                app_config=app_config,
                build_id=str(app.last_attempt_id),
                env_slug=environment.slug,
                environment=environment,
                app=app,
                subdomain=app.slug,
                synth_only=False,
                shared_alb_hosted_zone=environment.shared_alb_hosted_zone or None,
            )

            if result.success:
                app_job_service.settle_deploy_success(
                    app=app, service_url=result.service_url, alb_dns=result.alb_dns, image_hashes=result.image_hashes,
                )
                logger.info("Deployment of '%(app_slug)s' completed successfully", {"app_slug": app.slug})
                return True
            else:
                app_job_service.settle_failure(app=app, error=result.error or "Deployment failed")
                logger.error("Deployment of '%(app_slug)s' failed, error: %(error)s", {"app_slug": app.slug, "error": result.error})
                return False

        except Exception as e:
            logger.exception("Deployment error: %(error)s", {"error": str(e)})
            app_job_service.settle_failure(app=app, error=f"Deployment error: {e}")
            return False
