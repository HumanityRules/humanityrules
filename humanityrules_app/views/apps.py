import json
from datetime import datetime
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, IntegerField, OuterRef, Subquery, Value, When
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from humanityrules_app.models import App, AppRemovalJob, Deployment, DeploymentLog, Environment, ResourceTag
from humanityrules_app.services import abac_service
from humanityrules_app.services.cost import panel as cost_panel
from humanityrules_app.services.jobs import environment_operation_gate

from . import abac_view_checks
from . import base
from . import webapp_public_access

# Cap rendered log lines; the deployment-log fragment re-renders every second while polling.
MAX_DEPLOYMENT_LOG_LINES = 1000

def app_is_live(app: App) -> bool:
    """An app is 'live' if its latest deployment is not TORN_DOWN."""
    latest_status = (
        Deployment.objects.filter(app=app).order_by("-created_at").values_list("status", flat=True).first()
    )
    return latest_status is not None and latest_status != Deployment.Status.TORN_DOWN


def _app_has_persistent_data(app: App) -> bool:
    """True if the template declares persistent storage (EFS or per-container host bind mounts)."""
    template = app.source_template
    if not template:
        return False
    if template.efs_config:
        return True
    for container in (template.containers or []):
        if container.get("host_mounts"):
            return True
    return False


def _get_app_for_user(request: HttpRequest, app_slug: str) -> App:
    """Get an app that belongs to the current user's organization."""
    return get_object_or_404(
        App.objects.select_related(
            "workspace", "repository", "created_by", "source_template",
            "environment", "environment__aws_account",
        ),
        slug=app_slug,
        organization=request.user.current_organization,
    )


def _get_deployment_for_app(app: App, deployment_id: UUID) -> Deployment:
    """Get a deployment that belongs to the given app."""
    return get_object_or_404(Deployment, id=deployment_id, app=app)


def get_current_deployment(app: App) -> Deployment | None:
    """Return the app's most relevant visible deployment.

    Chosen by priority: transient operations first (deploys and teardowns in
    flight), then terminal authoritative conclusions (succeeded or torn down)
    picked by recency, then everything else. This means a failed redeploy
    attempt won't hide the last successful deployment, while a completed
    teardown correctly supersedes a prior success.
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
    return (
        Deployment.objects.filter(app=app, status__in=Deployment.VISIBLE_STATUSES)
        .annotate(status_priority=status_priority)
        .order_by("status_priority", "-created_at")
        .first()
    )


def build_app_detail_context(request: HttpRequest, app: App) -> dict[str, Any]:
    """Build the shared context dict for app detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="workspaces")

    deployments = Deployment.objects.filter(app=app).order_by("-created_at")[:20]
    current_deployment = get_current_deployment(app=app)

    # Tags
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    can_edit = abac_service.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:edit")
    can_admin = abac_service.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:admin")

    context["app"] = app
    context["deployments"] = deployments
    context["current_deployment"] = current_deployment
    # Deployment Log tab: enabled once there's something to show (any logged deployment, or one
    # currently in flight). When a deployment is in progress we open that tab by default, so a
    # freshly started deploy lands straight on its live log instead of the Overview.
    latest_deployment = deployments[0] if deployments else None
    deploy_in_progress = latest_deployment is not None and latest_deployment.is_transient
    has_logs = DeploymentLog.objects.filter(deployment__app=app).exists()
    context["latest_deployment"] = latest_deployment
    context["log_tab_enabled"] = has_logs or deploy_in_progress
    context["initial_tab"] = "logs" if deploy_in_progress else "content"
    if latest_deployment is not None:
        # Address shown by the welcome + deploy-success dialogs. Computed here so
        # every renderer of app_detail.html (detail, redeploy, teardown) includes
        # the deploy-success watcher, not just the app_detail view.
        zone = app.environment.shared_alb_hosted_zone
        context["deploy_address"] = f"{app.slug}.{zone}" if zone else app.slug
    org = request.user.current_organization
    context["direct_tags"] = direct_tags
    context["inherited_tags"] = inherited_tags
    context["tags_json"] = json.dumps([{"key": t.key, "value": t.value} for t in direct_tags])
    is_pending_removal = app.status == App.Status.PENDING_REMOVAL
    context["can_edit"] = can_edit
    context["can_admin"] = can_admin
    context["is_pending_removal"] = is_pending_removal
    context["can_remove"] = can_edit and not is_pending_removal and not app_is_live(app)
    context["url_base"] = f"/apps/{app.slug}/tags/"
    context["suggested_keys"], context["suggested_values"] = abac_service.get_resource_tag_suggestions(org, "app")
    context.update(webapp_public_access.build_public_access_context(request=request, app=app))

    return context


