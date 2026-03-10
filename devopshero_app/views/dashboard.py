from django.contrib.auth.decorators import login_required
from django.db.models import Max, OuterRef, Subquery
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..models import App, Datastore, Deployment, Workspace
from ..services import abac
from . import apps as apps_views
from . import base


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="dashboard")
        context["content_url"] = "/dashboard/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    org = request.user.current_organization

    latest_deployment_status = (
        Deployment.objects.filter(app=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    latest_deployed_service_url = (
        Deployment.objects.filter(app=OuterRef("pk"), status=Deployment.Status.SUCCEEDED)
        .order_by("-created_at")
        .values("service_url")[:1]
    )
    visible_workspaces = Workspace.objects.filter(organization=org)
    visible_workspaces = abac.filter_permitted_resources(
        org, request.user, visible_workspaces, "workspace", "workspace:view",
    )

    apps = (
        App.objects.filter(workspace__in=visible_workspaces)
        .select_related("workspace", "repository")
        .annotate(
            last_deployed_at=Max("deployments__created_at"),
            latest_status=Subquery(latest_deployment_status),
            open_blueprint_status=Subquery(apps_views.get_open_blueprint_status_subquery()),
            deployed_service_url=Subquery(latest_deployed_service_url),
        )
        .order_by("-created_at")
    )
    apps = list(apps)
    for app in apps:
        apps_views.populate_deployment_entrypoint(
            app=app,
            open_blueprint_status=app.open_blueprint_status or "",
        )
    datastores = Datastore.objects.filter(workspace__in=visible_workspaces).select_related("workspace").order_by("-created_at")

    context = base.get_app_shell_context(request=request, current_page="dashboard")
    context["apps"] = apps
    context["datastores"] = datastores

    return render(request, "devopshero_app/dashboard.html", context=context)

