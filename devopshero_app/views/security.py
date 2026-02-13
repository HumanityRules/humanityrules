import re
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from .. import models
from . import base


def _query_param_with_legacy_fallback(request, param_name, legacy_param_name):
    param_value = request.GET.get(param_name, "").strip()
    if param_value:
        return param_value
    return request.GET.get(legacy_param_name, "").strip()


def _build_inbox_filter_state(request, organization):
    workspace_slug = _query_param_with_legacy_fallback(
        request=request, param_name="inbox_workspace", legacy_param_name="workspace"
    )
    app_slug = _query_param_with_legacy_fallback(request=request, param_name="inbox_app", legacy_param_name="app")
    environment_slug = _query_param_with_legacy_fallback(
        request=request, param_name="inbox_environment", legacy_param_name="environment"
    )

    workspace_queryset = models.Workspace.objects.filter(organization=organization).order_by("name")
    selected_workspace = workspace_queryset.filter(slug=workspace_slug).first() if workspace_slug else None

    app_queryset = models.App.objects.filter(organization=organization).select_related("workspace").order_by("name")
    if selected_workspace:
        app_queryset = app_queryset.filter(workspace=selected_workspace)
    selected_app = app_queryset.filter(slug=app_slug).first() if app_slug else None

    environment_queryset = (
        models.Environment.objects.filter(aws_account__organization=organization)
        .select_related("aws_account")
        .order_by("name")
    )
    selected_environment = environment_queryset.filter(slug=environment_slug).first() if environment_slug else None

    filter_params = {}
    if selected_workspace:
        filter_params["inbox_workspace"] = selected_workspace.slug
    if selected_app:
        filter_params["inbox_app"] = selected_app.slug
    if selected_environment:
        filter_params["inbox_environment"] = selected_environment.slug

    return {
        "inbox_workspace_options": workspace_queryset,
        "inbox_app_options": app_queryset,
        "inbox_environment_options": environment_queryset,
        "selected_inbox_workspace": selected_workspace,
        "selected_inbox_app": selected_app,
        "selected_inbox_environment": selected_environment,
        "inbox_querystring": urlencode(query=filter_params),
        "inbox_query_params": filter_params,
    }


def _build_action_context_state(request, organization):
    app_slug = _query_param_with_legacy_fallback(request=request, param_name="context_app", legacy_param_name="app")
    environment_slug = _query_param_with_legacy_fallback(
        request=request, param_name="context_environment", legacy_param_name="environment"
    )

    app_queryset = models.App.objects.filter(organization=organization).select_related("workspace").order_by("name")
    selected_app = app_queryset.filter(slug=app_slug).first() if app_slug else None

    environment_queryset = (
        models.Environment.objects.filter(aws_account__organization=organization)
        .select_related("aws_account")
        .order_by("name")
    )
    selected_environment = environment_queryset.filter(slug=environment_slug).first() if environment_slug else None

    context_params = {}
    if selected_app:
        context_params["context_app"] = selected_app.slug
    if selected_environment:
        context_params["context_environment"] = selected_environment.slug

    return {
        "action_app_options": app_queryset,
        "action_environment_options": environment_queryset,
        "selected_context_app": selected_app,
        "selected_context_environment": selected_environment,
        "action_context_ready": bool(selected_app and selected_environment),
        "context_querystring": urlencode(query=context_params),
        "context_query_params": context_params,
    }


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


def _filter_permission_issues(permission_issues, selected_workspace, selected_app, selected_environment):
    filtered_rows = permission_issues
    if selected_workspace:
        filtered_rows = [issue for issue in filtered_rows if issue["workspace"].id == selected_workspace.id]
    if selected_app:
        filtered_rows = [issue for issue in filtered_rows if issue["app"].id == selected_app.id]
    if selected_environment:
        filtered_rows = [issue for issue in filtered_rows if issue["environment"].id == selected_environment.id]
    return filtered_rows


def _build_open_request_rows(permission_issues, max_items):
    rows = []
    for issue in permission_issues[:max_items]:
        rows.append(
            {
                "id": issue["id"],
                "title": f"{issue['app'].name} requested {issue['service']}:{issue['action']}",
                "status": "Draft proposal",
                "created_at": issue["created_at"],
                "source": "Runtime permission error",
                "workspace": issue["workspace"],
                "app": issue["app"],
                "environment": issue["environment"],
            }
        )
    return rows


def _build_permission_history_rows(permission_issues, max_items):
    rows = []
    for issue in permission_issues[:max_items]:
        rows.append(
            {
                "id": issue["id"],
                "created_at": issue["created_at"],
                "title": f"Proposed {issue['service']}:{issue['action']}",
                "summary": issue["message"],
                "source": "runtime_error",
                "status": "Draft",
                "app": issue["app"],
                "environment": issue["environment"],
            }
        )
    return rows


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


def _build_permission_request_statements(selected_app, prefill_issue):
    base_resource = f"arn:aws:s3:::replace-me-{selected_app.slug}/*" if selected_app else "arn:aws:s3:::replace-me/*"
    statements = [
        {
            "service": "s3",
            "effect": "Allow",
            "actions": "GetObject",
            "resource": base_resource,
            "source": "manual",
        }
    ]
    if prefill_issue:
        statements.append(
            {
                "service": prefill_issue["service"],
                "effect": "Allow",
                "actions": prefill_issue["action"],
                "resource": "*",
                "source": "runtime_error",
            }
        )
    return statements


