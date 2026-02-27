import json
import logging
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from policy_sentry.shared import iam_data as policy_sentry_iam_data

from .. import models
from ..services.agent import agent_service
from ..services import permissions as permissions_service
from . import abac_view_checks
from . import base

logger = logging.getLogger(__name__)


# =============================================================================
# Security Hub
# =============================================================================


@login_required
def security(request: HttpRequest) -> HttpResponse:
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    context = base.get_app_shell_context(request=request, current_page="security")
    context["active_tab"] = "hub"
    context["permission_issue_rows"] = []
    context["app_permission_request_rows"] = list(
        models.AppPermissionRequest.objects.filter(app__organization=organization)
        .select_related("app", "environment", "created_by")
        .order_by("-created_at")[:20]
    )

    return render(request=request, template_name="devopshero_app/security/security_hub.html", context=context)


# =============================================================================
# Permissions Editor
# =============================================================================

CURATED_SERVICES = {"s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm", "logs", "ecs", "ecr", "lambda", "ses"}

ACCESS_LEVELS = ["Read", "Write", "List", "Tagging", "Permissions management"]

RESOURCE_PLACEHOLDERS = {
    "s3": "Select S3 bucket...",
    "sqs": "Select SQS queue...",
    "dynamodb": "Select DynamoDB table...",
    "secretsmanager": "Select secret...",
    "kms": "Select KMS key...",
    "sns": "Select SNS topic...",
    "ssm": "Select SSM parameter...",
    "logs": "Select log group...",
    "ses": "Select SES identity...",
    "ecr": "Select ECR repository...",
}


def _build_service_group_data(
    service: str,
    selected_levels: list[str] | set[str],
    resources: list[str],
    available_resources: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a template-ready dict for a single service group with access-level toggles."""
    try:
        service_data = policy_sentry_iam_data.get_service_prefix_data(service)
        display_name = service_data.get("service_name", service)
    except Exception:
        display_name = service

    selected_set = set(selected_levels)
    access_levels = []
    for level in ACCESS_LEVELS:
        access_levels.append({
            "name": level,
            "checked": level in selected_set,
        })

    # For S3, available resources are base bucket ARNs (e.g. arn:aws:s3:::my-bucket)
    # but stored resources include the prefix (e.g. arn:aws:s3:::my-bucket/data/*).
    # Use startswith matching so the bucket shows as "selected" when any prefixed resource exists.
    if service == "s3":
        marked_available = [
            {**r, "selected": any(res == r["arn"] or res.startswith(r["arn"] + "/") for res in resources)}
            for r in available_resources
        ]
    else:
        selected_arns = set(resources)
        marked_available = [
            {**r, "selected": r["arn"] in selected_arns}
            for r in available_resources
        ]

    return {
        "service": service,
        "display_name": display_name,
        "resources": resources,
        "available_resources": marked_available,
        "resource_placeholder": RESOURCE_PLACEHOLDERS.get(service, "Select resource..."),
        "access_levels": access_levels,
        "has_checked_levels": bool(selected_set),
        "selected_count": len(selected_set),
    }


def _group_statements_by_service(
    statements: list[dict[str, Any]],
    available_resources_by_service: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Merge multiple statements for the same service into one group."""
    available = available_resources_by_service
    grouped = {}
    for statement in statements:
        service_name = statement.get("service", "")
        if not service_name:
            continue
        if service_name not in grouped:
            grouped[service_name] = {
                "access_levels": set(statement.get("access_levels", [])),
                "resources": list(statement.get("resources", [])),
            }
        else:
            grouped[service_name]["access_levels"].update(statement.get("access_levels", []))
            existing = set(grouped[service_name]["resources"])
            for resource in statement.get("resources", []):
                if resource not in existing:
                    grouped[service_name]["resources"].append(resource)
                    existing.add(resource)

    service_groups = []
    for service_name, data in grouped.items():
        group = _build_service_group_data(
            service=service_name,
            selected_levels=data["access_levels"],
            resources=data["resources"],
            available_resources=available.get(service_name, []),
        )
        service_groups.append(group)
    return service_groups


def _fetch_available_resources(app_permission_request: models.AppPermissionRequest) -> dict[str, list[dict[str, str]]]:
    """Fetch available AWS resources for all services in a permission request (cache-backed)."""
    services = [stmt.get("service") for stmt in (app_permission_request.statements or []) if stmt.get("service")]
    if not services:
        return {}
    return permissions_service.get_resources_for_services(app_permission_request.environment, services)


@login_required
def security_permissions_statements(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Return rendered permission statements for HTMX refetch (triggered by SSE notify)."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest.objects.select_related(
            "app", "environment", "environment__aws_account",
        ),
        id=app_permission_request_id,
        app__organization=organization,
    )
    available_resources = _fetch_available_resources(app_permission_request)
    service_groups = _group_statements_by_service(app_permission_request.statements or [], available_resources)
    return render(
        request=request,
        template_name="devopshero_app/security/_permission_statements.html",
        context={"service_groups": service_groups, "app_permission_request": app_permission_request},
    )


