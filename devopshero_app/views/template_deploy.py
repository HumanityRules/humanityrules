"""
Views for deploying applications from AppTemplates.

Two views:
- template_deploy_picker: grid of all active templates
- template_deploy_form: workspace/environment/name form + POST to deploy
"""

import logging

from asgiref.sync import async_to_sync
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils.text import slugify

from devopshero_app import models
from devopshero_app.services import abac
from devopshero_app.services.app_templates import template_deploy_service
from devopshero_app.views import base

logger = logging.getLogger(__name__)


def _editable_variables(template: models.AppTemplate) -> list[dict]:
    return [v for v in template.runtime_variables if v.get("user_editable")]


def _group_editable_variables(editable_vars: list[dict]) -> list[dict]:
    """Group variables by their `group` field, preserving first-appearance order.

    Each group is expanded if any variable in it is required and has no input_value.
    """
    groups: dict[str, dict] = {}
    for var in editable_vars:
        name = var.get("group") or "General"
        group = groups.setdefault(name, {"name": name, "variables": [], "expanded": False})
        group["variables"].append(var)
        if var.get("required") and not var.get("input_value"):
            group["expanded"] = True
    return list(groups.values())


def _input_value_from_template(var: dict) -> str:
    """Initial value shown in the form input for an editable variable."""
    if var["category"] == "secret":
        return ""
    value = var.get("value")
    if value in (None, ""):
        default = var.get("default_value")
        return "" if default is None else default
    return value


def _workspace_options(workspaces) -> list[dict]:
    return [{"id": str(ws.id), "name": ws.name} for ws in workspaces]


def _environment_options(environments) -> list[dict]:
    return [
        {"id": str(env.id), "name": f"{env.name} ({env.aws_account.name})"}
        for env in environments
    ]


def _selected_label(options: list[dict], value: str, placeholder: str) -> str:
    for opt in options:
        if opt["id"] == value:
            return opt["name"]
    return placeholder


@login_required
def template_deploy_picker(request: HttpRequest) -> HttpResponse:
    """Show a grid of all active templates."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = "/deploy/from-template/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    templates = models.AppTemplate.objects.filter(is_active=True).order_by("name")

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["templates"] = templates
    return render(request, "devopshero_app/deploy/template_deploy_picker.html", context=context)


@login_required
def template_deploy_form(request: HttpRequest, template_slug: str) -> HttpResponse:
    """GET: show deploy form. POST: create app + blueprint + deployment from template."""
    template = get_object_or_404(models.AppTemplate, slug=template_slug, is_active=True)
    org = request.user.current_organization

    if request.method == "POST":
        return _handle_deploy(request=request, template=template, org=org)

    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = f"/deploy/from-template/{template_slug}/"
        return render(request, "devopshero_app/app_shell.html", context=context)

    workspaces = models.Workspace.objects.filter(organization=org)
    workspaces = abac.filter_permitted_resources(
        org, request.user, workspaces, "workspace", "workspace:edit",
    )

    environments = models.Environment.objects.filter(aws_account__organization=org, status=models.Environment.Status.READY)
    environments = abac.filter_permitted_resources(
        org, request.user, environments, "environment", "environment:deploy",
    )

    editable_vars = _editable_variables(template)
    for var in editable_vars:
        var["input_value"] = _input_value_from_template(var)

    workspace_options = _workspace_options(workspaces)
    environment_options = _environment_options(environments)

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["template"] = template
    context["workspace_options"] = workspace_options
    context["environment_options"] = environment_options
    context["selected_workspace_id"] = ""
    context["selected_environment_id"] = ""
    context["selected_workspace_label"] = "Select a workspace"
    context["selected_environment_label"] = "Select an environment"
    context["default_app_name"] = template.name
    context["variable_groups"] = _group_editable_variables(editable_vars)
    return render(request, "devopshero_app/deploy/template_deploy_form.html", context=context)


def _handle_deploy(request: HttpRequest, template: models.AppTemplate, org: models.Organization) -> HttpResponse:
    """Validate form inputs and trigger deployment from template."""
    app_name = request.POST.get("app_name", "").strip()
    workspace_id = request.POST.get("workspace_id", "")
    environment_id = request.POST.get("environment_id", "")

    errors = []
    if not app_name:
        errors.append("App name is required.")
    if not workspace_id:
        errors.append("Workspace is required.")
    if not environment_id:
        errors.append("Environment is required.")

    app_slug = slugify(app_name)
    if not app_slug:
        errors.append("App name must contain at least one letter or number.")

    if not errors and models.App.objects.filter(organization=org, slug=app_slug).exists():
        errors.append(f"An app with the slug '{app_slug}' already exists in this organization.")

    editable_vars = _editable_variables(template)
    variable_overrides: dict[str, str] = {}
    for var in editable_vars:
        submitted = request.POST.get(f"var_{var['name']}", "").strip()
        var["input_value"] = submitted
        if var.get("required") and not submitted:
            errors.append(f"{var['name']} is required.")
        if submitted:
            variable_overrides[var["name"]] = submitted

    workspace = None
    environment = None
    if not errors:
        try:
            workspace = models.Workspace.objects.get(id=workspace_id, organization=org)
        except models.Workspace.DoesNotExist:
            errors.append("Selected workspace not found.")
        try:
            environment = models.Environment.objects.get(id=environment_id, aws_account__organization=org)
        except models.Environment.DoesNotExist:
            errors.append("Selected environment not found.")

    if errors:
        workspaces = models.Workspace.objects.filter(organization=org)
        workspaces = abac.filter_permitted_resources(
            org, request.user, workspaces, "workspace", "workspace:edit",
        )
        environments = models.Environment.objects.filter(
            aws_account__organization=org, status=models.Environment.Status.READY,
        )
        environments = abac.filter_permitted_resources(
            org, request.user, environments, "environment", "environment:deploy",
        )
        workspace_options = _workspace_options(workspaces)
        environment_options = _environment_options(environments)

        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["template"] = template
        context["workspace_options"] = workspace_options
        context["environment_options"] = environment_options
        context["selected_workspace_id"] = workspace_id
        context["selected_environment_id"] = environment_id
        context["selected_workspace_label"] = _selected_label(workspace_options, workspace_id, "Select a workspace")
        context["selected_environment_label"] = _selected_label(environment_options, environment_id, "Select an environment")
        context["default_app_name"] = app_name
        context["variable_groups"] = _group_editable_variables(editable_vars)
        context["errors"] = errors
        return render(request, "devopshero_app/deploy/template_deploy_form.html", context=context)

    deployment = async_to_sync(template_deploy_service.deploy_from_template)(
        template=template,
        organization=org,
        workspace=workspace,
        environment=environment,
        app_name=app_name,
        app_slug=app_slug,
        created_by=request.user,
        runtime_variable_overrides=variable_overrides,
        # The Owner field on the deploy form will fill this in for PAs (see
        # workstream (h) in sequential-hugging-crab.md). Until then the tag
        # simply isn't stamped and PAs remain inaccessible — fail-closed.
        owner_username=None,
    )

    return redirect("app_detail", app_slug=deployment.app.slug)
