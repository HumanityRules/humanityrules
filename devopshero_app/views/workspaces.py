from django.contrib.auth.decorators import login_required
from django.db.models import Max, OuterRef, Prefetch, Subquery, Sum
from django.shortcuts import get_object_or_404, render

from devopshero_app.models import Conversation, Deployment, Repository, Workspace

from .base import get_app_shell_context


@login_required
def workspaces(request):
    """List all workspaces in the current organization."""
    context = get_app_shell_context(request=request, current_page="workspaces")
    
    workspace_list = Workspace.objects.filter(
        organization=request.user.current_organization,
    ).prefetch_related("apps").order_by("name")
    
    context["workspaces"] = workspace_list
    
    if request.htmx:
        return render(request, "devopshero_app/workspaces/workspaces.html", context=context)

    context["content_url"] = "/workspaces/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def workspace_detail(request, workspace_slug):
    """Show workspace detail with apps, datastores, and conversations."""
    context = get_app_shell_context(request=request, current_page="workspaces")
    
    workspace = get_object_or_404(
        Workspace,
        slug=workspace_slug,
        organization=request.user.current_organization,
    )
    
    # Prefetch active deployments (running, not being torn down) with their environments
    active_deployments_prefetch = Prefetch(
        "deployments",
        queryset=Deployment.objects.filter(
            status=Deployment.Status.RUNNING,
        ).select_related("environment", "environment__aws_account").order_by("environment__name"),
        to_attr="active_deployments",
    )
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
    apps = workspace.apps.select_related("repository").prefetch_related(
        active_deployments_prefetch,
    ).annotate(
        last_deployed_at=Max("deployments__created_at"),
        latest_status=Subquery(latest_deployment_status),
        running_service_url=Subquery(latest_running_service_url),
    ).order_by("name")
    datastores = workspace.datastores.order_by("name")
    show_costs = request.user.is_staff
    conversations_qs = Conversation.objects.filter(
        context_workspace=workspace,
        user=request.user,
    ).order_by("-updated_at")
    if show_costs:
        conversations_qs = conversations_qs.annotate(total_cost=Sum("llm_usage_logs__cost_usd"))
    conversations = conversations_qs[:10]
    
    repositories = Repository.objects.filter(
        organization=request.user.current_organization,
    ).order_by("full_name")
    
    context["workspace"] = workspace
    context["apps"] = apps
    context["datastores"] = datastores
    context["conversations"] = conversations
    context["repositories"] = repositories
    context["show_costs"] = show_costs
    
    if request.htmx:
        return render(request, "devopshero_app/workspaces/workspace_detail.html", context=context)

    context["content_url"] = f"/workspaces/{workspace_slug}/"
    return render(request, "devopshero_app/app_shell.html", context=context)