@login_required
def app_detail(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Show app detail with configuration and deployments."""
    welcome_param = request.GET.get("welcome", "")
    if welcome_param not in ("1", "preview"):
        welcome_param = ""
    live_preview = settings.DEBUG and request.GET.get("live") == "preview"
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        params = [p for p in (f"welcome={welcome_param}" if welcome_param else "", "live=preview" if live_preview else "") if p]
        context["content_url"] = f"/apps/{app_slug}/" + (f"?{'&'.join(params)}" if params else "")
        return render(request, "humanityrules_app/app_shell.html", context=context)

    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    context = build_app_detail_context(request, app)
    # First-run welcome dialog: only when arriving from onboarding and the
    # deployment it announces is still running (a stale link shows the plain page).
    # ?welcome=preview (DEBUG only) forces it on any app with a deployment, kept
    # across reloads and never self-removing — for iterating on the dialog.
    latest_deployment = context["latest_deployment"]
    welcome_preview = settings.DEBUG and welcome_param == "preview"
    welcome = welcome_param == "1"
    context["show_welcome"] = latest_deployment is not None and (welcome_preview or (welcome and latest_deployment.is_transient))
    context["welcome_preview"] = welcome_preview
    # ?live=preview (DEBUG only): render the deploy-success dialog visible for iteration.
    context["live_preview"] = live_preview
    return render(request, "humanityrules_app/apps/app_detail.html", context=context)


@login_required
@require_POST
def app_deployment_teardown(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Trigger teardown for a deployment."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    if app.status == App.Status.PENDING_REMOVAL:
        return HttpResponse(status=422)

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
        return render(request, "humanityrules_app/apps/app_detail.html", context=context)

    context = {"app": app, "deployment": deployment, "mode": mode}
    return render(request, "humanityrules_app/apps/_app_deployment_row.html", context=context)


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
    return render(request, "humanityrules_app/apps/_app_deployment_row.html", context=context)


@login_required
@require_GET
def app_deployment_log(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Render the most recent deployment's log fragment; self-polls every 1s while transient."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    deployment = (
        Deployment.objects.filter(app=app)
        .order_by("-created_at")
        .first()
    )
    logs: list[DeploymentLog] = []
    truncated = False
    if deployment is not None:
        # Fetch newest-first so the cap keeps the tail, then reverse to chronological for display.
        recent = list(
            DeploymentLog.objects.filter(deployment=deployment).order_by("-created_at")[: MAX_DEPLOYMENT_LOG_LINES + 1]
        )
        truncated = len(recent) > MAX_DEPLOYMENT_LOG_LINES
        logs = list(reversed(recent[:MAX_DEPLOYMENT_LOG_LINES]))

    context = {
        "app": app,
        "deployment": deployment,
        "logs": logs,
        "truncated": truncated,
        "max_lines": MAX_DEPLOYMENT_LOG_LINES,
    }
    return render(request, "humanityrules_app/apps/_app_deployment_log.html", context=context)


@login_required
@require_GET
def app_environment_row_status(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return updated environment row inner HTML for self-terminating polling."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    current_deployment = get_current_deployment(app=app)
    if not current_deployment:
        return HttpResponse(status=404)

    context = {
        "app": app,
        "current_deployment": current_deployment,
    }
    return render(
        request,
        "humanityrules_app/apps/_app_environment_row.html#environment_row_content",
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
    return render(request, "humanityrules_app/partials/_app_card_status.html", {"app": app})


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
    return render(request, "humanityrules_app/apps/_app_teardown_confirm_modal.html", context=context)


@login_required
@require_POST
def app_deployment_redeploy(request: HttpRequest, app_slug: str, deployment_id: UUID) -> HttpResponse:
    """Create a new PENDING deployment to redeploy an app to the same environment."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    if app.status == App.Status.PENDING_REMOVAL:
        return HttpResponse(status=422)

    deployment = _get_deployment_for_app(app, deployment_id)

    if deployment.status not in (Deployment.Status.SUCCEEDED, Deployment.Status.FAILED, Deployment.Status.TORN_DOWN):
        return HttpResponse(status=422)

    if Deployment.objects.filter(app=app, status__in=Deployment.IN_PROGRESS_STATUSES).exists():
        return HttpResponse(status=422)

    git_ref = deployment.git_ref
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_ref = git_ref[:8] if len(git_ref) > 8 else git_ref
    image_tag = f"{app.slug}-{short_ref}-{timestamp}"

    Deployment.objects.create(
        app=app,
        git_ref=git_ref,
        image_tag=image_tag,
        status=Deployment.Status.PENDING,
        status_message="Redeploy triggered via web UI",
        created_by=request.user,
    )

    context = build_app_detail_context(request, app)
    return render(request, "humanityrules_app/apps/app_detail.html", context=context)


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
    return render(request, "humanityrules_app/apps/_app_tags.html", {
        "direct_tags": direct_tags, "inherited_tags": inherited_tags, "can_admin": True, "url_base": url_base,
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "app"))),
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
    return render(request, "humanityrules_app/apps/_app_tags.html", {
        "direct_tags": direct_tags, "inherited_tags": inherited_tags, "can_admin": True, "url_base": url_base,
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "app"))),
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
    suggested_keys, suggested_values = abac_service.get_resource_tag_suggestions(org, "app")
    return render(request, "humanityrules_app/partials/_security_tags_section.html", {
        "can_admin": True,
        "tags_title": "App Tags",
        "tags_json": json.dumps([{"key": t.key, "value": t.value} for t in tags]),
        "url_base": f"/apps/{app.slug}/tags/",
        "suggested_keys": suggested_keys,
        "suggested_values": suggested_values,
        "inherited_tags": inherited_tags,
        "empty_text": "No direct tags",
    })


