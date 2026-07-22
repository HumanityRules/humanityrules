from django.contrib.auth.decorators import login_required
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

    visible_workspaces = Workspace.objects.filter(organization=org)
    visible_workspaces = abac_service.filter_permitted_resources(
        org, request.user, visible_workspaces, "workspace", "workspace:view",
    )

    apps = (
        App.objects.filter(workspace__in=visible_workspaces)
        .select_related("workspace", "repository")
        .order_by("-created_at")
    )
    apps = list(apps)

    context = base.get_app_shell_context(request=request, current_page="dashboard")
    context["apps"] = apps

    return render(request, "humanityrules_app/dashboard.html", context=context)
