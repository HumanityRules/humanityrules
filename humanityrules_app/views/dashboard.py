from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import App, Workspace
from ..services import abac_service
from . import base


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="dashboard")
        context["content_url"] = "/dashboard/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    org = request.user.current_organization

    apps_prefetch = Prefetch(
        "apps",
        queryset=App.objects.select_related("environment", "repository").order_by("name"),
        to_attr="dashboard_apps",
    )
    visible_workspaces = Workspace.objects.filter(organization=org).prefetch_related(apps_prefetch)
    visible_workspaces = abac_service.filter_permitted_resources(
        org, request.user, visible_workspaces, "workspace", "workspace:view",
    )
    workspaces = sorted(
        visible_workspaces,
        key=lambda workspace: (workspace.slug != "default", workspace.name.casefold()),
    )
    for workspace in workspaces:
        workspace.can_admin = abac_service.check_action(
            org, request.user, workspace, "workspace", "workspace:admin",
        )

    context = base.get_app_shell_context(request=request, current_page="dashboard")
    context["workspaces"] = workspaces

    return render(request, "humanityrules_app/dashboard.html", context=context)
