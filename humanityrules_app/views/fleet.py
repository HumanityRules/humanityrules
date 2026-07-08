"""Staff-only platform fleet dashboard: every org's deployments, with on-demand live AWS state.

Deliberately unscoped by organization — this is a platform-operator view, so the
staff gate replaces the usual per-org query scoping.
"""

import functools

from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from humanityrules_app import models
from humanityrules_app.services import fleet_status


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
    return render(request, "humanityrules_app/fleet/fleet.html", context=context)


@_staff_or_404
def fleet_env_live_state(request: HttpRequest, environment_id: str) -> HttpResponse:
    environment = get_object_or_404(models.Environment.objects.select_related("aws_account"), id=environment_id)
    context = {
        "environment": environment,
        "live_state": fleet_status.fetch_env_live_state(environment),
    }
    return render(request, "humanityrules_app/fleet/fleet.html#env_live_state", context=context)
