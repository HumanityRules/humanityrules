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

from humanityrules_app import models
from humanityrules_app.services import abac_service
from humanityrules_app.services.app_templates import template_deploy_service
from humanityrules_app.views import base

logger = logging.getLogger(__name__)


def _editable_variables(template: models.AppTemplate) -> list[dict]:
    """Flatten editable configurable_variables across every container in the template.

    The deploy form renders one input per user-editable var regardless of which
    container declares it; at deploy time, overrides are applied by name to
    every container that has a matching variable.
    """
    result: list[dict] = []
    for container in template.containers or []:
        for v in container.get("configurable_variables") or []:
            if v.get("user_editable"):
                result.append(v)
    return result


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


def _compute_mode_options() -> list[dict]:
    """Return the ECS compute modes available from the template deploy form."""
    return [
        {"id": value, "name": label}
        for value, label in models.EcsComputeMode.choices
    ]


def _selected_label(options: list[dict], value: str, placeholder: str) -> str:
    for opt in options:
        if opt["id"] == value:
            return opt["name"]
    return placeholder


def _template_requires_owner(template: models.AppTemplate) -> bool:
    """True iff the template's default_tags mark it as a Personal Assistant."""
    has_policy_proxy = any(
        c.get("image_source") == "policy_proxy"
        for c in (template.containers or [])
    )
    if not has_policy_proxy:
        return False
    for tag in (template.default_tags or []):
        if tag.get("key") == "app-type" and tag.get("value") == "personal-assistant":
            return True
    return False


def _username_for_prefill(username: str) -> str:
    """Lowercased, alphanumeric-only local-part — safe to drop into an App slug."""
    local = username.split("@", 1)[0]
    return "".join(c for c in local if c.isalnum()).lower()


def _compute_default_app_name(*, template: models.AppTemplate, org: models.Organization, owner_username: str | None) -> str:
    """App Name prefill.

    Falls back to template.slug (short, already unique among templates) when
    there's no prefill_name or no owner — keeps derived resource names short
    enough to clear the 32-char ALB target-group limit.
    """
    pattern = (template.prefill_name or "").strip()
    if not pattern or not owner_username:
        return template.slug
    username_token = _username_for_prefill(username=owner_username)
    for index in range(100):
        candidate = pattern.format(username=username_token, index=f"{index:02d}")
        if not models.App.objects.filter(organization=org, slug=slugify(candidate)).exists():
            return candidate
    return pattern.format(username=username_token, index="99")


def _owner_options_for(request: HttpRequest, org: models.Organization) -> list[dict]:
    """Users the current requester may pick as an owner.

    Org admins may deploy a PA on behalf of any member of the organization.
    Everyone else is locked to themselves.
    """
    if abac_service.is_org_admin(organization=org, user=request.user):
        memberships = models.OrganizationMembership.objects.filter(
            organization=org,
        ).select_related("user").order_by("user__username")
        return [
            {"id": m.user.username, "name": f"{m.user.username} ({m.user.email})"}
            for m in memberships
        ]
    return [{"id": request.user.username, "name": f"{request.user.username} ({request.user.email})"}]


