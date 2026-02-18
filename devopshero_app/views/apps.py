from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from devopshero_app.models import App, Deployment

from .base import get_app_shell_context


def _get_app_for_user(request, app_slug):
    """Get an app that belongs to the current user's organization."""
    return get_object_or_404(
        App.objects.select_related("workspace", "repository", "datastore", "created_by"),
        slug=app_slug,
        organization=request.user.current_organization,
    )


def _get_deployment_for_app(app, deployment_id):
    """Get a deployment that belongs to the given app, with related environment."""
    return get_object_or_404(
        Deployment.objects.select_related("environment", "environment__aws_account"),
        id=deployment_id,
        app=app,
    )


def _build_app_detail_context(request, app):
    """Build the shared context dict for app detail rendering."""
    context = get_app_shell_context(request=request, current_page="workspaces")

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

    context["app"] = app
    context["deployments"] = deployments
    context["environment_rows"] = environment_rows
    context["secret_keys"] = secret_keys
    context["cpu_vcpu"] = cpu_vcpu

    return context


@login_required
def app_detail(request, app_slug):
    """Show app detail with configuration and deployments."""
    app = _get_app_for_user(request, app_slug)
    context = _build_app_detail_context(request, app)

    if request.htmx:
        return render(request, "devopshero_app/apps/app_detail.html", context=context)

    context["content_url"] = f"/apps/{app_slug}/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
@require_POST
def app_deployment_teardown(request, app_slug, deployment_id):
    """Trigger teardown for a deployment."""
    app = _get_app_for_user(request, app_slug)
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
def app_deployment_status(request, app_slug, deployment_id):
    """Return updated deployment row HTML for polling."""
    app = _get_app_for_user(request, app_slug)
    deployment = _get_deployment_for_app(app, deployment_id)

    mode = request.GET.get("mode", "")
    context = {"app": app, "deployment": deployment, "mode": mode}
    return render(request, "devopshero_app/apps/_app_deployment_row.html", context=context)


@login_required
@require_GET
def app_teardown_confirm(request, app_slug, deployment_id):
    """Return the teardown confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)
    deployment = _get_deployment_for_app(app, deployment_id)

    context = {"app": app, "deployment": deployment}
    return render(request, "devopshero_app/apps/_app_teardown_confirm_modal.html", context=context)


@login_required
@require_POST
def app_deployment_redeploy(request, app_slug, deployment_id):
    """Create a new PENDING deployment to redeploy an app to the same environment."""
    app = _get_app_for_user(request, app_slug)
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
