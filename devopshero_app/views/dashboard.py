from django.contrib.auth.decorators import login_required
from django.db.models import Max, OuterRef, Subquery
from django.shortcuts import render

from ..models import App, Datastore, Deployment
from .base import get_app_shell_context


@login_required
def dashboard(request):
    context = get_app_shell_context(request=request, current_page="dashboard")
    org = request.user.current_organization
    
    latest_deployment_status = (
        Deployment.objects.filter(app=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    latest_running_service_url = (
        Deployment.objects.filter(app=OuterRef("pk"), status=Deployment.Status.RUNNING)
        .order_by("-created_at")
        .values("service_url")[:1]
    )
    apps = (
        App.objects.filter(organization=org)
        .select_related("workspace", "repository")
        .annotate(
            last_deployed_at=Max("deployments__created_at"),
            latest_status=Subquery(latest_deployment_status),
            running_service_url=Subquery(latest_running_service_url),
        )
        .order_by("-created_at")
    )
    datastores = Datastore.objects.filter(workspace__organization=org).select_related("workspace").order_by("-created_at")
    
    context["apps"] = apps
    context["datastores"] = datastores
    
    if request.htmx:        
        return render(request, "devopshero_app/dashboard.html", context=context)

    context["content_url"] = "/dashboard/"
    return render(request, "devopshero_app/app_shell.html", context=context)

