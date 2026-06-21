"""Unified broker-facing disconnect endpoint for per-user integrations.

One handler for every provider kind: it deletes the IntegrationUserCredential
row and, for OAuth providers, best-effort revokes the grant upstream (vault
providers have nothing to revoke). Registry-driven, mirroring the batched
refresh endpoint (`token_refresh_batch.py`) — together they are the two
cross-provider integration endpoints.

The env-resident broker posts here (env bearer + {owner_username, app_slug,
provider} body) at `/api/integrations/credentials/disconnect` whenever the
user clicks Disconnect in the Hermes WebUI, for every provider kind.
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app.models import Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


def disconnect_user_integration(
    *,
    owner_user: User,
    environment: Environment,
    app_slug: str,
    spec: provider_registry.ProviderSpec,
) -> bool:
    """Delete the credential row; for OAuth providers, best-effort revoke upstream.

    Returns True when a row existed and was deleted.
    """
    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=spec.provider,
    ).first()
    if integration is None:
        logger.info(
            "integration disconnect no-op (no row) provider=%s env=%s owner=%s app=%s",
            spec.provider,
            environment.slug,
            owner_user.username,
            app_slug,
        )
        return False

    refresh_token = integration.credentials.get("refresh_token", "")
    integration.delete()
    if spec.kind == provider_registry.ProviderKind.OAUTH and refresh_token:
        spec.module.revoke(refresh_token=refresh_token)

    logger.info(
        "integration disconnected provider=%s env=%s owner=%s app=%s",
        spec.provider,
        environment.slug,
        owner_user.username,
        app_slug,
    )
    return True


@csrf_exempt
@require_POST
def integrations_credential_disconnect(request: HttpRequest) -> JsonResponse:
    """Delete a user credential row (any provider) on behalf of the env-resident broker."""
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
    provider = payload.get("provider")
    spec = provider_registry.get(provider=provider) if isinstance(provider, str) else None
    if spec is None:
        return JsonResponse({"error": "unsupported credential provider"}, status=400)

    disconnect_user_integration(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        spec=spec,
    )
    return JsonResponse({"ok": True, "status": "not_connected"})
