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
from humanityrules_app.services import sandbox_service

from . import app_deployment_teardown_executor
from . import app_job_service
from . import app_remove_executor
from . import job_logging

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


def _teardown_all_deployments(environment: models.Environment) -> bool:
    """
    Tear down every app in the environment that may still have infra behind it.

    Runs each teardown synchronously, stopping on the first failure.
    """
    apps = list(
        models.App.objects
        .filter(environment=environment, may_have_infra=True)
        .select_related("workspace", "source_template", "environment", "environment__aws_account")
        .order_by("created_at")
    )

    if not apps:
        logger.info("No active deployments to tear down in environment '%(env_name)s'", {"env_name": environment.name})
        return True

    logger.info(
        "Tearing down %(count)d app(s) in environment '%(env_name)s'",
        {"count": len(apps), "env_name": environment.name},
    )

    for app in apps:
        logger.info(
            "Tearing down infra for app '%(app_name)s'",
            {"app_name": app.name},
        )

        # Open the attempt directly in TEARING_DOWN so the job worker won't claim it
        # (it only polls TEARDOWN_PENDING). run_teardown() is called synchronously below.
        app_job_service.start_inline_teardown(app=app, created_by=None)

        success = app_deployment_teardown_executor.run_teardown(app_id=str(app.id))

        if not success:
            logger.error(
                "Failed to tear down infra for app '%(app_name)s' - stopping environment teardown",
                {"app_name": app.name},
            )
            return False

        logger.info(
            "Successfully tore down infra for app '%(app_name)s'",
            {"app_name": app.name},
        )

    logger.info("All apps torn down successfully")
    return True


def _delete_environment_apps(environment: models.Environment) -> bool:
    """Delete the environment's apps (cascading their deployments, permissions, activity, and logs).

    Apps live and die with their environment; releasing each sandbox slug claim
    here frees the name for reuse, and removing the App rows satisfies the
    PROTECT FK so the environment row itself can be deleted.

    In a sandbox account the slug is reusable across orgs, so releasing it while its
    data survives would let the next claimant inherit it. Each app's namespaces are
    therefore purged BEFORE its slug is released; on the first purge failure we stop
    and return False (the caller sets the env to ERROR) so a slug is never released
    with data left behind.
    """
    is_sandbox = environment.aws_account.is_humr_sandbox
    apps = models.App.objects.filter(environment=environment).select_related("source_template")
    for app in apps:
        if is_sandbox:
            ok, message = app_remove_executor.purge_app_namespace_data(app=app, env=environment)
            if not ok:
                logger.error(
                    "Sandbox data purge failed for app '%(app_slug)s' - stopping environment teardown: %(message)s",
                    {"app_slug": app.slug, "message": message},
                )
                return False
            sandbox_service.release_sandbox_app_slug(app_slug=app.slug, organization_id=app.organization_id)

        app.delete()
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

            # Sandbox environments share one base infra across all orgs — never delete the
            # shared base stacks. Just remove this org's deployment records and env row.
            if environment.aws_account.is_humr_sandbox:
                logger.info(
                    "Sandbox environment '%(env_name)s': skipping shared base infra teardown",
                    {"env_name": environment.name},
                )
                if not _delete_environment_apps(environment=environment):
                    environment.status = models.Environment.Status.ERROR
                    environment.status_message = "Environment teardown failed: sandbox data purge failed"
                    environment.save(update_fields=["status", "status_message", "updated_at"])
                    return False
                environment.delete()
                return True

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
                # Dedicated (non-sandbox) accounts never claim slugs, so the purge does not
                # run and _delete_environment_apps cannot fail here — no ERROR branch needed.
                _delete_environment_apps(environment=environment)
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
