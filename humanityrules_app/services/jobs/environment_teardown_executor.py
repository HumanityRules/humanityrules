"""Execute admitted sandbox and customer environment teardowns."""

import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.db import transaction

from humanityrules_app import models
from humanityrules_app.services import infra_customer
from humanityrules_app.services import sandbox_service

from . import app_deployment_teardown_executor
from . import app_remove_executor
from . import job_logging

logger = logging.getLogger(__name__)


class EnvironmentTeardownError(RuntimeError):
    """Raised when one teardown step fails without an underlying exception."""


def _get_aws_session(environment: models.Environment) -> Any:
    """Get an assumed-role AWS session for the environment account."""
    aws_account = environment.aws_account
    return infra_customer.iam_utils.get_assumed_role_session(
        access_key=settings.HUMR_AWS_ACCESS_KEY,
        secret_key=settings.HUMR_AWS_SECRET_KEY,
        account_id=aws_account.aws_account_id,
        external_id=str(aws_account.external_id),
        region=environment.aws_region,
    )


def _teardown_app_infrastructure(environment: models.Environment) -> None:
    """Tear down every App that may still have infrastructure."""
    apps = list(
        models.App.objects
        .filter(environment=environment, may_have_infra=True)
        .select_related("source_template", "environment", "environment__aws_account")
        .order_by("created_at")
    )
    logger.info(
        "Tearing down %(count)d app(s) in environment '%(env_name)s'",
        {"count": len(apps), "env_name": environment.name},
    )
    for app in apps:
        logger.info("Tearing down infrastructure for app '%(app_name)s'", {"app_name": app.name})
        if not app_deployment_teardown_executor.teardown_infra(app=app):
            raise EnvironmentTeardownError(f"Could not tear down infrastructure for app '{app.slug}'.")


def _purge_sandbox_app_data(environment: models.Environment) -> list[models.App]:
    """Purge every sandbox App namespace before any slug claim is released."""
    apps = list(
        models.App.objects
        .filter(environment=environment)
        .select_related("source_template")
        .order_by("created_at")
    )
    for app in apps:
        ok, message = app_remove_executor.purge_app_namespace_data(app=app, env=environment)
        if not ok:
            raise EnvironmentTeardownError(f"Sandbox data purge failed for app '{app.slug}': {message}")
    return apps


def _delete_sandbox_records(environment: models.Environment, apps: list[models.App]) -> None:
    """Atomically release sandbox claims and delete the Apps and Environment."""
    with transaction.atomic():
        for app in apps:
            sandbox_service.release_sandbox_app_slug(
                app_slug=app.slug,
                organization_id=app.organization_id,
            )
            app.delete()
        environment.delete()


def _delete_customer_records(environment: models.Environment) -> None:
    """Atomically delete customer Apps and their Environment row."""
    with transaction.atomic():
        models.App.objects.filter(environment=environment).delete()
        environment.delete()


def _teardown_sandbox_environment(environment: models.Environment) -> None:
    """Run the sandbox-specific teardown pipeline without touching shared base infrastructure."""
    _teardown_app_infrastructure(environment=environment)
    apps = _purge_sandbox_app_data(environment=environment)
    logger.info("Deleting sandbox environment records for '%(env_name)s'", {"env_name": environment.name})
    _delete_sandbox_records(environment=environment, apps=apps)


def _teardown_customer_environment(environment: models.Environment) -> None:
    """Run the dedicated customer environment teardown pipeline."""
    _teardown_app_infrastructure(environment=environment)
    session = _get_aws_session(environment=environment)
    logger.info("Deleting base infrastructure for environment '%(env_slug)s'", {"env_slug": environment.slug})
    if not infra_customer.deploy_base.teardown(session=session, env_slug=environment.slug):
        raise EnvironmentTeardownError("Infrastructure stack deletion failed. Check CloudFormation for details.")
    logger.info("Deleting customer environment records for '%(env_name)s'", {"env_name": environment.name})
    _delete_customer_records(environment=environment)


def _fail_environment_teardown(environment: models.Environment, message: str) -> None:
    """Move a failed teardown back to ERROR for an explicit retry."""
    environment.status = models.Environment.Status.ERROR
    environment.status_message = message
    environment.save(update_fields=["status", "status_message", "updated_at"])


def _run_teardown_pipeline(environment: models.Environment, pipeline: Callable[[models.Environment], None]) -> bool:
    """Run one selected pipeline and settle its Environment status on failure."""
    try:
        pipeline(environment)
        return True
    except EnvironmentTeardownError as exc:
        logger.error("Environment teardown failed: %(error)s", {"error": str(exc)})
        _fail_environment_teardown(environment=environment, message=str(exc))
        return False
    except Exception as exc:
        logger.exception("Environment teardown error: %(error)s", {"error": str(exc)})
        _fail_environment_teardown(environment=environment, message=f"Teardown error: {exc}")
        return False


def run_environment_teardown(environment_id: str) -> bool:
    """Execute the already-admitted environment teardown."""
    try:
        environment = models.Environment.objects.select_related("aws_account").get(id=environment_id)
    except models.Environment.DoesNotExist:
        logger.error("Environment %(environment_id)s not found", {"environment_id": environment_id})
        return False

    if environment.status != models.Environment.Status.TEARING_DOWN:
        logger.error(
            "Environment %(environment_id)s is not claimed for teardown (status: %(status)s)",
            {"environment_id": environment_id, "status": environment.status},
        )
        return False

    pipeline = (
        _teardown_sandbox_environment
        if environment.aws_account.is_humr_sandbox
        else _teardown_customer_environment
    )
    with job_logging.EnvironmentLogContext(
        environment=environment,
        source_default=models.EnvironmentLog.Source.SYSTEM,
    ):
        logger.info(
            "Starting teardown for environment '%(env_name)s' in account '%(account_name)s'",
            {"env_name": environment.name, "account_name": environment.aws_account.name},
        )
        return _run_teardown_pipeline(environment=environment, pipeline=pipeline)
