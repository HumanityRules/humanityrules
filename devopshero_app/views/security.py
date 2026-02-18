import json
import logging
import re
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .. import models
from ..services.agent import agent_service
from . import base

logger = logging.getLogger(__name__)


# =============================================================================
# Shared helpers
# =============================================================================


def _query_param_with_legacy_fallback(request, param_name, legacy_param_name):
    param_value = request.GET.get(param_name, "").strip()
    if param_value:
        return param_value
    return request.GET.get(legacy_param_name, "").strip()


def _build_search_params(app_search, environment_search):
    params = {}
    if app_search:
        params["app_search"] = app_search
    if environment_search:
        params["env_search"] = environment_search
    return params


def _get_deployed_environment_ids_for_app(selected_app):
    if not selected_app:
        return set()

    deployed_statuses = [
        models.Deployment.Status.DEPLOYED,
        models.Deployment.Status.SUPERSEDED,
    ]
    deployed_ids = models.Deployment.objects.filter(app=selected_app, status__in=deployed_statuses).values_list(
        "environment_id",
        flat=True,
    )
    return {str(environment_id) for environment_id in deployed_ids}


def _has_app_environment_deployment(selected_app, selected_environment):
    if not selected_app or not selected_environment:
        return False

    deployed_environment_ids = _get_deployed_environment_ids_for_app(selected_app=selected_app)
    return str(selected_environment.id) in deployed_environment_ids


def _build_selector_state(request, organization):
    app_search = request.GET.get("app_search", "").strip()
    environment_search = ""
    app_slug = _query_param_with_legacy_fallback(request=request, param_name="context_app", legacy_param_name="app")
    environment_slug = _query_param_with_legacy_fallback(
        request=request,
        param_name="context_environment",
        legacy_param_name="environment",
    )

    app_base_queryset = models.App.objects.filter(organization=organization).select_related("workspace")
    selected_app = app_base_queryset.filter(slug=app_slug).first() if app_slug else None

    app_queryset = app_base_queryset
    if app_search:
        app_queryset = app_queryset.filter(
            Q(name__icontains=app_search) | Q(slug__icontains=app_search) | Q(workspace__name__icontains=app_search)
        )
    app_rows = list(app_queryset.order_by("name"))
    if selected_app and selected_app not in app_rows:
        app_rows.append(selected_app)
        app_rows = sorted(app_rows, key=lambda app_item: app_item.name.lower())

    environment_base_queryset = (
        models.Environment.objects.filter(aws_account__organization=organization)
        .select_related("aws_account")
        .order_by("name")
    )
    selected_environment = environment_base_queryset.filter(slug=environment_slug).first() if environment_slug else None
    if not selected_app:
        selected_environment = None

    environment_queryset = environment_base_queryset
    environment_rows = list(environment_queryset)
    if selected_environment and selected_environment not in environment_rows:
        environment_rows.append(selected_environment)

    should_auto_select_environment = bool(selected_app and not selected_environment and len(environment_rows) == 1)
    if should_auto_select_environment:
        selected_environment = environment_rows[0]

    deployed_environment_ids = _get_deployed_environment_ids_for_app(selected_app=selected_app)
    if selected_app:
        environment_rows = sorted(
            environment_rows,
            key=lambda environment_item: (
                0 if str(environment_item.id) in deployed_environment_ids else 1,
                environment_item.name.lower(),
            ),
        )
    else:
        environment_rows = sorted(environment_rows, key=lambda environment_item: environment_item.name.lower())

    search_params = _build_search_params(app_search=app_search, environment_search=environment_search)
    selector_app_rows = []
    for app_item in app_rows:
        row_params = dict(search_params)
        row_params["context_app"] = app_item.slug
        if selected_app and selected_environment and selected_app.id == app_item.id:
            row_params["context_environment"] = selected_environment.slug
        selector_app_rows.append(
            {
                "app": app_item,
                "is_selected": bool(selected_app and selected_app.id == app_item.id),
                "select_querystring": urlencode(query=row_params),
            }
        )

    selector_environment_rows = []
    if selected_app:
        for environment_item in environment_rows:
            row_params = dict(search_params)
            row_params["context_app"] = selected_app.slug
            row_params["context_environment"] = environment_item.slug
            selector_environment_rows.append(
                {
                    "environment": environment_item,
                    "is_selected": bool(selected_environment and selected_environment.id == environment_item.id),
                    "is_deployed": str(environment_item.id) in deployed_environment_ids,
                    "select_querystring": urlencode(query=row_params),
                }
            )

    selector_params = dict(search_params)
    if selected_app:
        selector_params["context_app"] = selected_app.slug
    if selected_environment:
        selector_params["context_environment"] = selected_environment.slug

    selected_context_has_deployment = _has_app_environment_deployment(
        selected_app=selected_app,
        selected_environment=selected_environment,
    )

    return {
        "selected_context_app": selected_app,
        "selected_context_environment": selected_environment,
        "selected_context_ready": bool(selected_app and selected_environment),
        "selected_context_has_deployment": selected_context_has_deployment,
        "selector_app_rows": selector_app_rows,
        "selector_environment_rows": selector_environment_rows,
        "selector_app_search": app_search,
        "selector_environment_search": environment_search,
        "selector_environment_auto_selected": should_auto_select_environment,
        "selector_query_params": selector_params,
        "selector_querystring": urlencode(query=selector_params),
    }


