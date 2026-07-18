"""
Deployment editor view: two-panel UI with app/blueprint config + agent chat.

Entry points:
- New app: /deploy/new/<workspace_slug>/<repo_id>/ - creates a repo-scoped deployment conversation
- Existing app: /deploy/<app_slug>/ - resumes or creates the app's deployment conversation
- Reset: /deploy/<app_slug>/reset/ - closes current conversation, discards draft, starts fresh
"""

from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
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


def _build_blueprint_section_context(
    app: models.App | None,
    blueprint: models.DeploymentBlueprint | None,
) -> dict[str, Any]:
    """Build shared context for blueprint section rendering."""
    context: dict[str, Any] = {
        "app": app,
        "blueprint": blueprint,
        "blueprint_error": None,
        "blueprint_effective_values": None,
    }
    if app and blueprint:
        try:
            context["blueprint_effective_values"] = deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
                app=app,
                blueprint=blueprint,
            )
        except ValueError as exc:
            context["blueprint_error"] = str(exc)
    return context


def _render_deployment_editor(
    request: HttpRequest,
    workspace: models.Workspace,
    repository: models.Repository,
    conversation: models.Conversation,
    app: models.App | None,
    blueprint: models.DeploymentBlueprint | None,
) -> HttpResponse:
    """Render the deployment editor with the current app, blueprint, and chat state."""
    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context.update({
        "workspace": workspace,
        "repository": repository,
        "conversation": conversation,
        "messages": conversation.messages.exclude(
            content_type=models.Message.ContentType.SYSTEM_TRIGGER,
        ).order_by("created_at"),
    })
    context.update(_build_blueprint_section_context(app=app, blueprint=blueprint))
    return render(request=request, template_name="humanityrules_app/deploy/deployment_editor.html", context=context)


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


def _create_deployment_editor_conversation(
    request: HttpRequest,
    workspace: models.Workspace,
    repository: models.Repository,
    app: models.App | None,
) -> models.Conversation:
    """Create a fresh deployment-editor conversation for a repo and optional app."""
    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=str(workspace.id),
        repo_id=str(repository.id),
        aws_account_id=None,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
        app_permission_request_id=None,
    )
    if app:
        conversation.context_app = app
        conversation.save(update_fields=["context_app", "updated_at"])
    return conversation


def _get_or_create_existing_app_editor_conversation(
    request: HttpRequest,
    app: models.App,
    blueprint: models.DeploymentBlueprint | None,
) -> models.Conversation:
    """Return the active deployment-editor conversation for an app and optional open blueprint."""
    if blueprint:
        conversation = models.Conversation.objects.filter(
            context_deployment_blueprint=blueprint,
            user=request.user,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
        ).order_by("-updated_at").first()
        if conversation:
            update_fields: list[str] = []
            if conversation.context_app_id != app.id:
                conversation.context_app = app
                update_fields.append("context_app")
            if conversation.status != models.Conversation.Status.ACTIVE:
                conversation.status = models.Conversation.Status.ACTIVE
                update_fields.append("status")
            if update_fields:
                update_fields.append("updated_at")
                conversation.save(update_fields=update_fields)
            return conversation

        conversation = _create_deployment_editor_conversation(
            request=request,
            workspace=app.workspace,
            repository=app.repository,
            app=app,
        )
        conversation.context_deployment_blueprint = blueprint
        conversation.save(update_fields=["context_deployment_blueprint", "updated_at"])
        return conversation

    conversation = models.Conversation.objects.filter(
        context_app=app,
        context_deployment_blueprint__isnull=True,
        user=request.user,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
    ).order_by("-updated_at").first()
    if not conversation:
        return _create_deployment_editor_conversation(
            request=request,
            workspace=app.workspace,
            repository=app.repository,
            app=app,
        )
    return _reactivate_conversation(conversation=conversation)


