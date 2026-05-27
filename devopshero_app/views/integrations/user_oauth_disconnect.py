"""Shared OAuth user-integration disconnect logic and broker-facing API."""

import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations.user_credential_vault import (
    _parse_json_body,
    _resolve_env_bearer_context,
    _resolve_owned_app_slug,
    _resolve_owner_user,
)

logger = logging.getLogger(__name__)

OAUTH_DISCONNECT_PROVIDERS = frozenset({
    IntegrationUserCredential.Provider.GITHUB,
    IntegrationUserCredential.Provider.GOOGLE,
})


def _oauth_provider_from_payload(provider: object) -> tuple[str | None, JsonResponse | None]:
    if provider not in OAUTH_DISCONNECT_PROVIDERS:
        return None, JsonResponse({"error": "unsupported oauth provider"}, status=400)
    return str(provider), None


def disconnect_user_oauth_integration(
    *,
    owner_user: User,
    environment: Environment,
    app_slug: str,
    provider: str,
) -> bool:
    """Delete the credential row and best-effort revoke upstream.

    Returns True when a row existed and was deleted.
    """
    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
    ).first()
    if integration is None:
        logger.info(
            "oauth disconnect no-op (no grant) provider=%s env=%s owner=%s app=%s",
            provider,
            environment.slug,
            owner_user.username,
            app_slug,
        )
        return False

    refresh_token = integration.credentials.get("refresh_token", "")
    integration.delete()
    if refresh_token:
        if provider == IntegrationUserCredential.Provider.GITHUB:
            from devopshero_app.views.integrations import github_oauth

            github_oauth._revoke_github_grant(refresh_token=refresh_token)
        elif provider == IntegrationUserCredential.Provider.GOOGLE:
            from devopshero_app.views.integrations import google_oauth

            google_oauth._revoke_google_refresh_token(refresh_token=refresh_token)

    logger.info(
        "oauth integration disconnected provider=%s env=%s owner=%s app=%s",
        provider,
        environment.slug,
        owner_user.username,
        app_slug,
    )
    return True


@csrf_exempt
@require_POST
def integrations_user_oauth_disconnect(request: HttpRequest) -> JsonResponse:
    """Delete an OAuth user credential row on behalf of the env-resident broker."""
    environment, auth_error = _resolve_env_bearer_context(request=request)
    if auth_error is not None:
        return auth_error
    payload, parse_error = _parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    owner_user, owner_error = _resolve_owner_user(
        owner_username=payload.get("owner_username"),
        environment=environment,
    )
    if owner_error is not None:
        return owner_error
    app_slug, app_error = _resolve_owned_app_slug(
        app_slug=payload.get("app_slug"),
        environment=environment,
        owner_user=owner_user,
    )
    if app_error is not None:
        return app_error
    provider, provider_error = _oauth_provider_from_payload(provider=payload.get("provider"))
    if provider_error is not None:
        return provider_error

    disconnect_user_oauth_integration(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
    )
    return JsonResponse({"ok": True, "status": "not_connected"})
