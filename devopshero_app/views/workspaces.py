from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
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
        ).select_related("environment").order_by("environment__name"),
        to_attr="active_deployments",
    )
    apps = workspace.apps.select_related("repository").prefetch_related(
        active_deployments_prefetch,
    ).order_by("name")
    datastores = workspace.datastores.order_by("name")
    conversations = Conversation.objects.filter(
        context_workspace=workspace,
        user=request.user,
    ).order_by("-updated_at")[:10]
    
    repositories = Repository.objects.filter(
        organization=request.user.current_organization,
    ).order_by("full_name")
    
    context["workspace"] = workspace
    context["apps"] = apps
    context["datastores"] = datastores
    context["conversations"] = conversations
    context["repositories"] = repositories
    
    if request.htmx:
        return render(request, "devopshero_app/workspaces/workspace_detail.html", context=context)

    context["content_url"] = f"/workspaces/{workspace_slug}/"
    return render(request, "devopshero_app/app_shell.html", context=context)

