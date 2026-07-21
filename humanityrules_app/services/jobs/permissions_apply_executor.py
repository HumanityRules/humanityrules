"""
Permissions apply executor.

Applies approved IAM permission changes to the app's ECS task role.
This is the main entry point called by the job worker.
"""

import logging

from humanityrules_app import models
from humanityrules_app.services import permissions_service
from humanityrules_app.services.infra_customer import iam_utils

from . import tenant_consistency

logger = logging.getLogger(__name__)


def run_apply(app_permission_request_id: str) -> bool:
    """Apply approved permissions to the app's IAM task role.

    Converts the normalized statements to an IAM policy document and calls
    put_role_policy on the ECS task role. Updates AppPermissions baseline on success.
    """
    try:
        apr = models.AppPermissionRequest.objects.select_related(
            "app",
            "app__environment",
            "app__environment__aws_account",
        ).get(id=app_permission_request_id)
    except models.AppPermissionRequest.DoesNotExist:
        logger.error("AppPermissionRequest %s not found", app_permission_request_id)
        return False

    try:
        tenant_consistency.assert_apr_consistent(apr)
    except tenant_consistency.TenantConsistencyError as exc:
        logger.error("Refusing to apply permissions: %s", exc)
        apr.status = models.AppPermissionRequest.Status.FAILED
        apr.status_message = f"Refused: {exc}"
        apr.save(update_fields=["status", "status_message", "updated_at"])
        return False

    logger.info(
        "Applying permissions for app '%s' in environment '%s' (request %s)",
        apr.app.name, apr.app.environment.name, app_permission_request_id,
    )

    try:
        iam_utils.write_app_permissions_policy(
            environment=apr.app.environment,
            app=apr.app,
            policy_name=permissions_service.HUMR_APP_PERMISSIONS_POLICY_NAME,
            statements=apr.statements,
        )
    except Exception as e:
        logger.exception("Failed to apply permissions for request %s", app_permission_request_id)
        apr.status = models.AppPermissionRequest.Status.FAILED
        apr.status_message = f"IAM policy update failed: {e}"
        apr.save(update_fields=["status", "status_message", "updated_at"])
        return False

    # IAM update succeeded — advance the baseline and mark applied
    app_permissions = permissions_service.get_or_create_app_permissions(app=apr.app)
    app_permissions.statements = apr.statements
    app_permissions.save(update_fields=["statements", "updated_at"])

    apr.status = models.AppPermissionRequest.Status.APPLIED
    apr.status_message = "Permissions applied successfully"
    apr.save(update_fields=["status", "status_message", "updated_at"])

    logger.info("Permissions applied successfully for request %s", app_permission_request_id)
    return True