@login_required
def template_deploy_picker(request: HttpRequest) -> HttpResponse:
    """Show a grid of all active templates."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["content_url"] = "/deploy/from-template/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    templates = models.AppTemplate.objects.filter(is_active=True).order_by("name")

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["templates"] = templates
    return render(request, "humanityrules_app/deploy/template_deploy_picker.html", context=context)


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
        return render(request, "humanityrules_app/app_shell.html", context=context)

    workspaces = models.Workspace.objects.filter(organization=org)
    workspaces = abac_service.filter_permitted_resources(
        org, request.user, workspaces, "workspace", "workspace:edit",
    )

    environments = models.Environment.objects.filter(aws_account__organization=org, status=models.Environment.Status.READY)
    environments = abac_service.filter_permitted_resources(
        org, request.user, environments, "environment", "environment:deploy",
    )

    editable_vars = _editable_variables(template)
    for var in editable_vars:
        var["input_value"] = _input_value_from_template(var)

    workspace_options = _workspace_options(workspaces)
    environment_options = _environment_options(environments)
    compute_mode_options = _compute_mode_options()

    requires_owner = _template_requires_owner(template)
    owner_options = _owner_options_for(request=request, org=org) if requires_owner else []
    # Non-admins are locked to themselves; the dropdown is visible but has only
    # one option. Pre-select it so submission works without extra clicks.
    default_owner = request.user.username if requires_owner else ""
    owner_locked = requires_owner and not abac_service.is_org_admin(organization=org, user=request.user)

    default_app_name = _compute_default_app_name(
        template=template, org=org, owner_username=default_owner or None,
    )
    owner_prefill_map: dict[str, str] = {}
    if requires_owner and not owner_locked:
        for opt in owner_options:
            owner_prefill_map[opt["id"]] = _compute_default_app_name(
                template=template, org=org, owner_username=opt["id"],
            )

    context = base.get_app_shell_context(request=request, current_page="workspaces")
    context["template"] = template
    context["workspace_options"] = workspace_options
    context["environment_options"] = environment_options
    context["compute_mode_options"] = compute_mode_options
    default_workspace_id = workspace_options[0]["id"] if len(workspace_options) == 1 else ""
    default_environment_id = environment_options[0]["id"] if len(environment_options) == 1 else ""
    context["selected_workspace_id"] = default_workspace_id
    context["selected_environment_id"] = default_environment_id
    context["selected_compute_mode"] = template.default_compute_mode
    context["selected_workspace_label"] = _selected_label(
        workspace_options, default_workspace_id, "Select a workspace",
    )
    context["selected_environment_label"] = _selected_label(
        environment_options, default_environment_id, "Select an environment",
    )
    context["selected_compute_mode_label"] = _selected_label(
        compute_mode_options, template.default_compute_mode, "Select compute",
    )
    context["default_app_name"] = default_app_name
    context["variable_groups"] = _group_editable_variables(editable_vars)
    context["requires_owner"] = requires_owner
    context["owner_options"] = owner_options
    context["selected_owner_id"] = default_owner
    context["selected_owner_label"] = _selected_label(owner_options, default_owner, "Select an owner")
    context["owner_locked"] = owner_locked
    context["owner_prefill_map"] = owner_prefill_map
    return render(request, "humanityrules_app/deploy/template_deploy_form.html", context=context)


def _handle_deploy(request: HttpRequest, template: models.AppTemplate, org: models.Organization) -> HttpResponse:
    """Validate form inputs and trigger deployment from template."""
    app_name = request.POST.get("app_name", "").strip()
    workspace_id = request.POST.get("workspace_id", "")
    environment_id = request.POST.get("environment_id", "")
    compute_mode = request.POST.get("compute_mode", template.default_compute_mode)
    submitted_owner = request.POST.get("owner_id", "").strip()

    errors = []
    if not app_name:
        errors.append("App name is required.")
    if not workspace_id:
        errors.append("Workspace is required.")
    if not environment_id:
        errors.append("Environment is required.")
    if compute_mode not in models.EcsComputeMode.values:
        errors.append("Compute mode is invalid.")

    app_slug = slugify(app_name)
    if not app_slug:
        errors.append("App name must contain at least one letter or number.")

    if not errors and models.App.objects.filter(organization=org, slug=app_slug).exists():
        errors.append(f"An app with the slug '{app_slug}' already exists in this organization.")

    # Owner field: required for PA templates. Non-admins are always themselves.
    requires_owner = _template_requires_owner(template)
    owner_username: str | None = None
    if requires_owner:
        is_admin = abac_service.is_org_admin(organization=org, user=request.user)
        if is_admin:
            if not submitted_owner:
                errors.append("Owner is required.")
            else:
                owner_exists = models.OrganizationMembership.objects.filter(
                    organization=org, user__username=submitted_owner,
                ).exists()
                if not owner_exists:
                    errors.append("Selected owner is not a member of this organization.")
                else:
                    owner_username = submitted_owner
        else:
            # Non-admin deploys are always owner=self. Ignore any submitted value.
            owner_username = request.user.username

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
        workspaces = abac_service.filter_permitted_resources(
            org, request.user, workspaces, "workspace", "workspace:edit",
        )
        environments = models.Environment.objects.filter(
            aws_account__organization=org, status=models.Environment.Status.READY,
        )
        environments = abac_service.filter_permitted_resources(
            org, request.user, environments, "environment", "environment:deploy",
        )
        workspace_options = _workspace_options(workspaces)
        environment_options = _environment_options(environments)
        compute_mode_options = _compute_mode_options()
        owner_options = _owner_options_for(request=request, org=org) if requires_owner else []

        context = base.get_app_shell_context(request=request, current_page="workspaces")
        context["template"] = template
        context["workspace_options"] = workspace_options
        context["environment_options"] = environment_options
        context["compute_mode_options"] = compute_mode_options
        context["selected_workspace_id"] = workspace_id
        context["selected_environment_id"] = environment_id
        context["selected_compute_mode"] = compute_mode
        context["selected_workspace_label"] = _selected_label(workspace_options, workspace_id, "Select a workspace")
        context["selected_environment_label"] = _selected_label(environment_options, environment_id, "Select an environment")
        context["selected_compute_mode_label"] = _selected_label(compute_mode_options, compute_mode, "Select compute")
        context["default_app_name"] = app_name
        context["variable_groups"] = _group_editable_variables(editable_vars)
        context["errors"] = errors
        context["requires_owner"] = requires_owner
        context["owner_options"] = owner_options
        context["selected_owner_id"] = submitted_owner
        context["selected_owner_label"] = _selected_label(owner_options, submitted_owner, "Select an owner")
        context["owner_locked"] = requires_owner and not abac_service.is_org_admin(organization=org, user=request.user)
        context["owner_prefill_map"] = {}
        return render(request, "humanityrules_app/deploy/template_deploy_form.html", context=context)

    deployment = async_to_sync(template_deploy_service.deploy_from_template)(
        template=template,
        organization=org,
        workspace=workspace,
        environment=environment,
        app_name=app_name,
        app_slug=app_slug,
        created_by=request.user,
        runtime_variable_overrides=variable_overrides,
        owner_username=owner_username,
        compute_mode=compute_mode,
        label="",
    )

    return redirect("app_detail", app_slug=deployment.app.slug)
