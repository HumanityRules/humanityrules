from django.contrib.auth.decorators import login_required
from django.db.models import Max, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from devopshero_app.models import App, Conversation, Deployment, Repository, Workspace

from .base import get_app_shell_context


@login_required
def workspaces(request):
    """List all workspaces in the current organization."""
    if not request.htmx:
        context = get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = "/workspaces/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    latest_deployment_status = (
        Deployment.objects.filter(app=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    apps_prefetch = Prefetch(
        "apps",
        queryset=App.objects.annotate(
            latest_status=Coalesce(Subquery(latest_deployment_status), Value("never_deployed")),
        ).order_by("name"),
        to_attr="annotated_apps",
    )
    workspace_list = Workspace.objects.filter(
        organization=request.user.current_organization,
    ).prefetch_related(apps_prefetch).order_by("name")

    context = get_app_shell_context(request=request, current_page="workspaces")
    context["workspaces"] = workspace_list
    return render(request, "devopshero_app/workspaces/workspaces.html", context=context)


@login_required
def workspace_detail(request, workspace_slug):
    """Show workspace detail with apps, datastores, and conversations."""
    if not request.htmx:
        context = get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = f"/workspaces/{workspace_slug}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    workspace = get_object_or_404(
        Workspace,
        slug=workspace_slug,
        organization=request.user.current_organization,
    )

    # Prefetch active deployments (deployed, not being torn down) with their environments
    active_deployments_prefetch = Prefetch(
        "deployments",
        queryset=Deployment.objects.filter(
            status=Deployment.Status.DEPLOYED,
        ).select_related("environment", "environment__aws_account").order_by("environment__name"),
        to_attr="active_deployments",
    )
    latest_deployment_status = (
        Deployment.objects.filter(app=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    latest_deployed_service_url = (
        Deployment.objects.filter(app=OuterRef("pk"), status=Deployment.Status.DEPLOYED)
        .order_by("-created_at")
        .values("service_url")[:1]
    )
    apps = workspace.apps.select_related("repository").prefetch_related(
        active_deployments_prefetch,
    ).annotate(
        last_deployed_at=Max("deployments__created_at"),
        latest_status=Subquery(latest_deployment_status),
        deployed_service_url=Subquery(latest_deployed_service_url),
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

    context = get_app_shell_context(request=request, current_page="workspaces")
    context["workspace"] = workspace
    context["apps"] = apps
    context["datastores"] = datastores
    context["conversations"] = conversations
    context["repositories"] = repositories
    context["show_costs"] = show_costs

    return render(request, "devopshero_app/workspaces/workspace_detail.html", context=context)


@login_required
@require_POST
def workspace_create(request):
    """Create a new workspace and redirect to it."""
    name = request.POST.get("name", "").strip()
    if not name:
        return redirect("workspaces")

    org = request.user.current_organization
    base_slug = slugify(name)
    slug = base_slug
    counter = 1
    while Workspace.objects.filter(organization=org, slug=slug).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    workspace = Workspace.objects.create(
        organization=org,
        name=name,
        slug=slug,
        created_by=request.user,
    )
    return redirect("workspace_detail", workspace_slug=workspace.slug)