def _get_all_service_options() -> list[dict[str, str | bool]]:
    """Return sorted list of all IAM services for the picker dropdown."""
    iam_def = policy_sentry_iam_data.load_iam_definition()
    options = []
    for prefix, service_data in sorted(iam_def.items()):
        if not isinstance(service_data, dict):
            continue
        service_name = service_data.get("service_name", prefix)
        options.append({
            "value": prefix,
            "label": service_name,
            "is_curated": prefix in CURATED_SERVICES,
        })
    return options


@login_required
def security_permissions_editor(request: HttpRequest) -> HttpResponse:
    """Permissions editor: two-panel UI with policy editor + agent chat."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    app_slug = request.GET.get("context_app", "").strip()
    environment_slug = request.GET.get("context_environment", "").strip()

    if not app_slug or not environment_slug:
        return render(request=request, template_name="devopshero_app/security/security_permissions_editor.html", context={
            **base.get_app_shell_context(request=request, current_page="security"),
            "error_message": "Missing app or environment. Navigate here from the App Detail page.",
        })

    app = get_object_or_404(models.App, organization=organization, slug=app_slug)
    environment = get_object_or_404(models.Environment, aws_account__organization=organization, slug=environment_slug)

    app_permissions = permissions_service.get_or_create_app_permissions(app=app, environment=environment)
    app_permission_request = permissions_service.get_or_create_draft(app=app, environment=environment, user=request.user, app_permissions=app_permissions)

    # Ensure a conversation exists for the agent chat panel
    conversation = models.Conversation.objects.filter(
        context_app_permission_request=app_permission_request,
    ).first()
    if not conversation:
        conversation = agent_service.create_conversation(
            user=request.user,
            workspace_id=app.workspace_id,
            repo_id=app.repository_id,
            aws_account_id=environment.aws_account_id,
            mode=models.Conversation.Mode.PERMISSIONS,
            app_permission_request_id=app_permission_request.id,
        )
    messages = conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")

    available_resources = _fetch_available_resources(app_permission_request)
    service_groups = _group_statements_by_service(app_permission_request.statements or [], available_resources)
    service_options = _get_all_service_options()

    context = base.get_app_shell_context(request=request, current_page="security")
    context.update({
        "app_permission_request": app_permission_request,
        "app": app,
        "environment": environment,
        "conversation": conversation,
        "messages": messages,
        "service_groups": service_groups,
        "service_options_json": json.dumps(service_options),
        "security_querystring": urlencode(query={"context_app": app_slug, "context_environment": environment_slug}),
        "has_changes": app_permission_request.statements != app_permissions.statements,
    })

    return render(request=request, template_name="devopshero_app/security/security_permissions_editor.html", context=context)


@login_required
@require_POST
def security_permissions_editor_apply(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Set AppPermissionRequest status to APPROVED_PENDING_APPLY. Statements are already in DB."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest.objects.select_related("environment"),
        id=app_permission_request_id,
        app__organization=organization,
    )

    denied = abac_view_checks.check_abac(request, app_permission_request.environment, "environment", "environment:approve")
    if denied:
        return denied

    permissions_service.approve(app_permission_request)

    return JsonResponse({"status": "approved_pending_apply", "request_id": str(app_permission_request.id)})


@login_required
@require_POST
def security_permissions_editor_cancel(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Reset the draft's statements back to the AppPermissions baseline and re-render statements."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )

    app_permissions = permissions_service.get_or_create_app_permissions(
        app=app_permission_request.app, environment=app_permission_request.environment,
    )
    permissions_service.cancel(app_permission_request, app_permissions)

    available_resources = _fetch_available_resources(app_permission_request)
    service_groups = _group_statements_by_service(app_permission_request.statements or [], available_resources)

    return render(
        request=request,
        template_name="devopshero_app/security/_permission_statements.html",
        context={"service_groups": service_groups, "app_permission_request": app_permission_request},
    )


@login_required
@require_POST
def security_permissions_editor_update_statement(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Mutate a single aspect of AppPermissionRequest.statements and return fresh HTML."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )

    action = request.POST.get("action", "")
    service = request.POST.get("service", "").strip()
    arn = request.POST.get("arn", "").strip()

    # For S3 dropdown selections, the ARN is a base bucket ARN (arn:aws:s3:::bucket).
    # On add: combine with the prefix input to form the full resource ARN.
    # On remove: remove all stored resources that belong to this bucket.
    s3_prefix = request.POST.get("s3_prefix", "").strip()
    if service == "s3" and action == "add_resource" and s3_prefix:
        arn = f"{arn}/{s3_prefix}"
    elif service == "s3" and action == "remove_resource":
        base_arn = arn
        for stmt in (app_permission_request.statements or []):
            if stmt.get("service") == "s3":
                stmt["resources"] = [r for r in stmt.get("resources", []) if not (r == base_arn or r.startswith(base_arn + "/"))]
        app_permission_request.save(update_fields=["statements", "updated_at"])

    if not (service == "s3" and action == "remove_resource"):
        permissions_service.update_statements(
            app_permission_request,
            action=action,
            service=service,
            level=request.POST.get("level", "").strip(),
            arn=arn,
        )

    if action == "remove_service":
        if not app_permission_request.statements:
            return render(
                request=request,
                template_name="devopshero_app/security/_permission_statements.html",
                context={"service_groups": [], "app_permission_request": app_permission_request, "oob": True},
            )
        return HttpResponse()

    available_resources = _fetch_available_resources(app_permission_request)
    service_groups = _group_statements_by_service(app_permission_request.statements or [], available_resources)
    group = next((g for g in service_groups if g["service"] == service), None)
    if group is None:
        return HttpResponse(status=204)

    template = "devopshero_app/security/_permission_service_group.html"
    if action in ("add_resource", "remove_resource"):
        template += "#resources"
    elif action in ("add_level", "remove_level"):
        template += "#access_levels"

    return render(
        request=request,
        template_name=template,
        context={"group": group, "app_permission_request": app_permission_request},
    )


@login_required
def security_permissions_editor_service_group(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """HTMX endpoint: return a rendered service group partial for a new service."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )

    service = request.GET.get("service", "").strip()
    if not service:
        return JsonResponse({"error": "Missing service parameter"}, status=400)

    available = permissions_service.get_resources_for_services(app_permission_request.environment, [service])
    group = _build_service_group_data(
        service=service,
        selected_levels=set(),
        resources=[],
        available_resources=available.get(service, []),
    )
    return render(
        request=request,
        template_name="devopshero_app/security/_permission_service_group.html",
        context={"group": group, "app_permission_request": app_permission_request},
    )


@login_required
def security_permissions_editor_description(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Return the current description as plain text (for SSE refetch)."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )
    return HttpResponse(app_permission_request.description, content_type="text/plain")


@login_required
@require_POST
def security_permissions_editor_update_description(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Save the description textarea content (debounced from client)."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )
    permissions_service.update_description(
        app_permission_request, request.POST.get("description", ""),
    )
    return HttpResponse(status=204)


@login_required
@require_POST
def security_permissions_editor_refresh_resources(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Clear and re-fetch AWS resource cache, then re-render the statements partial."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest,
        id=app_permission_request_id,
        app__organization=organization,
    )

    services = [stmt.get("service") for stmt in (app_permission_request.statements or []) if stmt.get("service")]
    available_resources = permissions_service.refresh_resources_cache(app_permission_request.environment, services)
    service_groups = _group_statements_by_service(app_permission_request.statements or [], available_resources)

    return render(
        request=request,
        template_name="devopshero_app/security/_permission_statements.html",
        context={"service_groups": service_groups, "app_permission_request": app_permission_request},
    )
