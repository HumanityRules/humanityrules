import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.db.models import Case, IntegerField, OuterRef, Subquery, Value, When
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from devopshero_app.models import App, Deployment, DeploymentBlueprint, ResourceTag
from devopshero_app.services import abac

from . import abac_view_checks
from . import base

OPEN_BLUEPRINT_STATUSES = (
    DeploymentBlueprint.Status.DRAFT,
    DeploymentBlueprint.Status.FAILED,
    DeploymentBlueprint.Status.DEPLOYING,
)


@dataclass
class DeployedEnvironmentRow:
    """Blueprint-backed summary row for one deployed environment."""

    blueprint: DeploymentBlueprint
    current_deployment: Deployment


def _get_app_for_user(request: HttpRequest, app_slug: str) -> App:
    """Get an app that belongs to the current user's organization."""
    return get_object_or_404(
        App.objects.select_related("workspace", "repository", "created_by"),
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


def get_open_blueprint(app: App) -> DeploymentBlueprint | None:
    """Return the latest in-progress blueprint for an app, if any."""
    return (
        DeploymentBlueprint.objects.filter(app=app, status__in=OPEN_BLUEPRINT_STATUSES)
        .select_related("app", "environment", "datastore")
        .order_by("-created_at")
        .first()
    )



def _get_current_launched_blueprint(blueprints: list[DeploymentBlueprint]) -> DeploymentBlueprint | None:
    """Return the current launched blueprint for one environment."""
    current_blueprints = [
        blueprint
        for blueprint in blueprints
        if getattr(blueprint, "current_launched_deployment_created_at", None) is not None
    ]
    if current_blueprints:
        current_blueprints.sort(
            key=lambda blueprint: (
                blueprint.current_launched_deployment_created_at,
                blueprint.created_at,
            ),
            reverse=True,
        )
        return current_blueprints[0]
    return None


def _build_deployed_environment_rows(app: App) -> list[DeployedEnvironmentRow]:
    """Build one summary row per deployed environment, showing the most relevant deployment.

    For each environment the app has been deployed to, finds the latest launched blueprint
    and pairs it with a "current" deployment chosen by priority: transient operations first
    (deploys and teardowns in flight), then terminal authoritative conclusions (succeeded or
    torn down) picked by recency, then everything else. This means a failed redeploy attempt
    won't hide the last successful deployment, while a completed teardown correctly supersedes
    a prior success.
    """
    status_priority = Case(
        When(status__in=Deployment.TRANSIENT_STATUSES, then=Value(0)),
        When(
            status__in=(Deployment.Status.SUCCEEDED, Deployment.Status.TORN_DOWN),
            then=Value(1),
        ),
        default=Value(2),
        output_field=IntegerField(),
    )
    current_deployment_id_subquery = (
        Deployment.objects.filter(blueprint=OuterRef("pk"))
        .filter(status__in=Deployment.VISIBLE_STATUSES)
        .annotate(status_priority=status_priority)
        .order_by("status_priority", "-created_at")
        .values("id")[:1]
    )
    current_deployment_created_at_subquery = (
        Deployment.objects.filter(blueprint=OuterRef("pk"))
        .filter(status__in=Deployment.VISIBLE_STATUSES)
        .annotate(status_priority=status_priority)
        .order_by("status_priority", "-created_at")
        .values("created_at")[:1]
    )
    current_launched_deployment_created_at_subquery = (
        Deployment.objects.filter(blueprint=OuterRef("pk"))
        .filter(status__in=Deployment.CONCLUDED_STATUSES)
        .order_by("-created_at")
        .values("created_at")[:1]
    )

    blueprints = list(
        DeploymentBlueprint.objects.filter(app=app)
        .exclude(status=DeploymentBlueprint.Status.DISCARDED)
        .select_related("environment", "environment__aws_account", "datastore")
        .annotate(
            current_deployment_id=Subquery(current_deployment_id_subquery),
            current_deployment_created_at=Subquery(current_deployment_created_at_subquery),
            current_launched_deployment_created_at=Subquery(current_launched_deployment_created_at_subquery),
        )
    )

    blueprints_by_environment_id: dict[UUID, list[DeploymentBlueprint]] = {}
    for blueprint in blueprints:
        blueprints_by_environment_id.setdefault(blueprint.environment_id, []).append(blueprint)

    current_blueprints = []
    for environment_blueprints in blueprints_by_environment_id.values():
        current_blueprint = _get_current_launched_blueprint(blueprints=environment_blueprints)
        if current_blueprint:
            current_blueprints.append(current_blueprint)

    current_deployment_ids = [
        blueprint.current_deployment_id
        for blueprint in current_blueprints
        if getattr(blueprint, "current_deployment_id", None)
    ]
    current_deployments_by_id = {
        deployment.id: deployment
        for deployment in Deployment.objects.filter(id__in=current_deployment_ids).select_related(
            "app",
            "environment",
            "environment__aws_account",
        )
    }

    environment_rows = [
        DeployedEnvironmentRow(
            blueprint=blueprint,
            current_deployment=current_deployments_by_id[blueprint.current_deployment_id],
        )
        for blueprint in current_blueprints
    ]
    environment_rows.sort(key=lambda row: row.current_deployment.created_at, reverse=True)
    return environment_rows


def build_app_detail_context(request: HttpRequest, app: App) -> dict[str, Any]:
    """Build the shared context dict for app detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="workspaces")

    deployments = Deployment.objects.filter(
        app=app,
    ).select_related("environment", "environment__aws_account").order_by("-created_at")[:20]
    open_blueprint = get_open_blueprint(app=app)
    environment_rows = _build_deployed_environment_rows(app=app)

    # Tags
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    can_edit = abac.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:edit")
    can_admin = abac.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:admin")

    context["app"] = app
    context["deployments"] = deployments
    context["environment_rows"] = environment_rows
    context["open_blueprint"] = open_blueprint
    org = request.user.current_organization
    context["direct_tags"] = direct_tags
    context["inherited_tags"] = inherited_tags
    context["tags_json"] = json.dumps([{"key": t.key, "value": t.value} for t in direct_tags])
    context["can_edit"] = can_edit
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

    context = build_app_detail_context(request, app)
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
        Deployment.Status.SUCCEEDED,
        Deployment.Status.FAILED,
    ]
    if deployment.status not in teardownable_statuses:
        return HttpResponse(status=422)

    deployment.status = Deployment.Status.TEARDOWN_PENDING
    deployment.status_message = "Teardown triggered via web UI"
    deployment.save(update_fields=["status", "status_message", "updated_at"])

    render_mode = request.GET.get("render", "")
    mode = request.GET.get("mode", "")
    if render_mode == "app_detail":
        context = build_app_detail_context(request=request, app=app)
        return render(request, "devopshero_app/apps/app_detail.html", context=context)

    context = {"app": app, "deployment": deployment, "mode": mode}
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
def blueprint_row_status(request: HttpRequest, blueprint_id: UUID) -> HttpResponse:
    """Return updated blueprint row inner HTML for self-terminating polling."""
    blueprint = get_object_or_404(
        DeploymentBlueprint.objects.select_related(
            "app", "app__workspace", "environment", "environment__aws_account", "datastore",
        ),
        id=blueprint_id,
        app__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, blueprint.app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    current_deployment = (
        Deployment.objects.filter(blueprint=blueprint, status__in=Deployment.VISIBLE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if not current_deployment:
        return HttpResponse(status=404)

    context = {
        "app": blueprint.app,
        "blueprint": blueprint,
        "current_deployment": current_deployment,
    }
    return render(
        request,
        "devopshero_app/apps/_app_blueprint_row.html#blueprint_row_content",
        context=context,
    )


@login_required
@require_GET
def app_card_status(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return updated app card status pill for polling."""
    latest_deployment_status = (
        Deployment.objects.filter(app=OuterRef("pk"))
        .order_by("-created_at")
        .values("status")[:1]
    )
    app = get_object_or_404(
        App.objects.annotate(latest_status=Subquery(latest_deployment_status)),
        slug=app_slug,
        organization=request.user.current_organization,
    )
    return render(request, "devopshero_app/partials/_app_card_status.html", {"app": app})


@login_required
@require_GET
def app_teardown_confirm(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Return the teardown confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    deployment = _get_deployment_for_app(app, deployment_id)

    mode = request.GET.get("mode", "")
    render_mode = request.GET.get("render", "")
    post_url = reverse("app_deployment_teardown", kwargs={"app_slug": app.slug, "deployment_id": deployment.id})
    response_target = f"#deployment-{deployment.id}"
    response_swap = "outerHTML"

    query_params = []
    if mode:
        query_params.append(f"mode={mode}")
    if render_mode == "app_detail":
        query_params.append("render=app_detail")
        response_target = "#main-content"
        response_swap = "innerHTML"
    if query_params:
        post_url = f"{post_url}?{'&'.join(query_params)}"

    context = {
        "app": app,
        "deployment": deployment,
        "post_url": post_url,
        "response_target": response_target,
        "response_swap": response_swap,
    }
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

    if deployment.status not in (Deployment.Status.SUCCEEDED, Deployment.Status.FAILED, Deployment.Status.TORN_DOWN):
        return HttpResponse(status=422)

    if Deployment.objects.filter(app=app, status__in=Deployment.IN_PROGRESS_STATUSES).exists():
        return HttpResponse(status=422)

    git_ref = deployment.git_ref or app.branch
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    image_tag = f"{app.slug}-{short_ref}-{timestamp}"

    Deployment.objects.create(
        blueprint=deployment.blueprint,
        app=app,
        environment=deployment.environment,
        subdomain=deployment.subdomain,
        git_ref=git_ref,
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Redeploy triggered via web UI",
        created_by=request.user,
    )

    context = build_app_detail_context(request, app)
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
