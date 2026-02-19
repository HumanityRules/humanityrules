import json
import logging
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from policy_sentry.shared import iam_data as policy_sentry_iam_data

from .. import models
from ..services.agent import agent_service
from ..services import permissions as permissions_service
from . import base

logger = logging.getLogger(__name__)


# =============================================================================
# Security Hub
# =============================================================================


@login_required
def security(request):
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="devopshero_app/app_shell.html", context=context)

    organization = request.user.current_organization
    context = base.get_app_shell_context(request=request, current_page="security")
    context["permission_issue_rows"] = []
    context["permission_request_rows"] = list(
        models.PermissionRequest.objects.filter(app__organization=organization)
        .select_related("app", "environment", "created_by")
        .order_by("-created_at")[:20]
    )

    return render(request=request, template_name="devopshero_app/security/security_hub.html", context=context)


# =============================================================================
# Permissions Editor
# =============================================================================

CURATED_SERVICES = {"s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm", "logs", "ecs", "ecr", "lambda", "ses"}

ACCESS_LEVELS = ["Read", "Write", "List", "Tagging", "Permissions management"]


def _build_service_group_data(service, selected_levels, resources, available_resources):
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

    selected_arns = set(resources)

    return {
        "service": service,
        "display_name": display_name,
        "resources": resources,
        "available_resources": [
            {**r, "selected": r["arn"] in selected_arns}
            for r in available_resources
        ],
        "access_levels": access_levels,
        "selected_count": len(selected_set),
    }


def _group_statements_by_service(statements, available_resources_by_service):
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


def _fetch_available_resources(permission_request):
    """Fetch available AWS resources for all services in a permission request."""
    from ..services.infra_customer import iam_utils

    services = [stmt.get("service") for stmt in (permission_request.statements or []) if stmt.get("service")]
    if not services:
        return {}
    return iam_utils.list_resources_for_services(permission_request.environment, services)


def _get_all_service_options():
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
def security_permissions_editor(request):
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

    permission_request = permissions_service.get_or_create_draft(app=app, environment=environment, user=request.user)

    # Ensure a conversation exists for the agent chat panel
    if not permission_request.conversation:
        conversation = agent_service.create_conversation(
            user=request.user,
            workspace_id=app.workspace_id,
            repo_id=None,
            aws_account_id=None,
            mode=models.Conversation.Mode.PERMISSIONS,
        )
        permission_request.conversation = conversation
        permission_request.save(update_fields=["conversation", "updated_at"])

    conversation = permission_request.conversation
    messages = conversation.messages.exclude(
        content_type=models.Message.ContentType.SYSTEM_TRIGGER,
    ).order_by("created_at")

    available_resources = _fetch_available_resources(permission_request)
    service_groups = _group_statements_by_service(permission_request.statements or [], available_resources)
    service_options = _get_all_service_options()

    context = base.get_app_shell_context(request=request, current_page="security")
    context.update({
        "permission_request": permission_request,
        "app": app,
        "environment": environment,
        "conversation": conversation,
        "messages": messages,
        "service_groups": service_groups,
        "service_options_json": json.dumps(service_options),
        "security_querystring": urlencode(query={"context_app": app_slug, "context_environment": environment_slug}),
    })

    return render(request=request, template_name="devopshero_app/security/security_permissions_editor.html", context=context)


@login_required
@require_POST
def security_permissions_editor_apply(request, permission_request_id):
    """Set PermissionRequest status to APPROVED_PENDING_APPLY. Statements are already in DB."""
    organization = request.user.current_organization
    permission_request = get_object_or_404(
        models.PermissionRequest,
        id=permission_request_id,
        app__organization=organization,
    )

    permissions_service.approve(permission_request)

    return JsonResponse({"status": "approved_pending_apply", "request_id": str(permission_request.id)})


@login_required
@require_POST
def security_permissions_editor_update_statement(request, permission_request_id):
    """Mutate a single aspect of PermissionRequest.statements and return fresh HTML."""
    organization = request.user.current_organization
    permission_request = get_object_or_404(
        models.PermissionRequest,
        id=permission_request_id,
        app__organization=organization,
    )

    permissions_service.update_statements(
        permission_request,
        action=request.POST.get("action", ""),
        service=request.POST.get("service", "").strip(),
        level=request.POST.get("level", "").strip(),
        arn=request.POST.get("arn", "").strip(),
    )

    available_resources = _fetch_available_resources(permission_request)
    service_groups = _group_statements_by_service(permission_request.statements or [], available_resources)
    return render(
        request=request,
        template_name="devopshero_app/security/_permission_statements.html",
        context={"service_groups": service_groups, "permission_request": permission_request},
    )


@login_required
def security_permissions_editor_service_group(request, permission_request_id):
    """HTMX endpoint: return a rendered service group partial for a new service."""
    organization = request.user.current_organization
    get_object_or_404(
        models.PermissionRequest,
        id=permission_request_id,
        app__organization=organization,
    )

    service = request.GET.get("service", "").strip()
    if not service:
        return JsonResponse({"error": "Missing service parameter"}, status=400)

    group = _build_service_group_data(
        service=service,
        selected_levels=set(),
        resources=[],
        available_resources=[],
    )
    return render(
        request=request,
        template_name="devopshero_app/security/_permission_service_group.html",
        context={"group": group},
    )
