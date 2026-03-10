"""
Deployment editor view: two-panel UI with app/blueprint config + agent chat.

Entry points:
- New app: /deploy/new/?workspace=<slug>&repo=<id> - creates a repo-scoped deployment conversation
- Existing app new: /deploy/<app_slug>/new/ - starts a fresh app-scoped deployment conversation
- Existing app resume: /deploy/<app_slug>/resume/ - resumes the app's open deployment task
- Existing app fallback: /deploy/<app_slug>/ - preserves the current deployment task after app creation
"""

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .. import models
from ..services.agent import agent_service
from ..services import deployment_blueprint_effective_values
from . import abac_view_checks
from . import apps as apps_views
from . import base

DISCARDABLE_BLUEPRINT_STATUSES = (
    models.DeploymentBlueprint.Status.DRAFT,
    models.DeploymentBlueprint.Status.FAILED,
)


def _get_existing_app(request: HttpRequest, app_slug: str) -> models.App:
    """Load an existing app for deployment editor access."""
    organization = request.user.current_organization
    return get_object_or_404(
        models.App.objects.select_related("workspace", "repository"),
        organization=organization,
        slug=app_slug,
    )


def _get_editor_messages(conversation: models.Conversation):
    """Return visible messages for the deployment editor chat panel."""
    return conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")


def _build_blueprint_section_context(
    app: models.App | None,
    blueprint: models.DeploymentBlueprint | None,
) -> dict[str, Any]:
    """Build shared context for blueprint section rendering."""
    context: dict[str, Any] = {
        "app": app,
        "blueprint": blueprint,
        "blueprint_effective_values": None,
    }
    if app and blueprint:
        context["blueprint_effective_values"] = deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
            app=app,
            blueprint=blueprint,
        )
    return context


def _get_discardable_blueprint(app: models.App) -> models.DeploymentBlueprint | None:
    """Return the app's open blueprint when it can still be discarded."""
    blueprint = apps_views.get_open_blueprint(app=app)
    if not blueprint or blueprint.status not in DISCARDABLE_BLUEPRINT_STATUSES:
        return None
    return blueprint


def _render_existing_app_editor(
    request: HttpRequest,
    app: models.App,
    blueprint: models.DeploymentBlueprint | None,
    conversation: models.Conversation,
) -> HttpResponse:
    """Render the deployment editor for an existing app."""
    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context.update({
        "workspace": app.workspace,
        "repository": app.repository,
        "conversation": conversation,
        "messages": _get_editor_messages(conversation=conversation),
    })
    context.update(_build_blueprint_section_context(app=app, blueprint=blueprint))
    return render(request=request, template_name="devopshero_app/deploy/deployment_editor.html", context=context)


def _reactivate_conversation(conversation: models.Conversation) -> models.Conversation:
    """Ensure a resumed deployment conversation is active again."""
    update_fields = []
    if conversation.status != models.Conversation.Status.ACTIVE:
        conversation.status = models.Conversation.Status.ACTIVE
        update_fields.append("status")
    if update_fields:
        update_fields.append("updated_at")
        conversation.save(update_fields=update_fields)
    return conversation


def _create_app_scoped_conversation(request: HttpRequest, app: models.App) -> models.Conversation:
    """Create a fresh app-scoped deployment conversation with no blueprint yet."""
    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=str(app.workspace_id),
        repo_id=str(app.repository_id),
        aws_account_id=None,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
        app_permission_request_id=None,
    )
    conversation.context_app = app
    conversation.save(update_fields=["context_app", "updated_at"])
    return conversation


def _get_latest_app_conversation_without_blueprint(request: HttpRequest, app: models.App) -> models.Conversation | None:
    """Return the latest app-scoped deployment conversation that has no blueprint yet."""
    conversation = models.Conversation.objects.filter(
        context_app=app,
        context_deployment_blueprint__isnull=True,
        user=request.user,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
    ).order_by("-updated_at").first()
    if not conversation:
        return None
    return _reactivate_conversation(conversation=conversation)


def _get_resume_conversation(
    request: HttpRequest,
    app: models.App,
    blueprint: models.DeploymentBlueprint,
) -> models.Conversation:
    """Return the user's latest conversation for the open blueprint, creating one if needed."""
    conversation = models.Conversation.objects.filter(
        context_deployment_blueprint=blueprint,
        user=request.user,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
    ).order_by("-updated_at").first()
    if conversation:
        if conversation.context_app_id != app.id:
            conversation.context_app = app
            conversation.save(update_fields=["context_app", "updated_at"])
        return _reactivate_conversation(conversation=conversation)

    conversation = _create_app_scoped_conversation(request=request, app=app)
    conversation.context_deployment_blueprint = blueprint
    conversation.save(update_fields=["context_deployment_blueprint", "updated_at"])
    return conversation