def _build_security_context(request):
    organization = request.user.current_organization
    context = base.get_app_shell_context(request=request, current_page="security")
    inbox_state = _build_inbox_filter_state(request=request, organization=organization)
    action_state = _build_action_context_state(request=request, organization=organization)
    all_recent_issues = _get_recent_permission_issues(organization=organization, max_items=24)

    context.update(inbox_state)
    context.update(action_state)

    filtered_inbox_issues = _filter_permission_issues(
        permission_issues=all_recent_issues,
        selected_workspace=context["selected_inbox_workspace"],
        selected_app=context["selected_inbox_app"],
        selected_environment=context["selected_inbox_environment"],
    )
    filtered_history_issues = _filter_permission_issues(
        permission_issues=all_recent_issues,
        selected_workspace=None,
        selected_app=context["selected_context_app"],
        selected_environment=context["selected_context_environment"],
    )

    combined_query_params = {}
    combined_query_params.update(context["inbox_query_params"])
    combined_query_params.update(context["context_query_params"])
    context["security_query_params"] = combined_query_params
    context["security_querystring"] = urlencode(query=combined_query_params)

    context["permission_issue_rows"] = filtered_inbox_issues[:8]
    context["open_permission_request_rows"] = _build_open_request_rows(permission_issues=filtered_inbox_issues, max_items=4)
    context["permission_history_rows"] = _build_permission_history_rows(permission_issues=filtered_history_issues, max_items=20)
    context["task_role_policy_rows"] = _build_task_role_policies(
        selected_app=context["selected_context_app"], selected_environment=context["selected_context_environment"]
    )
    context["selected_app"] = context["selected_context_app"]
    context["selected_environment"] = context["selected_context_environment"]
    context["security_action_cards"] = [
        {
            "title": "Fix failing app permissions",
            "description": "Use runtime AccessDenied logs to prefill a permission proposal.",
            "cta_label": "Fix now",
            "route_name": "security_permission_request_new",
            "requires_context": True,
        },
        {
            "title": "Request new permissions",
            "description": "Start a new IAM request from scratch using form or chat.",
            "cta_label": "New request",
            "route_name": "security_permission_request_new",
            "requires_context": True,
        },
        {
            "title": "View current app permissions",
            "description": "Inspect task-role service policies for the selected app and environment.",
            "cta_label": "View permissions",
            "route_name": "security_task_role_view",
            "requires_context": True,
        },
        {
            "title": "Permission change history",
            "description": "Review previous proposals and policy diffs for traceability.",
            "cta_label": "Open history",
            "route_name": "security_permission_request_history",
            "requires_context": True,
        },
    ]
    return context


@login_required
def security(request):
    context = _build_security_context(request=request)
    if request.htmx:
        return render(request=request, template_name="devopshero_app/security/security_hub.html", context=context)

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


@login_required
def security_permission_request_new(request):
    context = _build_security_context(request=request)
    issue_id = request.GET.get("issue_id", "").strip()
    prefill_issue = next((issue for issue in context["permission_issue_rows"] if issue["id"] == issue_id), None)

    context["permission_request_title"] = "New permission request"
    context["permission_request_subtitle"] = "Draft a proposal for task-role policy updates."
    context["prefill_issue"] = prefill_issue
    context["permission_statement_rows"] = _build_permission_request_statements(
        selected_app=context["selected_context_app"], prefill_issue=prefill_issue
    )
    context["service_options"] = ["s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm"]

    if request.htmx:
        return render(
            request=request,
            template_name="devopshero_app/security/security_permission_request_workspace.html",
            context=context,
        )

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


@login_required
def security_permission_request_detail(request, request_id):
    context = _build_security_context(request=request)
    context["permission_request_title"] = f"Permission request {request_id}"
    context["permission_request_subtitle"] = "This is a UI skeleton view until persistence is implemented."
    context["prefill_issue"] = None
    context["permission_statement_rows"] = _build_permission_request_statements(
        selected_app=context["selected_context_app"], prefill_issue=None
    )
    context["service_options"] = ["s3", "sqs", "dynamodb", "secretsmanager", "kms", "sns", "ssm"]

    if request.htmx:
        return render(
            request=request,
            template_name="devopshero_app/security/security_permission_request_workspace.html",
            context=context,
        )

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


@login_required
def security_task_role_view(request, app_slug, environment_slug):
    organization = request.user.current_organization
    selected_app = get_object_or_404(models.App, organization=organization, slug=app_slug)
    selected_environment = get_object_or_404(
        models.Environment,
        aws_account__organization=organization,
        slug=environment_slug,
    )

    context = _build_security_context(request=request)
    context["selected_context_app"] = selected_app
    context["selected_context_environment"] = selected_environment
    context["selected_app"] = selected_app
    context["selected_environment"] = selected_environment
    context["action_context_ready"] = True
    context["context_query_params"] = {
        "context_app": selected_app.slug,
        "context_environment": selected_environment.slug,
    }
    context["context_querystring"] = urlencode(query=context["context_query_params"])
    combined_query_params = {}
    combined_query_params.update(context["inbox_query_params"])
    combined_query_params.update(context["context_query_params"])
    context["security_query_params"] = combined_query_params
    context["security_querystring"] = urlencode(query=combined_query_params)
    context["task_role_policy_rows"] = _build_task_role_policies(selected_app=selected_app, selected_environment=selected_environment)

    if request.htmx:
        return render(request=request, template_name="devopshero_app/security/security_task_role_view.html", context=context)

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)


@login_required
def security_permission_request_history(request):
    context = _build_security_context(request=request)
    if request.htmx:
        return render(
            request=request,
            template_name="devopshero_app/security/security_permission_request_history.html",
            context=context,
        )

    context["content_url"] = request.get_full_path()
    return render(request=request, template_name="devopshero_app/app_shell.html", context=context)
