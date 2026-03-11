"""
Environment editor view: two-panel UI with environment info + agent chat.

Entry points:
- New environment: /environments/new/?aws_account=<id> - creates an AWS-account-scoped conversation
- Existing environment: /environments/<slug>/ - shows environment detail with chat
- Environment section partial: /environments/<slug>/environment-section/ - HTMX refresh
"""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .. import models
from ..services.agent import agent_service
from . import abac_view_checks
from . import base


def _get_editor_messages(conversation: models.Conversation):
    """Return visible messages for the environment editor chat panel."""
    return conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")


def _build_environment_section_context(
    aws_account: models.AWSAccount,
    environment: models.Environment | None,
) -> dict[str, Any]:
    """Build shared context for environment section rendering."""
    return {
        "aws_account": aws_account,
        "environment": environment,
    }


def _reactivate_conversation(conversation: models.Conversation) -> models.Conversation:
    """Ensure a resumed environment conversation is active again."""
    if conversation.status != models.Conversation.Status.ACTIVE:
        conversation.status = models.Conversation.Status.ACTIVE
        conversation.save(update_fields=["status", "updated_at"])
    return conversation


def _get_environment_conversation(
    request: HttpRequest,
    environment: models.Environment,
) -> models.Conversation:
    """Find or create a conversation for the given environment."""
    conversation = models.Conversation.objects.filter(
        context_environment=environment,
        user=request.user,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
    ).order_by("-updated_at").first()
    if conversation:
        return _reactivate_conversation(conversation=conversation)

    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=None,
        repo_id=None,
        aws_account_id=str(environment.aws_account_id),
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        app_permission_request_id=None,
    )
    conversation.context_environment = environment
    conversation.save(update_fields=["context_environment", "updated_at"])
    return conversation


def _get_aws_account_conversation(
    request: HttpRequest,
    aws_account: models.AWSAccount,
) -> models.Conversation:
    """Find an existing account-scoped conversation with no environment yet, or create one."""
    conversation = models.Conversation.objects.filter(
        context_aws_account=aws_account,
        context_environment__isnull=True,
        user=request.user,
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        status=models.Conversation.Status.ACTIVE,
    ).order_by("-updated_at").first()
    if conversation:
        return conversation

    return agent_service.create_conversation(
        user=request.user,
        workspace_id=None,
        repo_id=None,
        aws_account_id=str(aws_account.id),
        mode=models.Conversation.Mode.ENVIRONMENT_SETUP,
        app_permission_request_id=None,
    )


@login_required
def environment_editor_new(request: HttpRequest) -> HttpResponse:
    """Entry point for provisioning a new environment from an AWS account."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    aws_account_id = request.GET.get("aws_account", "").strip()

    if not aws_account_id:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["error_message"] = "Missing AWS account. Navigate here from the Environments page."
        return render(request=request, template_name="devopshero_app/environments/environment_editor.html", context=context)

    aws_account = get_object_or_404(models.AWSAccount, id=aws_account_id, organization=organization)

    conversation = _get_aws_account_conversation(request=request, aws_account=aws_account)

    context = base.get_app_shell_context(request=request, current_page="environments")
    context.update({
        "conversation": conversation,
        "messages": _get_editor_messages(conversation=conversation),
    })
    context.update(_build_environment_section_context(aws_account=aws_account, environment=None))

    return render(request=request, template_name="devopshero_app/environments/environment_editor.html", context=context)


@login_required
def environment_editor(request: HttpRequest, environment_slug: str) -> HttpResponse:
    """Environment detail with integrated chat panel."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="environments")
        context["content_url"] = f"/environments/{environment_slug}/"
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    environment = get_object_or_404(
        models.Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    conversation = _get_environment_conversation(request=request, environment=environment)

    deployments = models.Deployment.objects.filter(
        environment=environment,
    ).select_related("app", "app__workspace").order_by("-created_at")[:10]

    context = base.get_app_shell_context(request=request, current_page="environments")
    context.update({
        "conversation": conversation,
        "messages": _get_editor_messages(conversation=conversation),
        "deployments": deployments,
    })
    context.update(_build_environment_section_context(aws_account=environment.aws_account, environment=environment))

    return render(request=request, template_name="devopshero_app/environments/environment_editor.html", context=context)


@login_required
def environment_editor_environment_section(request: HttpRequest, environment_slug: str) -> HttpResponse:
    """Return the environment section partial for HTMX refresh."""
    environment = get_object_or_404(
        models.Environment.objects.select_related("aws_account"),
        slug=environment_slug,
        aws_account__organization=request.user.current_organization,
    )

    denied = abac_view_checks.check_abac(request, environment, "environment", "environment:view")
    if denied:
        return denied

    deployments = models.Deployment.objects.filter(
        environment=environment,
    ).select_related("app", "app__workspace").order_by("-created_at")[:10]

    return render(
        request=request,
        template_name="devopshero_app/environments/_environment_section.html",
        context={
            "aws_account": environment.aws_account,
            "environment": environment,
            "deployments": deployments,
        },
    )