# =============================================================================
# Permission issues (runtime errors)
# =============================================================================


def _extract_permission_signature(message):
    match = re.search(pattern=r"([a-z0-9-]+):([A-Za-z0-9*]+)", string=message)
    if match:
        return {"service": match.group(1), "action": match.group(2)}
    return {"service": "unknown", "action": "unknown"}


def _get_recent_permission_issues(organization, max_items):
    permission_issue_filter = (
        Q(message__icontains="access denied")
        | Q(message__icontains="accessdenied")
        | Q(message__icontains="unauthorized")
        | Q(message__icontains="not authorized")
    )
    permission_logs = (
        models.DeploymentLog.objects.filter(permission_issue_filter, deployment__app__organization=organization)
        .select_related("deployment__app__workspace", "deployment__environment")
        .order_by("-created_at")
    )

    issues = []
    for log in permission_logs[:max_items]:
        signature = _extract_permission_signature(message=log.message)
        issues.append(
            {
                "id": str(log.id),
                "created_at": log.created_at,
                "message": log.message,
                "service": signature["service"],
                "action": signature["action"],
                "app": log.deployment.app,
                "workspace": log.deployment.app.workspace,
                "environment": log.deployment.environment,
                "deployment_id": str(log.deployment.id),
            }
        )
    return issues


def _filter_permission_issues(permission_issues, selected_app, selected_environment):
    filtered_rows = permission_issues
    if selected_app:
        filtered_rows = [issue for issue in filtered_rows if issue["app"].id == selected_app.id]
    if selected_environment:
        filtered_rows = [issue for issue in filtered_rows if issue["environment"].id == selected_environment.id]
    return filtered_rows


# =============================================================================
# Security hub helpers
# =============================================================================


def _build_task_role_policies(selected_app, selected_environment):
    if not selected_app or not selected_environment:
        return []

    policies = []
    if selected_app.app_secrets:
        policies.append(
            {
                "service": "secretsmanager",
                "effect": "Allow",
                "actions": ["GetSecretValue"],
                "resources": [f"arn:aws:secretsmanager:*:*:secret:devopshero/{selected_app.name}/*"],
                "notes": "App secret fields injected as environment variables at runtime.",
            }
        )
    if selected_app.datastore_id:
        policies.append(
            {
                "service": "secretsmanager",
                "effect": "Allow",
                "actions": ["GetSecretValue"],
                "resources": ["<database-connection-secret-arn>"],
                "notes": "Datastore connection secret (host, dbname, username, password, url).",
            }
        )

    if not policies:
        policies.append(
            {
                "service": "none",
                "effect": "Allow",
                "actions": [],
                "resources": [],
                "notes": "No app-specific task role inline policies detected for this app yet.",
            }
        )

    return policies


def _build_action_cards(selected_context_ready, selected_context_has_deployment):
    action_definitions = [
        {
            "title": "Fix failing app permissions",
            "description": "Use runtime AccessDenied logs to prefill a permission proposal.",
            "cta_label": "Fix now",
            "route_name": "security_permissions_editor",
            "requires_deployment": True,
        },
        {
            "title": "Edit permissions",
            "description": "View and modify IAM task-role policies for the deployed app.",
            "cta_label": "Open editor",
            "route_name": "security_permissions_editor",
            "requires_deployment": True,
        },
    ]

    action_cards = []
    for definition in action_definitions:
        is_enabled = selected_context_ready and (selected_context_has_deployment or not definition["requires_deployment"])
        if not selected_context_ready:
            disabled_reason = "Select app and environment to enable this action."
        elif definition["requires_deployment"] and not selected_context_has_deployment:
            disabled_reason = "No deployment/task role exists in this environment yet."
        else:
            disabled_reason = ""

        action_cards.append(
            {
                "title": definition["title"],
                "description": definition["description"],
                "cta_label": definition["cta_label"],
                "route_name": definition["route_name"],
                "is_enabled": is_enabled,
                "disabled_reason": disabled_reason,
            }
        )
    return action_cards