@login_required
@base.require_agent_deployments
def deployment_editor_new(request: HttpRequest, workspace_slug: str, repo_id: UUID) -> HttpResponse:
    """Entry point for deploying a new app from a repository."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="humanityrules_app/app_shell.html", context=context)

    organization = request.user.current_organization
    workspace = get_object_or_404(models.Workspace, slug=workspace_slug, organization=organization)

    denied = abac_view_checks.check_abac(request, workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    repository = get_object_or_404(models.Repository, id=repo_id, organization=organization)

    conversation_id = request.GET.get("conversation", "").strip()
    created_new = False
    if conversation_id:
        conversation = get_object_or_404(
            models.Conversation,
            id=conversation_id,
            user=request.user,
            organization=organization,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
            context_workspace=workspace,
            context_repository=repository,
        )
    else:
        conversation = _create_deployment_editor_conversation(
            request=request,
            workspace=workspace,
            repository=repository,
            app=None,
        )
        created_new = True

    response = _render_deployment_editor(
        request=request,
        workspace=workspace,
        repository=repository,
        conversation=conversation,
        app=None,
        blueprint=None,
    )
    if created_new:
        response["HX-Replace-Url"] = f"{request.path}?conversation={conversation.id}"
    return response


@login_required
@base.require_agent_deployments
def deployment_editor(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Single entry point for the existing-app deployment editor."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="humanityrules_app/app_shell.html", context=context)

    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    if app.status == models.App.Status.PENDING_REMOVAL:
        return HttpResponse(status=422)

    open_blueprint = apps_views.get_open_blueprint(app=app)
    conversation = _get_or_create_existing_app_editor_conversation(
        request=request,
        app=app,
        blueprint=open_blueprint,
    )
    return _render_deployment_editor(
        request=request,
        workspace=app.workspace,
        repository=app.repository,
        conversation=conversation,
        app=app,
        blueprint=open_blueprint,
    )


@login_required
@base.require_agent_deployments
@require_POST
def deployment_editor_reset(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Close current conversation, discard draft if present, and start fresh."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    if app.status == models.App.Status.PENDING_REMOVAL:
        return HttpResponse(status=422)

    blueprint = apps_views.get_open_blueprint(app=app)
    if blueprint and blueprint.status in DISCARDABLE_BLUEPRINT_STATUSES:
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

    models.Conversation.objects.filter(
        context_app=app,
        context_deployment_blueprint__isnull=True,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
        status=models.Conversation.Status.ACTIVE,
    ).update(
        status=models.Conversation.Status.ABANDONED,
        updated_at=timezone.now(),
    )

    conversation = _create_deployment_editor_conversation(
        request=request,
        workspace=app.workspace,
        repository=app.repository,
        app=app,
    )
    response = _render_deployment_editor(
        request=request,
        workspace=app.workspace,
        repository=app.repository,
        conversation=conversation,
        app=app,
        blueprint=None,
    )
    response["HX-Replace-Url"] = reverse("deployment_editor", kwargs={"app_slug": app.slug})
    return response


@login_required
@base.require_agent_deployments
def deployment_editor_app_section(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the app section partial for HTMX refresh in the editor."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied
    return render(request=request, template_name="humanityrules_app/deploy/_app_section.html", context={"app": app})


@login_required
@base.require_agent_deployments
def deployment_editor_blueprint_section(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Return the blueprint section partial for HTMX refresh in the editor."""
    app = _get_existing_app(request=request, app_slug=app_slug)
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied
    blueprint = (
        models.DeploymentBlueprint.objects
        .filter(app=app)
        .exclude(status=models.DeploymentBlueprint.Status.DISCARDED)
        .select_related("app", "environment", "datastore")
        .order_by("-created_at")
        .first()
    )
    return render(
        request=request,
        template_name="humanityrules_app/deploy/_blueprint_section.html",
        context=_build_blueprint_section_context(app=app, blueprint=blueprint),
    )


@login_required
@base.require_agent_deployments
@require_GET
def deployment_editor_fork(request: HttpRequest, conversation_id) -> HttpResponse:
    """Fork a deployment conversation and redirect back into the deployment editor."""
    source = get_object_or_404(
        models.Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
    )
    if not source.session_id:
        return HttpResponse("Cannot fork: conversation has no agent session yet.", status=400)

    forked = models.Conversation.objects.create(
        user=request.user,
        organization=request.user.current_organization,
        mode=source.mode,
        context_workspace=source.context_workspace,
        context_repository=source.context_repository,
        context_aws_account=source.context_aws_account,
        context_environment=source.context_environment,
        context_app=source.context_app,
        context_deployment_blueprint=source.context_deployment_blueprint,
        session_id=source.session_id,
        status=models.Conversation.Status.ACTIVE,
    )

    if source.context_app:
        return redirect("deployment_editor", app_slug=source.context_app.slug)

    url = reverse("deployment_editor_new", kwargs={
        "workspace_slug": source.context_workspace.slug,
        "repo_id": source.context_repository_id,
    })
    return redirect(f"{url}?conversation={forked.id}")
