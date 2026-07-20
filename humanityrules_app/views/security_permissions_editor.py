import json
import logging
from urllib.parse import urlencode
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from .. import models
from ..services import permissions_service
from . import abac_view_checks
from . import base
from .integrations import platform_owner

logger = logging.getLogger(__name__)


@login_required
def security_permissions_editor(request: HttpRequest) -> HttpResponse:
    """Permissions editor: full-width IAM policy editor for one (app, environment) draft."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="security")
        context["content_url"] = request.get_full_path()
        return render(request=request, template_name="humanityrules_app/app_shell.html", context=context)

    organization = request.user.current_organization
    app_slug = request.GET.get("context_app", "").strip()
    environment_slug = request.GET.get("context_environment", "").strip()

    if not app_slug or not environment_slug:
        return render(request=request, template_name="humanityrules_app/security/security_permissions_editor.html", context={
            **base.get_app_shell_context(request=request, current_page="security"),
            "error_message": "Missing app or environment. Navigate here from the App Detail page.",
        })

    app = get_object_or_404(models.App, organization=organization, slug=app_slug)
    environment = get_object_or_404(models.Environment, aws_account__organization=organization, slug=environment_slug)

    app_permissions = permissions_service.get_or_create_app_permissions(app=app, environment=environment)
    app_permission_request = permissions_service.get_or_create_draft(app=app, environment=environment, user=request.user, app_permissions=app_permissions)
    permissions_service.ensure_statement_sids(app_permission_request)

    available_resources = permissions_service.fetch_available_resources(app_permission_request)
    service_groups = permissions_service.build_statement_groups(app_permission_request.statements or [], available_resources)
    service_options = permissions_service.get_all_service_options()

    context = base.get_app_shell_context(request=request, current_page="security")
    context.update({
        "app_permission_request": app_permission_request,
        "app": app,
        "environment": environment,
        "service_groups": service_groups,
        "service_options_json": json.dumps(service_options),
        "security_querystring": urlencode(query={"context_app": app_slug, "context_environment": environment_slug}),
        "has_changes": not permissions_service.statements_equal(app_permission_request.statements, app_permissions.statements),
    })

    return render(request=request, template_name="humanityrules_app/security/security_permissions_editor.html", context=context)


@login_required
@require_POST
def security_permissions_editor_apply(request: HttpRequest, app_permission_request_id: UUID) -> HttpResponse:
    """Set AppPermissionRequest status to APPROVED_PENDING_APPLY. Statements are already in DB."""
    organization = request.user.current_organization
    app_permission_request = get_object_or_404(
        models.AppPermissionRequest.objects.select_related("environment", "environment__aws_account"),
        id=app_permission_request_id,
        app__organization=organization,
    )

    denied = abac_view_checks.check_abac(request, app_permission_request.environment, "environment", "environment:approve")
    if denied:
        return denied
    if platform_owner.is_sandbox_approval_gated(environment=app_permission_request.environment):
        return JsonResponse({"error": platform_owner.SANDBOX_APPROVAL_BLOCKED_MESSAGE}, status=403)

    if not permissions_service.approve(app_permission_request=app_permission_request):
        return JsonResponse({
            "error": f"Permission request is already {app_permission_request.get_status_display().lower()}.",
        }, status=409)

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

    available_resources = permissions_service.fetch_available_resources(app_permission_request)
    service_groups = permissions_service.build_statement_groups(app_permission_request.statements or [], available_resources)

    return render(
        request=request,
        template_name="humanityrules_app/security/_permission_statements.html",
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
    statement_id = request.POST.get("statement_id", "").strip()

    affected_sid = permissions_service.apply_statement_action(
        app_permission_request,
        action=action,
        service=service,
        statement_id=statement_id,
        level=request.POST.get("level", "").strip(),
        arn=request.POST.get("arn", "").strip(),
        s3_prefix=request.POST.get("s3_prefix", "").strip(),
    )

    if action == "remove_service":
        if not app_permission_request.statements:
            return render(
                request=request,
                template_name="humanityrules_app/security/_permission_statements.html",
                context={"service_groups": [], "app_permission_request": app_permission_request, "oob": True},
            )
        return HttpResponse()

    available_resources = permissions_service.fetch_available_resources(app_permission_request)
    service_groups = permissions_service.build_statement_groups(app_permission_request.statements or [], available_resources)
    group = next((g for g in service_groups if g["sid"] == affected_sid), None)
    if group is None:
        return HttpResponse(status=204)

    template = "humanityrules_app/security/_permission_service_group.html"
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
    group = permissions_service.build_service_group_data(
        sid=permissions_service.new_statement_sid(),
        service=service,
        selected_levels=set(),
        resources=[],
        available_resources=available.get(service, []),
    )
    return render(
        request=request,
        template_name="humanityrules_app/security/_permission_service_group.html",
        context={"group": group, "app_permission_request": app_permission_request},
    )


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
    service_groups = permissions_service.build_statement_groups(app_permission_request.statements or [], available_resources)

    return render(
        request=request,
        template_name="humanityrules_app/security/_permission_statements.html",
        context={"service_groups": service_groups, "app_permission_request": app_permission_request},
    )