def _build_security_context(request):
    organization = request.user.current_organization
    context = base.get_app_shell_context(request=request, current_page="security")
    selector_state = _build_selector_state(request=request, organization=organization)
    all_recent_issues = _get_recent_permission_issues(organization=organization, max_items=40)

    context.update(selector_state)

    filtered_issues = _filter_permission_issues(
        permission_issues=all_recent_issues,
        selected_app=context["selected_context_app"],
        selected_environment=context["selected_context_environment"],
    )
    for issue in filtered_issues:
        issue_query_params = _build_search_params(
            app_search=context["selector_app_search"],
            environment_search=context["selector_environment_search"],
        )
        issue_query_params["context_app"] = issue["app"].slug
        issue_query_params["context_environment"] = issue["environment"].slug
        issue_query_params["issue_id"] = issue["id"]
        issue["fix_querystring"] = urlencode(query=issue_query_params)

    context["permission_issue_rows"] = filtered_issues[:8]
    context["permission_request_rows"] = list(
        models.PermissionRequest.objects.filter(app__organization=organization)
        .select_related("app", "environment", "created_by")
        .order_by("-created_at")[:20]
    )
    context["task_role_policy_rows"] = _build_task_role_policies(
        selected_app=context["selected_context_app"],
        selected_environment=context["selected_context_environment"],
    )
    context["security_action_cards"] = _build_action_cards(
        selected_context_ready=context["selected_context_ready"],
        selected_context_has_deployment=context["selected_context_has_deployment"],
    )
    context["security_query_params"] = context["selector_query_params"]
    context["security_querystring"] = context["selector_querystring"]
    context["selected_app"] = context["selected_context_app"]
    context["selected_environment"] = context["selected_context_environment"]
    return context


# =============================================================================
# Security Hub
# =============================================================================


@login_required
def security(request):
    context = _build_security_context(request=request)
    if request.htmx:
        return render(request=request, template_name="devopshero_app/security/security_hub.html", context=context)

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


# =============================================================================
# Permissions Editor
# =============================================================================

SERVICE_OPTIONS = ["s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm", "logs", "ecs", "ecr", "lambda", "ses"]


def _get_or_create_permission_request(app, environment, user):
    """Find an existing DRAFT PermissionRequest for this app+environment, or create a new one."""
    from ..services.infra_customer import iam_utils

    existing = models.PermissionRequest.objects.filter(
        app=app,
        environment=environment,
        status=models.PermissionRequest.Status.DRAFT,
    ).order_by("-created_at").first()

    if existing:
        return existing

    # Read current IAM policies from AWS
    try:
        statements = iam_utils.read_task_role_statements(environment=environment, app=app)
    except Exception:
        logger.exception("Failed to read IAM policies for %s/%s", app.slug, environment.slug)
        statements = []

    # Create a conversation for this permissions session
    conversation = agent_service.create_conversation(
        user=user,
        workspace_id=app.workspace_id,
        repo_id=None,
        aws_account_id=None,
        mode=models.Conversation.Mode.PERMISSIONS,
    )

    permission_request = models.PermissionRequest.objects.create(
        app=app,
        environment=environment,
        conversation=conversation,
        statements=statements,
        status=models.PermissionRequest.Status.DRAFT,
        created_by=user,
    )
    return permission_request


@login_required
def security_permissions_editor(request):
    """Permissions editor: two-panel UI with policy editor + agent chat."""
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

    permission_request = _get_or_create_permission_request(app=app, environment=environment, user=request.user)

    conversation = permission_request.conversation
    messages = []
    if conversation:
        messages = conversation.messages.exclude(
            content_type=models.Message.ContentType.SYSTEM_TRIGGER,
        ).order_by("created_at")

    context = base.get_app_shell_context(request=request, current_page="security")
    context.update({
        "permission_request": permission_request,
        "app": app,
        "environment": environment,
        "conversation": conversation,
        "messages": messages,
        "service_options": SERVICE_OPTIONS,
        "security_querystring": urlencode(query={"context_app": app_slug, "context_environment": environment_slug}),
    })

    if request.htmx:
        return render(request=request, template_name="devopshero_app/security/security_permissions_editor.html", context=context)

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


@login_required
@require_POST
def security_permissions_editor_apply(request, request_id):
    """Set PermissionRequest status to APPROVED_PENDING_APPLY with final statements."""
    organization = request.user.current_organization
    permission_request = get_object_or_404(
        models.PermissionRequest,
        id=request_id,
        app__organization=organization,
    )

    try:
        body = json.loads(request.body)
        statements = body.get("statements", [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    permission_request.statements = statements
    permission_request.status = models.PermissionRequest.Status.APPROVED_PENDING_APPLY
    permission_request.save(update_fields=["statements", "status", "updated_at"])

    return JsonResponse({"status": "approved_pending_apply", "request_id": str(permission_request.id)})


@login_required
def security_permissions_editor_statements(request, request_id):
    """Return the policy cards HTML for left-panel polling."""
    organization = request.user.current_organization
    permission_request = get_object_or_404(
        models.PermissionRequest,
        id=request_id,
        app__organization=organization,
    )

    context = {
        "permission_request": permission_request,
        "service_options": SERVICE_OPTIONS,
    }
    return render(
        request=request,
        template_name="devopshero_app/security/_permission_statements.html",
        context=context,
    )
