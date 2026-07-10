"""Bearer-auth JSON endpoints for the self-referential Hermes permissions editor.

The env-resident `humr_broker` relays the Hermes WebUI's `/permissions/*` calls
here (env bearer + {owner_username, app_slug, ...} body). HUMR owns auth, target
resolution, ABAC, and the async Apply job; the sandbox never holds the bearer.

The target `(app, environment)` is never a request parameter — it is resolved
from the env bearer (-> environment) plus owner_username/app_slug (-> the app
owned by that user), exactly as the integration endpoints do. This is the JSON
twin of the session-auth HTML editor in `security_permissions_editor.py`; both
sit on the same `services/permissions_service.py` service layer.
"""

import logging
from dataclasses import dataclass

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app.models import App, AppPermissionRequest, Environment, User
from humanityrules_app.services import abac_service
from humanityrules_app.services import permissions_service
from humanityrules_app.views.integrations import broker_request_context
from humanityrules_app.views.integrations import platform_owner

logger = logging.getLogger(__name__)


@dataclass
class _Deployment:
    """The resolved deployment identity for one broker-relayed request."""

    environment: Environment
    owner_user: User
    app: App
    payload: dict


def _resolve_deployment(request: HttpRequest) -> tuple[_Deployment | None, JsonResponse | None]:
    """Resolve (environment, owner_user, app) + body from the env bearer, or an error response."""
    environment, auth_error = broker_request_context.resolve_env_bearer_context(request=request)
    if auth_error is not None:
        return None, auth_error
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return None, parse_error
    owner_user, owner_error = broker_request_context.resolve_owner_user(
        owner_username=payload.get("owner_username"), environment=environment,
    )
    if owner_error is not None:
        return None, owner_error
    app_slug, app_error = broker_request_context.resolve_owned_app_slug(
        app_slug=payload.get("app_slug"), environment=environment, owner_user=owner_user,
    )
    if app_error is not None:
        return None, app_error
    app = App.objects.get(organization=environment.aws_account.organization, slug=app_slug)
    return _Deployment(environment=environment, owner_user=owner_user, app=app, payload=payload), None


def _get_scoped_request(deployment: _Deployment, request_id: object) -> AppPermissionRequest | None:
    """Fetch the AppPermissionRequest by id, scoped to this deployment's app + environment."""
    if not isinstance(request_id, str) or not request_id:
        return None
    return AppPermissionRequest.objects.filter(
        id=request_id, app=deployment.app, environment=deployment.environment,
    ).first()


def _serialize_service_groups(app_permission_request: AppPermissionRequest) -> list[dict]:
    """Render-ready statement groups for the request's current statements (cache-backed)."""
    available = permissions_service.fetch_available_resources(app_permission_request)
    return permissions_service.build_statement_groups(app_permission_request.statements or [], available)


def _serialize_draft(app_permission_request: AppPermissionRequest, deployment: _Deployment, app_permissions: object) -> dict:
    """Full draft state: identity, lifecycle status, has_changes, and service groups."""
    return {
        "request_id": str(app_permission_request.id),
        "status": app_permission_request.status,
        "status_message": app_permission_request.status_message,
        "description": app_permission_request.description,
        "has_changes": not permissions_service.statements_equal(app_permission_request.statements, app_permissions.statements),
        "updated_at": app_permission_request.updated_at.isoformat(),
        "app": {"slug": deployment.app.slug, "name": deployment.app.name},
        "environment": {
            "slug": deployment.environment.slug,
            "aws_account": deployment.environment.aws_account.aws_account_id,
        },
        "service_groups": _serialize_service_groups(app_permission_request),
    }


@csrf_exempt
@require_POST
def permissions_draft(request: HttpRequest) -> JsonResponse:
    """Open/resolve the draft, or report one request's live status when request_id is given.

    With no request_id this resolve-or-creates the DRAFT (initial open). With a
    request_id it returns that request's current state regardless of status, which
    is how the panel polls the Apply lifecycle (applying -> applied/failed) — a
    bare resolve would otherwise spawn a fresh draft once Apply leaves DRAFT.
    """
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permissions = permissions_service.get_or_create_app_permissions(
        app=deployment.app, environment=deployment.environment,
    )

    request_id = deployment.payload.get("request_id")
    if isinstance(request_id, str) and request_id:
        app_permission_request = _get_scoped_request(deployment=deployment, request_id=request_id)
        if app_permission_request is None:
            return JsonResponse({"error": "permission request not found"}, status=404)
    else:
        app_permission_request = permissions_service.get_or_create_draft(
            app=deployment.app, environment=deployment.environment,
            user=deployment.owner_user, app_permissions=app_permissions,
        )

    if app_permission_request.status == AppPermissionRequest.Status.DRAFT:
        permissions_service.ensure_statement_sids(app_permission_request)

    return JsonResponse(_serialize_draft(
        app_permission_request=app_permission_request, deployment=deployment, app_permissions=app_permissions,
    ))


