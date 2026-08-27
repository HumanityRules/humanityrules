"""Unified broker-facing disconnect endpoint for per-user integrations.

One handler for every provider kind: it deletes the IntegrationUserCredential
row and, for OAuth providers, best-effort revokes the grant upstream (vault
providers have nothing to revoke). Registry-driven, mirroring the batched
refresh endpoint (`token_refresh_batch.py`) — together they are the two
cross-provider integration endpoints.

The env-resident broker posts here (per-app bearer + {provider} body) at
`/api/integrations/credentials/disconnect` whenever the user clicks Disconnect
in the Hermes WebUI, for every provider kind. The app and its owner are derived
from the bearer, so a broker can only disconnect its own app's credentials.
"""

import logging
import time

from django.db import transaction
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
    """Disconnect the credential row; for OAuth providers, best-effort revoke upstream.

    Providers that set `TOMBSTONE_ON_DISCONNECT` (Google) keep the row as a
    blanked tombstone stamped with `revoked_at_epoch` instead of deleting it:
    the marker is the durable generation check that stops a still-pending
    OAuth callback (whose consent predates this revocation) from silently
    recreating the credential. Everyone else deletes as before.

    Returns True when a row existed and was disconnected.
    """
    # Row-locked so the write serializes against a concurrent OAuth callback's
    # guarded write (google) — the tombstone can't be overwritten by a
    # callback that read pre-disconnect state.
    with transaction.atomic():
        integration = IntegrationUserCredential.objects.select_for_update().filter(
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
        if getattr(spec.module, "TOMBSTONE_ON_DISCONNECT", False):
            integration.credentials = {}
            integration.config = {"scope": ""}
            integration.metadata = {**integration.metadata, "revoked_at_epoch": time.time()}
            integration.save(update_fields=["credentials", "config", "metadata", "updated_at"])
        else:
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
    app, auth_error = broker_request_context.resolve_app_bearer_context(request=request)
    if auth_error is not None:
        return auth_error
    owner_user, owner_error = broker_request_context.resolve_app_owner(app=app)
    if owner_error is not None:
        return owner_error
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    provider = payload.get("provider")
    spec = provider_registry.get(provider=provider) if isinstance(provider, str) else None
    if spec is None:
        return JsonResponse({"error": "unsupported credential provider"}, status=400)

    disconnect_user_integration(
        owner_user=owner_user,
        environment=app.environment,
        app_slug=app.slug,
        spec=spec,
    )
    return JsonResponse({"ok": True, "status": "not_connected"})
