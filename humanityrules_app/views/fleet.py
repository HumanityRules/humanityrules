"""Staff-only platform fleet dashboard for every organization's deployments.

Deliberately unscoped by organization — this is a platform-operator view, so the
staff gate replaces the usual per-org query scoping.
"""

from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from humanityrules_app import models
from humanityrules_app.services import fleet_service
from humanityrules_app.views import platform_access

FLEET_LOG_MAX_LINES = 300


@platform_access.platform_staff_required
def fleet(request: HttpRequest) -> HttpResponse:
    context = {"env_groups": fleet_service.build_fleet_snapshot()}
    template = "humanityrules_app/fleet/fleet.html"
    if request.htmx:
        template += "#fleet_list"
    return render(request, template, context=context)


@platform_access.platform_staff_required
@require_GET
def fleet_redeploy_all_confirm(request: HttpRequest) -> HttpResponse:
    """Return the fleet-wide redeploy confirmation modal."""
    context = {"preview": fleet_service.build_redeploy_all_preview()}
    return render(request, "humanityrules_app/fleet/_fleet_redeploy_all_confirm.html", context=context)


@platform_access.platform_staff_required
@require_POST
def fleet_redeploy_all(request: HttpRequest) -> HttpResponse:
    """Queue redeployments for the eligible current fleet rows."""
    include_failed = request.POST.get("include_failed") == "on"
    redeploy_result = fleet_service.queue_redeploy_all(
        created_by=request.user,
        include_failed=include_failed,
    )
    context = {
        "env_groups": fleet_service.build_fleet_snapshot(),
        "redeploy_result": redeploy_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@platform_access.platform_staff_required
@require_GET
def fleet_fail_unsettled_deployments_confirm(request: HttpRequest) -> HttpResponse:
    """Return the confirmation modal for the fleet recovery action."""
    context = {"unsettled_count": fleet_service.count_unsettled_deployments()}
    return render(request, "humanityrules_app/fleet/_fleet_fail_unsettled_confirm.html", context=context)


@platform_access.platform_staff_required
@require_POST
def fleet_fail_unsettled_deployments(request: HttpRequest) -> HttpResponse:
    """Mark all deployment jobs left in unsettled states as failed."""
    recovery_result = fleet_service.fail_unsettled_deployments()
    context = {
        "env_groups": fleet_service.build_fleet_snapshot(),
        "recovery_result": recovery_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@platform_access.platform_staff_required
@require_POST
def fleet_deployment_redeploy(request: HttpRequest, app_id: UUID) -> HttpResponse:
    """Queue a redeploy for one eligible HA row on the fleet page."""
    app = get_object_or_404(models.App.objects.only("id"), id=app_id)
    redeploy_result = fleet_service.queue_redeploy(
        app_id=app.id,
        created_by=request.user,
    )
    context = {
        "env_groups": fleet_service.build_fleet_snapshot(),
        "redeploy_result": redeploy_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@platform_access.platform_staff_required
@require_GET
def fleet_app_remove_confirm(request: HttpRequest, app_id: UUID) -> HttpResponse:
    """Return the confirmation modal for tearing down and deleting one HA."""
    app = get_object_or_404(
        models.App.objects.select_related("organization", "environment", "environment__aws_account", "source_template"),
        id=app_id,
    )
    context = {
        "app": app,
        "skip_reason": fleet_service.get_remove_skip_reason(app=app),
    }
    return render(request, "humanityrules_app/fleet/_fleet_remove_confirm.html", context=context)


@platform_access.platform_staff_required
@require_POST
def fleet_app_remove(request: HttpRequest, app_id: UUID) -> HttpResponse:
    """Queue teardown, data purge, and deletion for one HA row on the fleet page."""
    app = get_object_or_404(models.App.objects.only("id"), id=app_id)
    remove_result = fleet_service.queue_remove(
        app_id=app.id,
        created_by=request.user,
    )
    context = {
        "env_groups": fleet_service.build_fleet_snapshot(),
        "remove_result": remove_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@platform_access.platform_staff_required
def fleet_deployment_log(request: HttpRequest, app_id: str) -> HttpResponse:
    app = get_object_or_404(models.App.objects.select_related("environment"), id=app_id)
    # Fetch newest-first so the cap keeps the tail, then reverse to chronological for display.
    recent = list(
        models.DeploymentLog.objects.filter(app=app, attempt_id=app.last_attempt_id)
        .order_by("-created_at")[: FLEET_LOG_MAX_LINES + 1]
    )
    context = {
        "app": app,
        "logs": list(reversed(recent[:FLEET_LOG_MAX_LINES])),
        "truncated": len(recent) > FLEET_LOG_MAX_LINES,
        "max_lines": FLEET_LOG_MAX_LINES,
    }
    return render(request, "humanityrules_app/fleet/_fleet_deployment_log.html", context=context)