@login_required
def deployment_editor_new(request: HttpRequest) -> HttpResponse:
    """Entry point for deploying a new app from a repository."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    workspace_slug = request.GET.get("workspace", "").strip()
    repo_id = request.GET.get("repo", "").strip()

    if not workspace_slug or not repo_id:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["error_message"] = "Missing workspace or repository. Navigate here from a workspace."
        return render(request=request, template_name="devopshero_app/deploy/deployment_editor.html", context=context)

    workspace = get_object_or_404(models.Workspace, slug=workspace_slug, organization=organization)

    denied = abac_view_checks.check_abac(request, workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    repository = get_object_or_404(models.Repository, id=repo_id, organization=organization)

    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=str(workspace.id),
        repo_id=str(repository.id),
        aws_account_id=None,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
        app_permission_request_id=None,
    )

    messages = conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context.update({
        "workspace": workspace,
        "repository": repository,
        "conversation": conversation,
        "messages": messages,
    })
    context.update(_build_blueprint_section_context(app=None, blueprint=None))

    return render(request=request, template_name="devopshero_app/deploy/deployment_editor.html", context=context)


@login_required
def deployment_editor(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Backward-compatible existing-app deployment entrypoint."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    open_blueprint = apps_views.get_open_blueprint(app=app)
    if open_blueprint:
        conversation = _get_resume_conversation(request=request, app=app, blueprint=open_blueprint)
        return _render_existing_app_editor(
            request=request,
            app=app,
            blueprint=open_blueprint,
            conversation=conversation,
        )

    conversation = _get_latest_app_conversation_without_blueprint(request=request, app=app)
    if not conversation:
        conversation = _create_app_scoped_conversation(request=request, app=app)

    return _render_existing_app_editor(
        request=request,
        app=app,
        blueprint=None,
        conversation=conversation,
    )


@login_required
def deployment_editor_app_new(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Start a fresh deployment conversation for an existing app."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    open_blueprint = apps_views.get_open_blueprint(app=app)
    if open_blueprint:
        conversation = _get_resume_conversation(request=request, app=app, blueprint=open_blueprint)
        response = _render_existing_app_editor(
            request=request,
            app=app,
            blueprint=open_blueprint,
            conversation=conversation,
        )
        response["HX-Push-Url"] = reverse("deployment_editor_resume", kwargs={"app_slug": app.slug})
        return response

    conversation = _create_app_scoped_conversation(request=request, app=app)
    return _render_existing_app_editor(
        request=request,
        app=app,
        blueprint=None,
        conversation=conversation,
    )


@login_required
def deployment_editor_resume(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Resume the current deployment task for an existing app."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    open_blueprint = apps_views.get_open_blueprint(app=app)
    if not open_blueprint:
        conversation = _get_latest_app_conversation_without_blueprint(request=request, app=app)
        if not conversation:
            conversation = _create_app_scoped_conversation(request=request, app=app)
        response = _render_existing_app_editor(
            request=request,
            app=app,
            blueprint=None,
            conversation=conversation,
        )
        response["HX-Push-Url"] = reverse("deployment_editor_app_new", kwargs={"app_slug": app.slug})
        return response

    conversation = _get_resume_conversation(request=request, app=app, blueprint=open_blueprint)
    return _render_existing_app_editor(
        request=request,
        app=app,
        blueprint=open_blueprint,
        conversation=conversation,
    )


@login_required
def deployment_editor_app_section(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the app section partial for HTMX refresh in the editor."""
    organization = request.user.current_organization
    app = get_object_or_404(
        models.App.objects.select_related("repository", "workspace"),
        organization=organization,
        slug=app_slug,
    )
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied
    return render(request=request, template_name="devopshero_app/deploy/_app_section.html", context={"app": app})


@login_required
def deployment_editor_blueprint_section(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the open blueprint section partial for HTMX refresh in the editor."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied
    blueprint = apps_views.get_open_blueprint(app=app)
    return render(
        request=request,
        template_name="devopshero_app/deploy/_blueprint_section.html",
        context=_build_blueprint_section_context(app=app, blueprint=blueprint),
    )


@login_required
@require_GET
def deployment_editor_discard_draft_confirm(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the discard-draft confirmation modal HTML."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    blueprint = _get_discardable_blueprint(app=app)

    if blueprint:
        modal_message = (
            f"Discard the current deployment draft for {blueprint.environment.name}? "
            "This abandons the draft blueprint and its conversation."
        )
    else:
        modal_message = "Abandon the current deployment session? You can start a new deployment later."

    return render(
        request=request,
        template_name="devopshero_app/partials/_confirm_modal.html",
        context={
            "modal_title": "Discard Deployment Draft",
            "modal_message": modal_message,
            "confirm_url": reverse("deployment_editor_discard_draft", kwargs={"app_slug": app.slug}),
            "confirm_label": "Discard Draft",
        },
    )


@login_required
@require_POST
def deployment_editor_discard_draft(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Discard the app's open draft or failed blueprint and return to app detail."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    blueprint = _get_discardable_blueprint(app=app)

    if blueprint:
        blueprint.status = models.DeploymentBlueprint.Status.DISCARDED
        blueprint.status_message = "Draft discarded"
        blueprint.save(update_fields=["status", "status_message", "updated_at"])

        models.Conversation.objects.filter(
            context_deployment_blueprint=blueprint,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
        ).update(
            status=models.Conversation.Status.ABANDONED,
            updated_at=timezone.now(),
        )
    else:
        models.Conversation.objects.filter(
            context_app=app,
            context_deployment_blueprint__isnull=True,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
            status=models.Conversation.Status.ACTIVE,
        ).update(
            status=models.Conversation.Status.ABANDONED,
            updated_at=timezone.now(),
        )

    context = apps_views.build_app_detail_context(request=request, app=app)
    response = render(request=request, template_name="devopshero_app/apps/app_detail.html", context=context)
    response["HX-Push-Url"] = reverse("app_detail", kwargs={"app_slug": app.slug})
    return response
