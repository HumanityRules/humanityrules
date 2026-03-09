"""
Deployment editor view: two-panel UI with app/blueprint config + agent chat.

Entry points:
- New app: /deploy/new/?workspace=<slug>&repo=<id> - creates App + Blueprint via agent
- Existing app: /deploy/<app_slug>/ - resume or start a new blueprint
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from .. import models
from ..services.agent import agent_service
from . import abac_view_checks
from . import base


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
        "app": None,
        "blueprint": None,
        "conversation": conversation,
        "messages": messages,
    })

    return render(request=request, template_name="devopshero_app/deploy/deployment_editor.html", context=context)


@login_required
def deployment_editor(request: HttpRequest, app_slug: str) -> HttpResponse:
    """Deployment editor for an existing app."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    app = get_object_or_404(
        models.App.objects.select_related("workspace", "repository"),
        organization=organization,
        slug=app_slug,
    )

    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied

    blueprint = models.DeploymentBlueprint.objects.filter(
        app=app,
    ).select_related("environment", "datastore").order_by("-created_at").first()

    conversation = models.Conversation.objects.filter(
        context_app=app,
        user=request.user,
        mode=models.Conversation.Mode.APP_DEPLOYMENT,
    ).order_by("-updated_at").first()

    if not conversation:
        conversation = agent_service.create_conversation(
            user=request.user,
            workspace_id=str(app.workspace_id),
            repo_id=str(app.repository_id),
            aws_account_id=None,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
            app_permission_request_id=None,
        )
        conversation.context_app = app
        if blueprint:
            conversation.context_deployment_blueprint = blueprint
        conversation.save(update_fields=["context_app", "context_deployment_blueprint", "updated_at"])

    messages = conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context.update({
        "workspace": app.workspace,
        "repository": app.repository,
        "app": app,
        "blueprint": blueprint,
        "conversation": conversation,
        "messages": messages,
    })

    return render(request=request, template_name="devopshero_app/deploy/deployment_editor.html", context=context)


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
    """Return the latest blueprint section partial for HTMX refresh in the editor."""
    organization = request.user.current_organization
    app = get_object_or_404(
        models.App.objects.select_related("workspace"),
        organization=organization,
        slug=app_slug,
    )
    denied = abac_view_checks.check_abac(request, app.workspace, "workspace", "workspace:edit")
    if denied:
        return denied
    blueprint = (
        models.DeploymentBlueprint.objects.filter(app=app)
        .select_related("app", "environment", "datastore")
        .order_by("-created_at")
        .first()
    )
    return render(request=request, template_name="devopshero_app/deploy/_blueprint_section.html", context={"blueprint": blueprint})
