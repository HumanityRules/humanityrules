"""Broker-facing endpoint that stores a Codex device-flow refresh token.

OpenAI Codex connects via a device flow the env-resident broker drives itself
(no browser redirect, no callback of ours — see
`doh_runtime/integrations_broker.py`). When the user approves at
`auth.openai.com/codex/device` and the broker completes the token exchange, the
broker POSTs the resulting refresh_token here. DOH persists it as an
IntegrationUserCredential row, after which the normal batched-refresh path
(`token_refresh_batch.py` → `provider_openai_codex.refresh_outcome`) mints
access tokens from it.

This is the Codex analogue of the OAuth providers' redirect callback: the same
"store the refresh_token" step, reached over the env-bearer broker channel
instead of a browser round-trip. Env bearer + {owner_username, app_slug,
refresh_token} body, at `/api/integrations/credentials/codex-device-complete`.
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import IntegrationUserCredential
from devopshero_app.views.integrations import broker_request_context, provider_openai_codex

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def integrations_codex_device_complete(request: HttpRequest) -> JsonResponse:
    """Store the refresh_token the broker obtained from OpenAI's device flow."""
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

    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return JsonResponse({"error": "refresh_token is required"}, status=400)
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return JsonResponse({"error": "access_token is required"}, status=400)

    # The broker already exchanged the device authorization_code for this
    # refresh/access pair, so we do NOT re-mint here: re-minting would rotate
    # the refresh token (invalidating the one we were handed) and add a fragile
    # second OpenAI round-trip on connect. We only need the chatgpt_account_id,
    # which is a claim in the access token the broker forwarded — derive it
    # directly. If it's absent, this isn't a usable ChatGPT-subscription login.
    account_id = provider_openai_codex._account_id_from_access_token(access_token)
    if account_id is None:
        return JsonResponse({"error": "token has no chatgpt_account_id; not a ChatGPT-subscription login"}, status=400)

    IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENAI_CODEX,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {},
            "metadata": {
                "chatgpt_account_id": account_id,
                "connected_at": timezone.now().isoformat(),
            },
            "last_refreshed_at": None,
        },
    )
    logger.info(
        "codex integration stored env=%s owner=%s app=%s",
        environment.slug, owner_user.username, app_slug,
    )
    return JsonResponse({"ok": True, "provider": "openai-codex", "status": "connected"})
