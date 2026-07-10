"""Staff-only platform fleet dashboard: every org's deployments, with on-demand live AWS state.

Deliberately unscoped by organization — this is a platform-operator view, so the
staff gate replaces the usual per-org query scoping.
"""

import functools
from uuid import UUID

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from humanityrules_app import models
from humanityrules_app.services import fleet_redeploy
from humanityrules_app.services import fleet_status

FLEET_LOG_MAX_LINES = 300


def _staff_or_404(view_func):
    """Gate to staff, answering everyone else with a 404 so the page's existence isn't revealed."""
    @functools.wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not (request.user.is_authenticated and request.user.is_staff):
            raise Http404
        return view_func(request, *args, **kwargs)
    return wrapper


@_staff_or_404
def fleet(request: HttpRequest) -> HttpResponse:
    context = {"env_groups": fleet_status.build_fleet_snapshot()}
    template = "humanityrules_app/fleet/fleet.html"
    if request.htmx:
        template += "#fleet_list"
    return render(request, template, context=context)


@_staff_or_404
@require_GET
def fleet_redeploy_all_confirm(request: HttpRequest) -> HttpResponse:
    """Return the fleet-wide redeploy confirmation modal."""
    context = {"preview": fleet_redeploy.build_redeploy_all_preview()}
    return render(request, "humanityrules_app/fleet/_fleet_redeploy_all_confirm.html", context=context)


@_staff_or_404
@require_POST
def fleet_redeploy_all(request: HttpRequest) -> HttpResponse:
    """Queue redeployments for the eligible current fleet rows."""
    include_failed = request.POST.get("include_failed") == "on"
    redeploy_result = fleet_redeploy.queue_redeploy_all(
        created_by=request.user,
        include_failed=include_failed,
    )
    context = {
        "env_groups": fleet_status.build_fleet_snapshot(),
        "redeploy_result": redeploy_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@_staff_or_404
@require_POST
def fleet_deployment_redeploy(request: HttpRequest, deployment_id: UUID) -> HttpResponse:
    """Queue a redeploy for one eligible HA row on the fleet page."""
    deployment = get_object_or_404(models.Deployment.objects.only("id"), id=deployment_id)
    redeploy_result = fleet_redeploy.queue_redeploy(
        source_id=deployment.id,
        created_by=request.user,
    )
    context = {
        "env_groups": fleet_status.build_fleet_snapshot(),
        "redeploy_result": redeploy_result,
    }
    return render(request, "humanityrules_app/fleet/fleet.html#fleet_list", context=context)


@_staff_or_404
def fleet_deployment_log(request: HttpRequest, deployment_id: str) -> HttpResponse:
    deployment = get_object_or_404(models.Deployment.objects.select_related("app", "environment"), id=deployment_id)
    # Fetch newest-first so the cap keeps the tail, then reverse to chronological for display.
    recent = list(models.DeploymentLog.objects.filter(deployment=deployment).order_by("-created_at")[: FLEET_LOG_MAX_LINES + 1])
    context = {
        "deployment": deployment,
        "logs": list(reversed(recent[:FLEET_LOG_MAX_LINES])),
        "truncated": len(recent) > FLEET_LOG_MAX_LINES,
        "max_lines": FLEET_LOG_MAX_LINES,
    }
    return render(request, "humanityrules_app/fleet/_fleet_deployment_log.html", context=context)


@_staff_or_404
def fleet_env_live_state(request: HttpRequest, environment_id: str) -> HttpResponse:
    environment = get_object_or_404(models.Environment.objects.select_related("aws_account"), id=environment_id)
    context = {
        "environment": environment,
        "live_state": fleet_status.fetch_env_live_state(environment),
    }
    return render(request, "humanityrules_app/fleet/fleet.html#env_live_state", context=context)
