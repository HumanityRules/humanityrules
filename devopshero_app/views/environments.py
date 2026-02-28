from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from devopshero_app.models import AWSAccount, Deployment, Environment, ResourceTag
from devopshero_app.services import abac

from . import abac_view_checks
from . import base


@login_required
def environments(request: HttpRequest) -> HttpResponse:
    """List all environments in the current organization's AWS accounts."""
    context = base.get_app_shell_context(request=request, current_page="environments")

    environment_list = Environment.objects.filter(
        aws_account__organization=request.user.current_organization,
    ).select_related("aws_account").order_by("aws_account__name", "name")
    environment_list = abac.filter_permitted_resources(
        request.user.current_organization, request.user, environment_list, "environment", "environment:view",
    )

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
def environment_detail(request: HttpRequest, environment_slug: str) -> HttpResponse:
    """Show environment detail with deployments."""
    context = base.get_app_shell_context(request=request, current_page="environments")

    environment = get_object_or_404(
        Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    deployments = Deployment.objects.filter(
        environment=environment,
    ).select_related("app", "app__workspace").order_by("-created_at")[:20]

    tags = ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    can_admin = abac.check_action(request.user.current_organization, request.user, environment, "environment", "environment:admin")

    context["environment"] = environment
    context["deployments"] = deployments
    org = request.user.current_organization
    context["tags"] = tags
    context["can_admin"] = can_admin
    context["suggested_keys"] = abac.get_tag_suggestion_keys(org)
    context["suggested_values"] = abac.get_tag_suggestion_values(org)

    if request.htmx:
        return render(request, "devopshero_app/environments/environment_detail.html", context=context)

    context["content_url"] = f"/environments/{environment_slug}/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
@require_POST
def environment_tag_add(request: HttpRequest, environment_slug: str) -> HttpResponse:
    """Add a tag to an environment. Returns updated tag partial."""
    environment = get_object_or_404(
        Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        ResourceTag.objects.get_or_create(
            organization=request.user.current_organization,
            resource_type="environment",
            environment=environment,
            key=key,
            value=value,
        )

    org = request.user.current_organization
    tags = ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    return render(request, "devopshero_app/environments/_environment_tags.html", {
        "tags": tags, "environment": environment, "can_admin": True,
        "suggested_keys": abac.get_tag_suggestion_keys(org), "suggested_values": abac.get_tag_suggestion_values(org),
    })


@login_required
@require_POST
def environment_tag_remove(request: HttpRequest, environment_slug: str, tag_id: UUID) -> HttpResponse:
    """Remove a tag from an environment. Returns updated tag partial."""
    environment = get_object_or_404(
        Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    ResourceTag.objects.filter(id=tag_id, environment=environment).delete()

    org = request.user.current_organization
    tags = ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    return render(request, "devopshero_app/environments/_environment_tags.html", {
        "tags": tags, "environment": environment, "can_admin": True,
        "suggested_keys": abac.get_tag_suggestion_keys(org), "suggested_values": abac.get_tag_suggestion_values(org),
    })
