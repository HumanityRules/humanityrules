"""Broker-facing endpoints that store device-flow OAuth refresh tokens."""

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import IntegrationUserCredential
from devopshero_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def integrations_device_complete(request: HttpRequest, provider: str) -> JsonResponse:
    """Store the refresh_token the broker obtained from a provider device flow."""
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.OAUTH)
    if spec is None or not hasattr(spec.module, "store_device_credentials"):
        return JsonResponse({"error": "unknown device-flow provider"}, status=404)

    environment, auth_error = broker_request_context.resolve_env_bearer_context(request=request)
    if auth_error is not None:
        return auth_error
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    owner_user, owner_error = broker_request_context.resolve_owner_user(
        owner_username=payload.get("owner_username"),
        environment=environment,
    )
    if owner_error is not None:
        return owner_error
    app_slug, app_error = broker_request_context.resolve_owned_app_slug(
        app_slug=payload.get("app_slug"),
        environment=environment,
        owner_user=owner_user,
    )
    if app_error is not None:
        return app_error

    status, response_payload = spec.module.store_device_credentials(
        environment=environment,
        owner_user=owner_user,
        app_slug=app_slug,
        payload=payload,
    )
    return JsonResponse(response_payload, status=status)


@csrf_exempt
@require_POST
def integrations_codex_device_complete(request: HttpRequest) -> JsonResponse:
    """Compatibility wrapper for the original Codex-only completion URL."""
    return integrations_device_complete(request=request, provider=IntegrationUserCredential.Provider.OPENAI_CODEX)
