import json
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

import devopshero_app.models as models
from devopshero_app.services import abac_service

from . import abac_view_checks
from . import base

SETUP_EDITOR_STATUSES = {
    models.Environment.Status.DRAFT,
    models.Environment.Status.PENDING,
    models.Environment.Status.PROVISIONING,
    models.Environment.Status.ERROR,
}


def _get_environment_for_user(request: HttpRequest, environment_id: UUID) -> models.Environment:
    """Load an environment that belongs to the current organization."""
    return get_object_or_404(
        models.Environment.objects.select_related("aws_account").exclude(status=models.Environment.Status.DISCARDED),
        id=environment_id,
        aws_account__organization=request.user.current_organization,
    )


def populate_environment_entrypoint(environment: models.Environment, user_is_org_admin: bool) -> models.Environment:
    """Attach the primary navigation target for an environment card."""
    if user_is_org_admin and environment.status in SETUP_EDITOR_STATUSES:
        environment.primary_url = reverse("environment_editor", kwargs={"environment_id": environment.id})
    else:
        environment.primary_url = reverse("environment_detail", kwargs={"environment_id": environment.id})
    return environment


def build_environments_context(request: HttpRequest) -> dict[str, object]:
    """Build the shared context for the environments index page."""
    context = base.get_app_shell_context(request=request, current_page="environments")
    user_is_org_admin = context["user_is_org_admin"]

    environment_list = (
        models.Environment.objects
        .filter(aws_account__organization=request.user.current_organization)
        .exclude(status=models.Environment.Status.DISCARDED)
        .select_related("aws_account")
        .order_by("aws_account__name", "name")
    )
    environment_list = abac_service.filter_permitted_resources(
        request.user.current_organization,
        request.user,
        environment_list,
        "environment",
        "environment:view",
    )
    environment_list = [
        populate_environment_entrypoint(environment=environment, user_is_org_admin=user_is_org_admin)
        for environment in environment_list
    ]

    aws_accounts = models.AWSAccount.objects.filter(
        organization=request.user.current_organization,
        status=models.AWSAccount.Status.CONNECTED,
    ).order_by("name")

    context["environments"] = environment_list
    context["aws_accounts"] = aws_accounts
    return context


def build_environment_detail_context(request: HttpRequest, environment: models.Environment) -> dict[str, object]:
    """Build the shared context for environment detail rendering."""
    context = base.get_app_shell_context(request=request, current_page="environments")
    deployments = models.Deployment.objects.filter(
        environment=environment,
    ).select_related("app", "app__workspace").order_by("-created_at")[:20]
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    org = request.user.current_organization
    can_admin = abac_service.check_action(org, request.user, environment, "environment", "environment:admin")

    context["environment"] = environment
    context["deployments"] = deployments
    context["tags"] = tags
    context["tags_json"] = json.dumps([{"key": tag.key, "value": tag.value} for tag in tags])
    context["can_admin"] = can_admin
    context["url_base"] = f"/environments/{environment.id}/tags/"
    context["suggested_keys"], context["suggested_values"] = abac_service.get_resource_tag_suggestions(org, "environment")
    return context


@login_required
def environments(request: HttpRequest) -> HttpResponse:
    """List all environments in the current organization's AWS accounts."""
    context = build_environments_context(request=request)

    if request.htmx:
        return render(request, "devopshero_app/environments/environments.html", context=context)

    context["content_url"] = "/environments/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def environment_detail(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Show environment detail with deployments."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    context = build_environment_detail_context(request=request, environment=environment)

    if request.htmx:
        return render(request, "devopshero_app/environments/environment_detail.html", context=context)

    context["content_url"] = f"/environments/{environment.id}/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def environment_teardown_confirm(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Return the environment teardown confirmation modal HTML."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    return render(request, "devopshero_app/partials/_confirm_modal.html", {
        "modal_title": "Tear Down Environment",
        "modal_message": f'Are you sure you want to tear down "{environment.name}"? This will destroy all deployments in the environment and delete the underlying infrastructure (VPC, ECS cluster). This action cannot be undone.',
        "confirm_url": f"/environments/{environment.id}/teardown/",
        "confirm_label": "Tear Down",
    })


@login_required
@require_POST
def environment_teardown(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Queue teardown for an environment."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    teardownable_statuses = {
        models.Environment.Status.READY,
        models.Environment.Status.ERROR,
    }
    if environment.status not in teardownable_statuses:
        return HttpResponse(status=422)

    environment.status = models.Environment.Status.TEARDOWN_PENDING
    environment.status_message = "Teardown triggered via web UI"
    environment.save(update_fields=["status", "status_message", "updated_at"])

    context = build_environment_detail_context(request=request, environment=environment)
    return render(request, "devopshero_app/environments/environment_detail.html", context=context)


@login_required
@require_POST
def environment_tag_add(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Add a tag to an environment. Returns updated tag partial."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    key = request.POST.get("key", "").strip()
    value = request.POST.get("value", "").strip()
    if key and value:
        models.ResourceTag.objects.get_or_create(
            organization=request.user.current_organization,
            resource_type="environment",
            environment=environment,
            key=key,
            value=value,
        )

    org = request.user.current_organization
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    url_base = f"/environments/{environment.id}/tags/"
    return render(request, "devopshero_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#environment-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "environment"))),
    })


@login_required
@require_POST
def environment_tag_remove(request: HttpRequest, environment_id: UUID, tag_id: UUID) -> HttpResponse:
    """Remove a tag from an environment. Returns updated tag partial."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    models.ResourceTag.objects.filter(id=tag_id, environment=environment).delete()

    org = request.user.current_organization
    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    url_base = f"/environments/{environment.id}/tags/"
    return render(request, "devopshero_app/partials/_kv_tag_editor.html", {
        "items": tags, "can_edit": True, "url_base": url_base, "hx_target": "#environment-tags", "empty_text": "No tags",
        **dict(zip(("suggested_keys", "suggested_values"), abac_service.get_resource_tag_suggestions(org, "environment"))),
    })


@login_required
@require_POST
def environment_tags_save(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Bulk-save environment tags. Replaces all existing tags with the submitted array."""
    environment = _get_environment_for_user(request=request, environment_id=environment_id)

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:admin")
    if denied:
        return denied

    org = request.user.current_organization
    tags_data = json.loads(request.POST.get("tags", "[]"))

    models.ResourceTag.objects.filter(environment=environment).delete()
    seen = set()
    for tag in tags_data:
        key = tag.get("key", "").strip()
        value = tag.get("value", "").strip()
        if key and value and (key, value) not in seen:
            seen.add((key, value))
            models.ResourceTag.objects.create(
                organization=org, resource_type="environment", environment=environment,
                key=key, value=value,
            )

    tags = models.ResourceTag.objects.filter(environment=environment).order_by("key", "value")
    suggested_keys, suggested_values = abac_service.get_resource_tag_suggestions(org, "environment")
    return render(request, "devopshero_app/partials/_security_tags_section.html", {
        "can_admin": True,
        "tags_title": "Environment Tags",
        "tags_json": json.dumps([{"key": t.key, "value": t.value} for t in tags]),
        "url_base": f"/environments/{environment.id}/tags/",
        "suggested_keys": suggested_keys,
        "suggested_values": suggested_values,
    })
