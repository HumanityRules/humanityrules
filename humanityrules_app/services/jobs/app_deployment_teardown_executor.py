"""
App deployment teardown executor.

Orchestrates the teardown of deployed applications by calling CDK infrastructure code.
This is the entry point called by the job worker (which has already moved the app to
TEARING_DOWN) and by flows that run teardown inline (app removal, environment teardown).
"""

import logging

from django.conf import settings

from humanityrules_app import models
from humanityrules_app.services import infra_customer

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


def teardown_infra(app: models.App) -> bool:
    """Delete the app's stack without touching App job state.

    For flows that tear down inline as part of a larger attempt (app removal,
    environment teardown) and manage App transitions themselves.
    """
    session = _get_aws_session(environment=app.environment)
    return infra_customer.deploy_app.teardown(
        session=session,
        env_slug=app.environment.slug,
        app_name=app.slug,
    )


def run_teardown(app_id: str) -> bool:
    """Execute the teardown attempt for an app in TEARING_DOWN. Returns success."""
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
        logger.error("Refusing to tear down app: %(msg)s", {"msg": str(exc)})
        app_job_service.settle_failure(app=app, error=f"Refused: {exc}")
        return False

    with job_logging.DeploymentLogContext(
        app=app,
        attempt_id=app.last_attempt_id,
        source_default=models.DeploymentLog.Source.APP,
    ):
        logger.info(
            "Starting teardown for app '%(app_name)s' in environment '%(environment_name)s'",
            {"app_name": app.name, "environment_name": environment.name},
        )

        try:
            session = _get_aws_session(environment=environment)

            logger.info(
                "Tearing down app '%(app_name)s' stacks",
                {"app_name": app.name},
            )

            success = infra_customer.deploy_app.teardown(
                session=session,
                env_slug=environment.slug,
                app_name=app.slug,
            )

            if success:
                app_job_service.settle_teardown_success(app=app)
                logger.info("Teardown of '%(app_slug)s' completed successfully", {"app_slug": app.slug})
                return True

            app_job_service.settle_failure(app=app, error="Teardown failed - some stacks may not have been deleted")
            logger.error("Teardown of '%(app_slug)s' failed", {"app_slug": app.slug})
            return False

        except Exception as e:
            logger.exception("Teardown error: %(error)s", {"error": str(e)})
            app_job_service.settle_failure(app=app, error=f"Teardown error: {e}")
            return False
