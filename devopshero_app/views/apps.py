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


@login_required
def app_detail(request, app_slug):
    """Show app detail with configuration and deployments."""
    context = get_app_shell_context(request=request, current_page="workspaces")

    app = _get_app_for_user(request, app_slug)

    deployments = Deployment.objects.filter(
        app=app,
    ).select_related("environment", "environment__aws_account").order_by("-created_at")[:20]

    secret_keys = list(app.app_secrets.keys()) if app.app_secrets else []
    cpu_vcpu = app.cpu / 1024

    context["app"] = app
    context["deployments"] = deployments
    context["secret_keys"] = secret_keys
    context["cpu_vcpu"] = cpu_vcpu

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
        Deployment.Status.RUNNING,
        Deployment.Status.FAILED,
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

    try:
        deployment = Deployment.objects.select_related(
            "environment", "environment__aws_account"
        ).get(id=deployment_id, app=app)
    except Deployment.DoesNotExist:
        # Deployment was deleted (teardown succeeded) — remove the row
        return HttpResponse("")

    context = {"app": app, "deployment": deployment}
    return render(request, "devopshero_app/apps/_app_deployment_row.html", context=context)


@login_required
@require_GET
def app_teardown_confirm(request, app_slug, deployment_id):
    """Return the teardown confirmation modal HTML."""
    app = _get_app_for_user(request, app_slug)
    deployment = _get_deployment_for_app(app, deployment_id)

    context = {"app": app, "deployment": deployment}
    return render(request, "devopshero_app/apps/_app_teardown_confirm_modal.html", context=context)
