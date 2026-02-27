from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from devopshero_app.models import AWSAccount, Deployment, Environment

from .base import get_app_shell_context


@login_required
def environments(request):
    """List all environments in the current organization's AWS accounts."""
    context = get_app_shell_context(request=request, current_page="environments")

    environment_list = Environment.objects.filter(
        aws_account__organization=request.user.current_organization,
    ).select_related("aws_account").order_by("aws_account__name", "name")

    # Get AWS accounts for the "New Environment" modal
    aws_accounts = AWSAccount.objects.filter(
        organization=request.user.current_organization,
        status=AWSAccount.Status.CONNECTED,
    ).order_by("name")

    context["environments"] = environment_list
    context["aws_accounts"] = aws_accounts

    if request.htmx:
        return render(request, "devopshero_app/environments/environments.html", context=context)

    context["content_url"] = "/environments/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def environment_detail(request, environment_slug):
    """Show environment detail with deployments."""
    context = get_app_shell_context(request=request, current_page="environments")

    environment = get_object_or_404(
        Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    deployments = Deployment.objects.filter(
        environment=environment,
    ).select_related("app", "app__workspace").order_by("-created_at")[:20]

    context["environment"] = environment
    context["deployments"] = deployments

    if request.htmx:
        return render(request, "devopshero_app/environments/environment_detail.html", context=context)

    context["content_url"] = f"/environments/{environment_slug}/"
    return render(request, "devopshero_app/app_shell.html", context=context)
