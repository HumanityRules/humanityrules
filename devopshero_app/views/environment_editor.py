"""
Environment setup editor: two-panel UI with environment draft/progress + agent chat.

Entry points:
- New setup: /environments/new/?aws_account=<uuid> - creates an account-scoped setup conversation
- Existing environment: /environments/<uuid>/setup/ - resumes the environment setup task
"""

from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

import devopshero_app.models as models
from devopshero_app.services.agent import agent_service

from . import abac_view_checks
from . import base
from .environments import build_environments_context

DISCARDABLE_ENVIRONMENT_STATUSES = (
    models.Environment.Status.DRAFT,
    models.Environment.Status.ERROR,
)


def _get_editor_messages(conversation: models.Conversation) -> Any:
    """Return visible messages for the environment editor chat panel."""
    return conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")


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


def _reactivate_conversation(conversation: models.Conversation) -> models.Conversation:
    """Ensure a resumed environment conversation is active again."""
    update_fields = []
    if conversation.status != models.Conversation.Status.ACTIVE:
        conversation.status = models.Conversation.Status.ACTIVE
        update_fields.append("status")
    if update_fields:
        update_fields.append("updated_at")
        conversation.save(update_fields=update_fields)
    return conversation


def _create_account_scoped_conversation(request: HttpRequest, aws_account: models.AWSAccount) -> models.Conversation:
    """Create a fresh account-scoped environment conversation with no saved environment yet."""
    return agent_service.create_conversation(
        user=request.user,
        workspace_id=None,
        repo_id=None,
        aws_account_id=str(aws_account.id),
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        app_permission_request_id=None,
    )


def _get_resume_conversation(request: HttpRequest, environment: models.Environment) -> models.Conversation:
    """Return the user's latest conversation for an environment setup task."""
    conversation = models.Conversation.objects.filter(
        context_environment=environment,
        user=request.user,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    ).order_by("-updated_at").first()
    if conversation:
        if conversation.context_aws_account_id != environment.aws_account_id:
            conversation.context_aws_account = environment.aws_account
            conversation.save(update_fields=["context_aws_account", "updated_at"])
        return _reactivate_conversation(conversation=conversation)

    conversation = _create_account_scoped_conversation(request=request, aws_account=environment.aws_account)
    conversation.context_environment = environment
    conversation.save(update_fields=["context_environment", "updated_at"])
    return conversation


def _build_environment_section_context(
    aws_account: models.AWSAccount,
    conversation: models.Conversation,
    environment: models.Environment | None,
) -> dict[str, Any]:
    """Build shared context for environment section rendering."""
    return {
        "aws_account": aws_account,
        "conversation": conversation,
        "environment": environment,
    }


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
        "messages": _get_editor_messages(conversation=conversation),
    })
    context.update(_build_environment_section_context(
        aws_account=aws_account,
        conversation=conversation,
        environment=environment,
    ))
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
    conversation = _create_account_scoped_conversation(request=request, aws_account=aws_account)
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
    conversation = _get_resume_conversation(request=request, environment=environment)
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
    conversation = _get_resume_conversation(request=request, environment=environment)
    return render(
        request=request,
        template_name="devopshero_app/environments/_environment_editor_setup_section.html",
        context=_build_environment_section_context(
            aws_account=environment.aws_account,
            conversation=conversation,
            environment=environment,
        ),
    )


def _get_discardable_environment(conversation: models.Conversation) -> models.Environment | None:
    """Return the environment draft when it can still be discarded."""
    environment = conversation.context_environment
    if not environment:
        return None
    if environment.status not in DISCARDABLE_ENVIRONMENT_STATUSES:
        return None
    return environment


@login_required
@require_GET
def environment_editor_discard_draft_confirm(request: HttpRequest, conversation_id: UUID) -> HttpResponse:
    """Return the discard-draft confirmation modal HTML."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    conversation = get_object_or_404(
        models.Conversation.objects.select_related("context_environment", "context_aws_account"),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    )
    environment = _get_discardable_environment(conversation=conversation)

    if environment:
        modal_message = (
            f"Discard the current environment setup draft for {environment.name}? "
            "This abandons the draft and its conversation."
        )
    else:
        modal_message = "Abandon the current environment setup session? You can start a new setup later."

    return render(
        request=request,
        template_name="devopshero_app/partials/_confirm_modal.html",
        context={
            "modal_title": "Discard Environment Draft",
            "modal_message": modal_message,
            "confirm_url": reverse("environment_editor_discard_draft", kwargs={"conversation_id": conversation.id}),
            "confirm_label": "Discard Draft",
        },
    )


@login_required
@require_POST
def environment_editor_discard_draft(request: HttpRequest, conversation_id: UUID) -> HttpResponse:
    """Discard the environment draft or abandon the current setup session."""
    denied = abac_view_checks.require_org_admin(request)
    if denied:
        return denied

    conversation = get_object_or_404(
        models.Conversation.objects.select_related("context_environment"),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    )
    environment = _get_discardable_environment(conversation=conversation)

    if environment:
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
    else:
        conversation.status = models.Conversation.Status.ABANDONED
        conversation.save(update_fields=["status", "updated_at"])

    context = build_environments_context(request=request)
    response = render(request=request, template_name="devopshero_app/environments/environments.html", context=context)
    response["HX-Push-Url"] = reverse("environments")
    return response