@login_required
@require_GET
def app_remove_confirm(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the remove-app confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    if app_is_live(app):
        return HttpResponse(status=422)

    context = {
        "app": app,
        "post_url": reverse("app_remove", kwargs={"app_slug": app.slug}),
        "has_persistent_data": _app_has_persistent_data(app),
    }
    return render(request, "humanityrules_app/apps/_app_remove_confirm_modal.html", context=context)


@login_required
@require_POST
def app_remove(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Queue an AppRemovalJob; the job worker handles cleanup and the DB cascade delete."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    delete_all_data = request.POST.get("delete_all_data") == "on"
    with transaction.atomic():
        locked_app = get_object_or_404(
            App.objects.select_for_update().select_related("workspace"),
            id=app.id,
            organization=request.user.current_organization,
        )
        if locked_app.status == App.Status.PENDING_REMOVAL:
            return HttpResponse(status=422)
        if app_is_live(locked_app):
            return HttpResponse(status=422)

        environment = environment_operation_gate.lock_app_environment_for_removal(app_id=locked_app.id)
        if environment.status == Environment.Status.TEARING_DOWN:
            return HttpResponse(status=422)

        AppRemovalJob.objects.create(
            organization=request.user.current_organization,
            app_id_snapshot=locked_app.id,
            app_slug_snapshot=locked_app.slug,
            app_name_snapshot=locked_app.name,
            workspace_slug_snapshot=locked_app.workspace.slug,
            delete_secrets=delete_all_data,
            delete_persistent_data=_app_has_persistent_data(locked_app) and delete_all_data,
            delete_policies=delete_all_data,
            created_by=request.user,
        )
        locked_app.status = App.Status.PENDING_REMOVAL
        locked_app.save(update_fields=["status", "updated_at"])

    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse(
        "workspace_detail", kwargs={"workspace_slug": app.workspace.slug},
    )
    return response


@login_required
@require_GET
def app_cost_panel(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Render the cost-chart fragment for ``app_slug`` (org-scoped), enqueuing a recompute."""
    organization = request.user.current_organization
    app = get_object_or_404(App.objects.select_related("workspace"), slug=app_slug, organization=organization)
    if not abac_service.check_action(organization, request.user, app.workspace, "workspace", "workspace:view"):
        return HttpResponse(status=403)
    # Only a real page view (the shell's initial load) enqueues a recompute. The self-poll sends
    # ?await=1 and stays read-only, so polling can't spawn an endless chain of refresh jobs.
    if request.GET.get("await") != "1":
        cost_panel.enqueue_refresh(app=app)
    context = cost_panel.build_panel_context(app=app)
    return render(request, "humanityrules_app/apps/_app_cost_chart.html", context)
