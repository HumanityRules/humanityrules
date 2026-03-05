import json
from datetime import datetime
from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from devopshero_app.models import App, Deployment, ResourceTag
from devopshero_app.services import abac

from . import abac_view_checks
from . import base


def _get_app_for_user(request: HttpRequest, app_slug: str) -> App:
    """Get an app that belongs to the current user's organization."""
    return get_object_or_404(
        App.objects.select_related("workspace", "repository", "datastore", "created_by"),
        slug=app_slug,
        organization=request.user.current_organization,
    )


def _get_deployment_for_app(app: App, deployment_id: UUID) -> Deployment:
    """Get a deployment that belongs to the given app, with related environment."""
    return get_object_or_404(
        Deployment.objects.select_related("environment", "environment__aws_account"),
        id=deployment_id,
        app=app,
    )


def _build_app_detail_context(request: HttpRequest, app: App) -> dict[str, Any]:
    """Build the shared context dict for app detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="workspaces")

    deployments = Deployment.objects.filter(
        app=app,
    ).select_related("environment", "environment__aws_account").order_by("-created_at")[:20]

    # Build per-environment summary (first occurrence = latest, since ordered by -created_at)
    seen_environments = {}
    for deployment in deployments:
        if deployment.environment_id not in seen_environments:
            seen_environments[deployment.environment_id] = {
                "environment": deployment.environment,
                "latest_deployment": deployment,
            }
    environment_rows = list(seen_environments.values())

    secret_keys = list(app.app_secrets.keys()) if app.app_secrets else []
    cpu_vcpu = app.cpu / 1024

    # Tags
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    can_admin = abac.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:admin")

    context["app"] = app
    context["deployments"] = deployments
    context["environment_rows"] = environment_rows
    context["secret_keys"] = secret_keys
    context["cpu_vcpu"] = cpu_vcpu
    org = request.user.current_organization
    context["direct_tags"] = direct_tags
    context["inherited_tags"] = inherited_tags
    context["tags_json"] = json.dumps([{"key": t.key, "value": t.value} for t in direct_tags])
    context["can_admin"] = can_admin
    context["url_base"] = f"/apps/{app.slug}/tags/"
    context["suggested_keys"], context["suggested_values"] = abac.get_resource_tag_suggestions(org, "app")

    return context


@login_required
def app_detail(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Show app detail with configuration and deployments."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = f"/apps/{app_slug}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    context = _build_app_detail_context(request, app)
    return render(request, "devopshero_app/apps/app_detail.html", context=context)


@login_required
@require_POST
def app_deployment_teardown(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Trigger teardown for a deployment."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    deployment = _get_deployment_for_app(app, deployment_id)

    teardownable_statuses = [
        Deployment.Status.DEPLOYED,
    ]
    if deployment.status not in teardownable_statuses:
        return HttpResponse(status=422)

    deployment.status = Deployment.Status.TEARDOWN_PENDING
    deployment.status_message = "Teardown triggered via web UI"
    deployment.save(update_fields=["status", "status_message", "updated_at"])

    context = {"app": app, "deployment": deployment}
    return render(request, "devopshero_app/apps/_app_deployment_row.html", context=context)


@login_required
@require_GET
def app_deployment_status(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Return updated deployment row HTML for polling."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    deployment = _get_deployment_for_app(app, deployment_id)

    mode = request.GET.get("mode", "")
    context = {"app": app, "deployment": deployment, "mode": mode}
    return render(request, "devopshero_app/apps/_app_deployment_row.html", context=context)


@login_required
@require_GET
def app_teardown_confirm(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Return the teardown confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    deployment = _get_deployment_for_app(app, deployment_id)

    context = {"app": app, "deployment": deployment}
    return render(request, "devopshero_app/apps/_app_teardown_confirm_modal.html", context=context)


@login_required
@require_POST
def app_deployment_redeploy(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Create a new PENDING deployment to redeploy an app to the same environment."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    deployment = _get_deployment_for_app(app, deployment_id)

    if deployment.status != Deployment.Status.DEPLOYED:
        return HttpResponse(status=422)

    active_statuses = [
        Deployment.Status.PENDING,
        Deployment.Status.BUILDING,
        Deployment.Status.PUSHING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.STARTING,
    ]
    if Deployment.objects.filter(app=app, status__in=active_statuses).exists():
        return HttpResponse(status=422)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = app.branch[:8] if len(app.branch) > 8 else app.branch
    image_tag = f"{app.slug}-{short_ref}-{timestamp}"

    Deployment.objects.create(
        app=app,
        environment=deployment.environment,
        subdomain=deployment.subdomain,
        git_ref=app.branch,
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Redeploy triggered via web UI",
        created_by=request.user,
    )

    context = _build_app_detail_context(request, app)
    return render(request, "devopshero_app/apps/app_detail.html", context=context)


@login_required
@require_POST
def app_tag_add(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Add a tag to an app. Returns updated tag partial."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:admin")
    if denied:
        return denied

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        ResourceTag.objects.get_or_create(
            organization=request.user.current_organization,
            resource_type="app",
            app=app,
            key=key,
            value=value,
        )

    org = request.user.current_organization
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    url_base = f"/apps/{app.slug}/tags/"
    return render(request, "devopshero_app/apps/_app_tags.html", {
        "direct_tags": direct_tags, "inherited_tags": inherited_tags, "can_admin": True, "url_base": url_base,
        **dict(zip(("suggested_keys", "suggested_values"), abac.get_resource_tag_suggestions(org, "app"))),
    })


@login_required
@require_POST
def app_tag_remove(request: HttpRequest, app_slug: str, tag_id: UUID) -> HttpResponse:
    """Remove a tag from an app. Returns updated tag partial."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:admin")
    if denied:
        return denied

    ResourceTag.objects.filter(id=tag_id, app=app).delete()

    org = request.user.current_organization
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    url_base = f"/apps/{app.slug}/tags/"
    return render(request, "devopshero_app/apps/_app_tags.html", {
        "direct_tags": direct_tags, "inherited_tags": inherited_tags, "can_admin": True, "url_base": url_base,
        **dict(zip(("suggested_keys", "suggested_values"), abac.get_resource_tag_suggestions(org, "app"))),
    })


@login_required
@require_POST
def app_tags_save(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Bulk-save app tags. Replaces all direct tags with the submitted array."""

    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:admin")
    if denied:
        return denied

    org = request.user.current_organization
    tags_data = json.loads(request.POST.get("tags", "[]"))

    ResourceTag.objects.filter(app=app).delete()
    seen = set()
    for tag in tags_data:
        key = tag.get("key", "").strip()
        value = tag.get("value", "").strip()
        if key and value and (key, value) not in seen:
            seen.add((key, value))
            ResourceTag.objects.create(
                organization=org, resource_type="app", app=app,
                key=key, value=value,
            )

    tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    suggested_keys, suggested_values = abac.get_resource_tag_suggestions(org, "app")
    return render(request, "devopshero_app/partials/_security_tags_section.html", {
        "can_admin": True,
        "tags_title": "App Tags",
        "tags_json": json.dumps([{"key": t.key, "value": t.value} for t in tags]),
        "url_base": f"/apps/{app.slug}/tags/",
        "suggested_keys": suggested_keys,
        "suggested_values": suggested_values,
        "inherited_tags": inherited_tags,
        "empty_text": "No direct tags",
    })
