from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db.models import Max, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from devopshero_app.models import App, Conversation, Deployment, Repository, ResourceTag, Workspace
from devopshero_app.services import abac

from . import abac_view_checks
from . import base


@login_required
def workspaces(request: HttpRequest) -> HttpResponse:
    """List all workspaces in the current organization."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
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
    workspace_list = abac.filter_permitted_resources(
        request.user.current_organization, request.user, workspace_list, "workspace", "workspace:view",
    )

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["workspaces"] = workspace_list
    return render(request, "devopshero_app/workspaces/workspaces.html", context=context)


@login_required
def workspace_detail(request: HttpRequest, workspace_slug: str) -> HttpResponse:
    """Show workspace detail with apps, datastores, and conversations."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = f"/workspaces/{workspace_slug}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    workspace = get_object_or_404(
        Workspace,
        slug=workspace_slug,
        organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, workspace, "workspace", "workspace:view")
    if denied:
        return denied

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

    tags = ResourceTag.objects.filter(workspace=workspace).order_by("key", "value")
    can_admin = abac.check_action(request.user.current_organization, request.user, workspace, "workspace", "workspace:admin")

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["workspace"] = workspace
    context["apps"] = apps
    context["datastores"] = datastores
    context["conversations"] = conversations
    context["repositories"] = repositories
    context["show_costs"] = show_costs
    context["tags"] = tags
    context["can_admin"] = can_admin
    context["url_base"] = f"/workspaces/{workspace.slug}/tags/"
    context["suggested_keys"], context["suggested_values"] = abac.get_resource_tag_suggestions(request.user.current_organization, "workspace")

    return render(request, "devopshero_app/workspaces/workspace_detail.html", context=context)


@login_required
@require_POST
def workspace_create(request: HttpRequest) -> HttpResponse:
    """Create a new workspace and redirect to it."""
    denied = abac_view_checks.check_abac_create(request, "workspace", "workspace:edit")
    if denied:
        return denied

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


@login_required
@require_POST
def workspace_tag_add(request: HttpRequest, workspace_slug: str) -> HttpResponse:
    """Add a tag to a workspace. Returns updated tag partial."""
    workspace = get_object_or_404(Workspace, slug=workspace_slug, organization=request.user.current_organization)

    denied = abac_view_checks.check_abac(request, workspace, "workspace", "workspace:admin")
    if denied:
        return denied

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        ResourceTag.objects.get_or_create(
            organization=request.user.current_organization,
            resource_type="workspace",
            workspace=workspace,
            key=key,
            value=value,
        )

    org = request.user.current_organization
    tags = ResourceTag.objects.filter(workspace=workspace).order_by("key", "value")
    url_base = f"/workspaces/{workspace.slug}/tags/"
    return render(request, "devopshero_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#workspace-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac.get_resource_tag_suggestions(org, "workspace"))),
    })


@login_required
@require_POST
def workspace_tag_remove(request: HttpRequest, workspace_slug: str, tag_id: UUID) -> HttpResponse:
    """Remove a tag from a workspace. Returns updated tag partial."""
    workspace = get_object_or_404(Workspace, slug=workspace_slug, organization=request.user.current_organization)

    denied = abac_view_checks.check_abac(request, workspace, "workspace", "workspace:admin")
    if denied:
        return denied

    ResourceTag.objects.filter(id=tag_id, workspace=workspace).delete()

    org = request.user.current_organization
    tags = ResourceTag.objects.filter(workspace=workspace).order_by("key", "value")
    url_base = f"/workspaces/{workspace.slug}/tags/"
    return render(request, "devopshero_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#workspace-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac.get_resource_tag_suggestions(org, "workspace"))),
    })

