import logging

from django.utils import timezone

from .. import models
from .infra_customer import iam_utils

logger = logging.getLogger(__name__)

DOH_APP_PERMISSIONS_POLICY_NAME = "doh-app-permissions"


def get_or_create_app_permissions(app, environment) -> models.AppPermissions:
    """Get or create the AppPermissions baseline for an app+environment.

    If no AppPermissions exists yet, seeds from AWS (best-effort, empty list on failure).
    """
    try:
        return models.AppPermissions.objects.get(app=app, environment=environment)
    except models.AppPermissions.DoesNotExist:
        pass

    try:
        statements = iam_utils.read_app_permissions_policy(
            environment=environment, app=app, policy_name=DOH_APP_PERMISSIONS_POLICY_NAME,
        )
    except Exception:
        logger.exception("Failed to seed AppPermissions from AWS for %s/%s", app.slug, environment.slug)
        statements = []

    return models.AppPermissions.objects.create(
        app=app,
        environment=environment,
        statements=statements,
    )


def get_or_create_draft(app, environment, user, app_permissions) -> models.AppPermissionRequest:
    """Find an existing DRAFT AppPermissionRequest for this app+environment, or create a new one."""
    existing = models.AppPermissionRequest.objects.filter(
        app=app,
        environment=environment,
        status=models.AppPermissionRequest.Status.DRAFT,
    ).order_by("-created_at").first()

    if existing:
        return existing

    return models.AppPermissionRequest.objects.create(
        app=app,
        environment=environment,
        statements=app_permissions.statements,
        status=models.AppPermissionRequest.Status.DRAFT,
        created_by=user,
    )


def update_statements(app_permission_request, *, action: str, service: str, level: str = "", arn: str = ""):
    """Mutate a single aspect of an AppPermissionRequest's statements and save."""
    statements = app_permission_request.statements or []

    def _find_or_create_service(svc):
        for stmt in statements:
            if stmt.get("service") == svc:
                return stmt
        new_stmt = {"service": svc, "effect": "Allow", "access_levels": [], "resources": []}
        statements.append(new_stmt)
        return new_stmt

    if action == "add_service" and service:
        _find_or_create_service(service)

    elif action == "remove_service" and service:
        app_permission_request.statements = [s for s in statements if s.get("service") != service]
        statements = app_permission_request.statements

    elif action == "add_level" and service and level:
        stmt = _find_or_create_service(service)
        if level not in stmt.get("access_levels", []):
            stmt.setdefault("access_levels", []).append(level)

    elif action == "remove_level" and service and level:
        for stmt in statements:
            if stmt.get("service") == service:
                stmt["access_levels"] = [l for l in stmt.get("access_levels", []) if l != level]

    elif action == "add_resource" and service and arn:
        stmt = _find_or_create_service(service)
        if arn not in stmt.get("resources", []):
            stmt.setdefault("resources", []).append(arn)

    elif action == "remove_resource" and service and arn:
        for stmt in statements:
            if stmt.get("service") == service:
                stmt["resources"] = [r for r in stmt.get("resources", []) if r != arn]

    app_permission_request.statements = statements
    app_permission_request.save(update_fields=["statements", "updated_at"])


def approve(app_permission_request, app_permissions):
    """Set AppPermissionRequest status to APPROVED_PENDING_APPLY and update the baseline."""
    app_permission_request.status = models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY
    app_permission_request.save(update_fields=["status", "updated_at"])

    app_permissions.statements = app_permission_request.statements
    app_permissions.save(update_fields=["statements", "updated_at"])


def cancel(app_permission_request, app_permissions):
    """Reset the draft's statements back to the AppPermissions baseline."""
    app_permission_request.statements = app_permissions.statements
    app_permission_request.save(update_fields=["statements", "updated_at"])


def get_resources_for_services(environment, services):
    """Return cached AWS resources, fetching from AWS only for cache misses.

    Returns {"s3": [{"arn": "...", "label": "..."}, ...], ...}.
    """
    if not services:
        return {}

    cached = models.AwsResourceCache.objects.filter(
        environment=environment, service__in=services,
    )
    result = {entry.service: entry.resources for entry in cached}

    missing = [s for s in services if s not in result]
    if missing:
        fetched = iam_utils.list_resources_for_services(environment, missing)
        now = timezone.now()
        models.AwsResourceCache.objects.bulk_create(
            [
                models.AwsResourceCache(
                    environment=environment,
                    service=svc,
                    resources=fetched.get(svc, []),
                    fetched_at=now,
                )
                for svc in missing
            ],
            ignore_conflicts=True,
        )
        result.update(fetched)

    return result


def refresh_resources_cache(environment, services):
    """Delete and re-fetch cached resources for the given services.

    Returns {"s3": [{"arn": "...", "label": "..."}, ...], ...}.
    """
    if not services:
        return {}

    models.AwsResourceCache.objects.filter(
        environment=environment, service__in=services,
    ).delete()

    fetched = iam_utils.list_resources_for_services(environment, services)
    now = timezone.now()
    models.AwsResourceCache.objects.bulk_create(
        [
            models.AwsResourceCache(
                environment=environment,
                service=svc,
                resources=fetched.get(svc, []),
                fetched_at=now,
            )
            for svc in services
        ],
        ignore_conflicts=True,
    )
    return fetched
