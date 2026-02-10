from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from devopshero_app.models import App, Deployment

from .base import get_app_shell_context


@login_required
def app_detail(request, app_slug):
    """Show app detail with configuration and deployments."""
    context = get_app_shell_context(request=request, current_page="workspaces")

    app = get_object_or_404(
        App.objects.select_related("workspace", "repository", "datastore", "created_by"),
        slug=app_slug,
        organization=request.user.current_organization,
    )

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
