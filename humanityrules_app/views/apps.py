import json
from typing import Any

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from humanityrules_app.models import App, DeploymentLog, DeploymentRecord, Environment, ResourceTag
from humanityrules_app.services import abac_service
from humanityrules_app.services.cost import panel as cost_panel
from humanityrules_app.services.jobs import app_job_service

from . import abac_view_checks
from . import base
from . import webapp_public_access

# Cap rendered log lines; the deployment-log fragment re-renders every second while polling.
MAX_DEPLOYMENT_LOG_LINES = 1000

# History rows shown on the app detail page.
MAX_DEPLOYMENT_RECORDS = 30


def _app_removable(app: App) -> bool:
    """An app can be removed only while idle with no infra possibly behind it."""
    return (
        app.environment.status == Environment.Status.READY
        and app.job_status == App.JobStatus.IDLE
        and not app.may_have_infra
    )


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


def build_app_detail_context(request: HttpRequest, app: App) -> dict[str, Any]:
    """Build the shared context dict for app detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="dashboard")

    deployment_records = (
        DeploymentRecord.objects.filter(app=app).select_related("created_by")[:MAX_DEPLOYMENT_RECORDS]
    )

    # Tags
    direct_tags = ResourceTag.objects.filter(app=app).order_by("key", "value")
    inherited_tags = ResourceTag.objects.filter(workspace=app.workspace).order_by("key", "value")
    can_edit = abac_service.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:edit")
    can_admin = abac_service.check_action(request.user.current_organization, request.user, app.workspace, "workspace", "workspace:admin")

    context["app"] = app
    context["deployment_records"] = deployment_records
    # Deployment Log tab: enabled once there's something to show (any logged attempt, or one
    # currently in flight). When a job is in flight we open that tab by default, so a
    # freshly started deploy lands straight on its live log instead of the Overview.
    has_logs = DeploymentLog.objects.filter(app=app).exists()
    context["log_tab_enabled"] = has_logs or app.job_in_flight
    context["initial_tab"] = "logs" if app.job_in_flight else "content"
    if app.last_attempt_id is not None:
        # Address shown by the welcome + deploy-success dialogs. Computed here so
        # every renderer of app_detail.html (detail, redeploy, teardown) includes
        # the deploy-success watcher, not just the app_detail view.
        zone = app.environment.shared_alb_hosted_zone
        context["deploy_address"] = f"{app.slug}.{zone}" if zone else app.slug
    org = request.user.current_organization
    context["direct_tags"] = direct_tags
    context["inherited_tags"] = inherited_tags
    context["tags_json"] = json.dumps([{"key": t.key, "value": t.value} for t in direct_tags])
    context["can_edit"] = can_edit
    context["can_admin"] = can_admin
    context["is_pending_removal"] = app.is_pending_removal
    context["can_remove"] = can_edit and not app.is_pending_removal and _app_removable(app)
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
        context = base.get_app_shell_context(request=request, current_page="dashboard")
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
    # ?welcome=preview (DEBUG only) forces it on any app with an attempt, kept
    # across reloads and never self-removing — for iterating on the dialog.
    welcome_preview = settings.DEBUG and welcome_param == "preview"
    welcome = welcome_param == "1"
    has_attempt = app.last_attempt_id is not None
    context["show_welcome"] = has_attempt and (welcome_preview or (welcome and app.job_in_flight))
    context["welcome_preview"] = welcome_preview
    # ?live=preview (DEBUG only): render the deploy-success dialog visible for iteration.
    context["live_preview"] = live_preview
    return render(request, "humanityrules_app/apps/app_detail.html", context=context)


@login_required
@require_POST
def app_deployment_teardown(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Queue a teardown attempt for the app's live infra."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    try:
        app_job_service.queue_teardown(app=app, created_by=request.user, label=None)
    except app_job_service.AppJobAdmissionError:
        return HttpResponse(status=422)

    context = build_app_detail_context(request=request, app=_get_app_for_user(request, app_slug))
    return render(request, "humanityrules_app/apps/app_detail.html", context=context)


@login_required
@require_GET
def app_status_row(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return an updated app status row for the workspace/environment lists."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    mode = request.GET.get("mode", "")
    context = {"app": app, "mode": mode}
    return render(request, "humanityrules_app/apps/_app_status_row.html", context=context)


@login_required
@require_GET
def app_deployment_log(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Render the most recent deployment's log fragment; self-polls every 1s while transient."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    logs: list[DeploymentLog] = []
    truncated = False
    if app.last_attempt_id is not None:
        # Fetch newest-first so the cap keeps the tail, then reverse to chronological for display.
        recent = list(
            DeploymentLog.objects.filter(app=app, attempt_id=app.last_attempt_id)
            .order_by("-created_at")[: MAX_DEPLOYMENT_LOG_LINES + 1]
        )
        truncated = len(recent) > MAX_DEPLOYMENT_LOG_LINES
        logs = list(reversed(recent[:MAX_DEPLOYMENT_LOG_LINES]))

    context = {
        "app": app,
        "logs": logs,
        "truncated": truncated,
        "max_lines": MAX_DEPLOYMENT_LOG_LINES,
    }
    return render(request, "humanityrules_app/apps/_app_deployment_log.html", context=context)


@login_required
@require_GET
def app_deployment_section_status(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return updated deployment section inner HTML for self-terminating polling."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    context = {"app": app}
    return render(
        request,
        "humanityrules_app/apps/_app_deployment_section.html#deployment_section",
        context=context,
    )


@login_required
@require_GET
def app_card_status(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return updated app card status pill for polling."""
    app = get_object_or_404(
        App,
        slug=app_slug,
        organization=request.user.current_organization,
    )
    return render(request, "humanityrules_app/partials/_app_card_status.html", {"app": app})


@login_required
@require_GET
def app_teardown_confirm(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the teardown confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:view")
    if denied:
        return denied

    context = {
        "app": app,
        "post_url": reverse("app_deployment_teardown", kwargs={"app_slug": app.slug}),
    }
    return render(request, "humanityrules_app/apps/_app_teardown_confirm_modal.html", context=context)


@login_required
@require_POST
def app_deployment_redeploy(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Queue a fresh deploy attempt for the app."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    try:
        app_job_service.queue_deploy(app=app, created_by=request.user)
    except app_job_service.AppJobAdmissionError:
        return HttpResponse(status=422)

    context = build_app_detail_context(request, _get_app_for_user(request, app_slug))
    return render(request, "humanityrules_app/apps/app_detail.html", context=context)


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

    if not _app_removable(app):
        return HttpResponse(status=422)

    context = {
        "app": app,
        "post_url": reverse("app_remove", kwargs={"app_slug": app.slug}),
        "has_persistent_data": _app_has_persistent_data(app),
        "is_sandbox": app.environment.aws_account.is_humr_sandbox,
    }
    return render(request, "humanityrules_app/apps/_app_remove_confirm_modal.html", context=context)


@login_required
@require_POST
def app_remove(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Queue a removal attempt; the job worker handles cleanup and the DB cascade delete."""
    app = _get_app_for_user(request, app_slug)

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    # Sandbox slugs are reusable across orgs, so a released slug must never leave data behind:
    # the full-purge choice is mandatory, not a user checkbox.
    is_sandbox = app.environment.aws_account.is_humr_sandbox
    delete_all_data = is_sandbox or request.POST.get("delete_all_data") == "on"
    try:
        app_job_service.queue_removal(
            app=app,
            created_by=request.user,
            delete_all_data=delete_all_data,
            teardown_first=False,
            label=None,
        )
    except app_job_service.AppJobAdmissionError:
        return HttpResponse(status=422)

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