@csrf_exempt
@require_POST
def permissions_statement(request: HttpRequest) -> JsonResponse:
    """Mutate one statement on the draft and return fresh service groups (409 if not a draft)."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permission_request = _get_scoped_request(deployment=deployment, request_id=deployment.payload.get("request_id"))
    if app_permission_request is None:
        return JsonResponse({"error": "permission request not found"}, status=404)
    if app_permission_request.status != AppPermissionRequest.Status.DRAFT:
        return JsonResponse({"error": "permission request is not a draft"}, status=409)

    permissions_service.apply_statement_action(
        app_permission_request,
        action=str(deployment.payload.get("action", "")),
        service=str(deployment.payload.get("service", "")).strip(),
        statement_id=str(deployment.payload.get("statement_id", "")).strip(),
        level=str(deployment.payload.get("level", "")).strip(),
        arn=str(deployment.payload.get("arn", "")).strip(),
        s3_prefix=str(deployment.payload.get("s3_prefix", "")).strip(),
    )
    app_permissions = permissions_service.get_or_create_app_permissions(
        app=deployment.app, environment=deployment.environment,
    )
    return JsonResponse({
        "service_groups": _serialize_service_groups(app_permission_request),
        "has_changes": not permissions_service.statements_equal(app_permission_request.statements, app_permissions.statements),
        "updated_at": app_permission_request.updated_at.isoformat(),
    })


@csrf_exempt
@require_POST
def permissions_description(request: HttpRequest) -> JsonResponse:
    """Replace the draft's description (debounced by the client).

    Returns a 200 JSON ack rather than 204 so the broker's post_json relay (which
    treats a 2xx with no parseable body as a transport error) forwards it cleanly.
    """
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permission_request = _get_scoped_request(deployment=deployment, request_id=deployment.payload.get("request_id"))
    if app_permission_request is None:
        return JsonResponse({"error": "permission request not found"}, status=404)
    permissions_service.update_description(app_permission_request, str(deployment.payload.get("description", "")))
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def permissions_cancel(request: HttpRequest) -> JsonResponse:
    """Reset the draft's statements back to the AppPermissions baseline."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permission_request = _get_scoped_request(deployment=deployment, request_id=deployment.payload.get("request_id"))
    if app_permission_request is None:
        return JsonResponse({"error": "permission request not found"}, status=404)
    app_permissions = permissions_service.get_or_create_app_permissions(
        app=deployment.app, environment=deployment.environment,
    )
    permissions_service.cancel(app_permission_request, app_permissions)
    return JsonResponse({
        "service_groups": _serialize_service_groups(app_permission_request),
        "has_changes": False,
    })


@csrf_exempt
@require_POST
def permissions_apply(request: HttpRequest) -> JsonResponse:
    """Approve the draft for async apply, enforcing the sandbox gate + environment:approve ABAC check."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permission_request = _get_scoped_request(deployment=deployment, request_id=deployment.payload.get("request_id"))
    if app_permission_request is None:
        return JsonResponse({"error": "permission request not found"}, status=404)

    if platform_owner.is_sandbox_approval_gated(environment=deployment.environment):
        return JsonResponse({"error": platform_owner.SANDBOX_APPROVAL_BLOCKED_MESSAGE}, status=403)

    allowed = abac_service.check_action(
        organization=deployment.environment.aws_account.organization,
        user=deployment.owner_user,
        resource=deployment.environment,
        resource_type="environment",
        action="environment:approve",
    )
    if not allowed:
        return JsonResponse({"error": "not authorized to approve permission changes"}, status=403)

    if not permissions_service.approve(app_permission_request=app_permission_request):
        return JsonResponse({
            "error": f"permission request is already {app_permission_request.get_status_display().lower()}",
        }, status=409)
    return JsonResponse({"status": "approved_pending_apply", "request_id": str(app_permission_request.id)})


@csrf_exempt
@require_POST
def permissions_refresh_resources(request: HttpRequest) -> JsonResponse:
    """Clear + re-fetch the AWS resource cache for the draft's services, return fresh groups."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    app_permission_request = _get_scoped_request(deployment=deployment, request_id=deployment.payload.get("request_id"))
    if app_permission_request is None:
        return JsonResponse({"error": "permission request not found"}, status=404)
    services = [stmt.get("service") for stmt in (app_permission_request.statements or []) if stmt.get("service")]
    available = permissions_service.refresh_resources_cache(deployment.environment, services)
    return JsonResponse({
        "service_groups": permissions_service.build_statement_groups(app_permission_request.statements or [], available),
    })


@csrf_exempt
@require_POST
def permissions_resources(request: HttpRequest) -> JsonResponse:
    """Return the cached available resources for a single service (lazy picker load)."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    service = str(deployment.payload.get("service", "")).strip()
    if not service:
        return JsonResponse({"error": "service is required"}, status=400)
    available = permissions_service.get_resources_for_services(deployment.environment, [service])
    return JsonResponse({"service": service, "available_resources": available.get(service, [])})


@csrf_exempt
@require_POST
def permissions_service_catalog(request: HttpRequest) -> JsonResponse:
    """Return the full IAM service list + access levels for the service picker."""
    deployment, error = _resolve_deployment(request=request)
    if error is not None:
        return error
    return JsonResponse({
        "services": permissions_service.get_all_service_options(),
        "access_levels": permissions_service.ACCESS_LEVELS,
    })
