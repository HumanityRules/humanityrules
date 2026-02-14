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


def _build_action_cards(selected_context_ready, selected_context_has_deployment):
    action_definitions = [
        {
            "title": "Fix failing app permissions",
            "description": "Use runtime AccessDenied logs to prefill a permission proposal.",
            "cta_label": "Fix now",
            "route_name": "security_permission_request_new",
            "requires_deployment": True,
        },
        {
            "title": "Request new permissions",
            "description": "Start a new IAM request from scratch using form or chat.",
            "cta_label": "New request",
            "route_name": "security_permission_request_new",
            "requires_deployment": False,
        },
        {
            "title": "View current app permissions",
            "description": "Inspect current task-role service policies.",
            "cta_label": "View permissions",
            "route_name": "security_task_role_view",
            "requires_deployment": True,
        },
        {
            "title": "Permission change history",
            "description": "Review previous permission proposals and edits.",
            "cta_label": "Open history",
            "route_name": "security_permission_request_history",
            "requires_deployment": False,
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
    context["open_permission_request_rows"] = _build_open_request_rows(permission_issues=filtered_issues, max_items=4)
    context["permission_history_rows"] = _build_permission_history_rows(permission_issues=filtered_issues, max_items=20)
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
        selected_app=context["selected_context_app"],
        prefill_issue=prefill_issue,
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
        selected_app=context["selected_context_app"],
        prefill_issue=None,
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
    context["selected_context_ready"] = True
    context["selected_context_has_deployment"] = _has_app_environment_deployment(
        selected_app=selected_app,
        selected_environment=selected_environment,
    )

    context_query_params = _build_search_params(
        app_search=context["selector_app_search"],
        environment_search=context["selector_environment_search"],
    )
    context_query_params["context_app"] = selected_app.slug
    context_query_params["context_environment"] = selected_environment.slug
    context["selector_query_params"] = context_query_params
    context["selector_querystring"] = urlencode(query=context_query_params)
    context["security_query_params"] = context["selector_query_params"]
    context["security_querystring"] = context["selector_querystring"]

    context["selected_app"] = selected_app
    context["selected_environment"] = selected_environment
    context["task_role_policy_rows"] = _build_task_role_policies(
        selected_app=selected_app,
        selected_environment=selected_environment,
    )

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
