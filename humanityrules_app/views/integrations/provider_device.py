"""Broker-facing endpoints that store device-flow OAuth refresh tokens.

The app and its owner are derived from the per-app bearer; the body carries
only the provider-specific fields (e.g. `refresh_token`).
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def integrations_device_complete(request: HttpRequest, provider: str) -> JsonResponse:
    """Store the refresh_token the broker obtained from a provider device flow."""
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.OAUTH)
    if spec is None or not hasattr(spec.module, "store_device_credentials"):
        return JsonResponse({"error": "unknown device-flow provider"}, status=404)

    app, auth_error = broker_request_context.resolve_app_bearer_context(request=request)
    if auth_error is not None:
        return auth_error
    owner_user, owner_error = broker_request_context.resolve_app_owner(app=app)
    if owner_error is not None:
        return owner_error
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return parse_error

    status, response_payload = spec.module.store_device_credentials(
        environment=app.environment,
        owner_user=owner_user,
        app_slug=app.slug,
        payload=payload,
    )
    return JsonResponse(response_payload, status=status)
