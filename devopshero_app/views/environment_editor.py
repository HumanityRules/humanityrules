"""
Environment setup editor: two-panel UI with environment draft/progress + agent chat.

Entry points:
- New setup: /environments/new/?aws_account=<uuid> - creates an account-scoped setup conversation
- Existing environment: /environments/<uuid>/setup/ - resumes the environment setup task
"""

from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

import devopshero_app.models as models
from devopshero_app.services.agent import agent_service

from . import abac_view_checks
from . import base

DISCARDABLE_ENVIRONMENT_STATUSES = (
    models.Environment.Status.DRAFT,
    models.Environment.Status.ERROR,
)


def _load_aws_account(request: HttpRequest, aws_account_id: str) -> models.AWSAccount:
    """Load an AWS account for environment setup."""
    return get_object_or_404(
        models.AWSAccount,
        id=aws_account_id,
        organization=request.user.current_organization,
        status=models.AWSAccount.Status.CONNECTED,
    )


def _get_environment(request: HttpRequest, environment_id: UUID) -> models.Environment:
    """Load an environment for setup-editor access."""
    return get_object_or_404(
        models.Environment.objects.select_related("aws_account").exclude(status=models.Environment.Status.DISCARDED),
        id=environment_id,
        aws_account__organization=request.user.current_organization,
    )


def _create_environment_editor_conversation(
    request: HttpRequest,
    aws_account: models.AWSAccount,
    environment: models.Environment | None,
) -> models.Conversation:
    """Create a fresh environment-editor conversation for an AWS account and optional environment."""
    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=None,
        repo_id=None,
        aws_account_id=str(aws_account.id),
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        app_permission_request_id=None,
    )
    if environment:
        conversation.context_environment = environment
        conversation.save(update_fields=["context_environment", "updated_at"])
    return conversation


def _get_or_create_environment_editor_conversation(
    request: HttpRequest,
    environment: models.Environment,
) -> models.Conversation:
    """Return the active environment-editor conversation for an environment."""
    conversation = models.Conversation.objects.filter(
        context_environment=environment,
        user=request.user,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    ).order_by("-updated_at").first()
    if not conversation:
        return _create_environment_editor_conversation(
            request=request,
            aws_account=environment.aws_account,
            environment=environment,
        )

    update_fields: list[str] = []
    if conversation.context_aws_account_id != environment.aws_account_id:
        conversation.context_aws_account = environment.aws_account
        update_fields.append("context_aws_account")
    if conversation.status != models.Conversation.Status.ACTIVE:
        conversation.status = models.Conversation.Status.ACTIVE
        update_fields.append("status")
    if update_fields:
        update_fields.append("updated_at")
        conversation.save(update_fields=update_fields)
    return conversation


def _render_environment_editor(
    request: HttpRequest,
    aws_account: models.AWSAccount,
    conversation: models.Conversation,
    environment: models.Environment | None,
) -> HttpResponse:
    """Render the environment setup editor."""
    context = base.get_app_shell_context(request=request, current_page="environments")
    context.update({
        "aws_account": aws_account,
        "conversation": conversation,
        "environment": environment,
        "messages": conversation.messages.exclude(
            content_type=models.Message.ContentType.SYSTEM_TRIGGER,
        ).order_by("created_at"),
    })
    return render(request=request, template_name="devopshero_app/environments/environment_editor.html", context=context)


@login_required
def environment_editor_new(request: HttpRequest) -> HttpResponse:
    """Entry point for starting a new environment setup flow from an AWS account."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    aws_account_id = request.GET.get("aws_account", "").strip()
    if not aws_account_id:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["error_message"] = "Missing AWS account. Navigate here from the Environments page."
        return render(request=request, template_name="devopshero_app/environments/environment_editor.html", context=context)

    aws_account = _load_aws_account(request=request, aws_account_id=aws_account_id)
    conversation = _create_environment_editor_conversation(
        request=request,
        aws_account=aws_account,
        environment=None,
    )
    return _render_environment_editor(
        request=request,
        aws_account=aws_account,
        conversation=conversation,
        environment=None,
    )


@login_required
def environment_editor(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Resume the setup task for an existing environment."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    environment = _get_environment(request=request, environment_id=environment_id)
    conversation = _get_or_create_environment_editor_conversation(request=request, environment=environment)
    return _render_environment_editor(
        request=request,
        aws_account=environment.aws_account,
        conversation=conversation,
        environment=environment,
    )


@login_required
def environment_editor_environment_section(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Return the environment setup section partial for HTMX refresh in the editor."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    environment = _get_environment(request=request, environment_id=environment_id)
    return render(
        request=request,
        template_name="devopshero_app/environments/_environment_editor_setup_section.html",
        context={"environment": environment},
    )


def _reset_and_render_fresh_editor(request: HttpRequest, aws_account: models.AWSAccount) -> HttpResponse:
    """Create a fresh conversation and render the editor at the new-environment entry point."""
    fresh_conversation = _create_environment_editor_conversation(
        request=request,
        aws_account=aws_account,
        environment=None,
    )
    response = _render_environment_editor(
        request=request,
        aws_account=aws_account,
        conversation=fresh_conversation,
        environment=None,
    )
    new_url = f"{reverse('environment_editor_new')}?aws_account={aws_account.id}"
    response["HX-Replace-Url"] = new_url
    return response


@login_required
@require_POST
def environment_editor_reset(request: HttpRequest, environment_id: UUID) -> HttpResponse:
    """Close current conversation, discard draft environment if present, and start fresh."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    environment = _get_environment(request=request, environment_id=environment_id)

    if environment.status in DISCARDABLE_ENVIRONMENT_STATUSES:
        environment.status = models.Environment.Status.DISCARDED
        environment.status_message = "Draft discarded"
        environment.save(update_fields=["status", "status_message", "updated_at"])

    models.Conversation.objects.filter(
        context_environment=environment,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    ).update(
        status=models.Conversation.Status.ABANDONED,
        updated_at=timezone.now(),
    )

    return _reset_and_render_fresh_editor(request=request, aws_account=environment.aws_account)


@login_required
@require_POST
def environment_editor_reset_new(request: HttpRequest, conversation_id: UUID) -> HttpResponse:
    """Abandon the current pre-environment conversation and start fresh."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    conversation = get_object_or_404(
        models.Conversation.objects.select_related("context_aws_account"),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    )
    conversation.status = models.Conversation.Status.ABANDONED
    conversation.save(update_fields=["status", "updated_at"])

    return _reset_and_render_fresh_editor(request=request, aws_account=conversation.context_aws_account)
