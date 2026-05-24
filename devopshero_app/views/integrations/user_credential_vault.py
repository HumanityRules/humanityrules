"""DOH-hosted vault endpoints for user-owned integration credentials."""

import json
import logging
import re
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core import signing
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import App, Environment, IntegrationUserCredential, ResourceTag, User
from devopshero_app.views import env_bearer_auth

logger = logging.getLogger(__name__)


SETUP_TOKEN_SALT = "devopshero.integrations.user_credential_setup.v1"
SETUP_TOKEN_MAX_AGE_SECONDS = 5 * 60
TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
TELEGRAM_GET_ME_TIMEOUT_SECONDS = 20
TELEGRAM_BROKER_CACHE_SECONDS = 60 * 60
TELEGRAM_INVALID_TOKEN_MESSAGE = "Telegram rejected this bot token. Check that you pasted the complete token from BotFather."


def _parse_json_body(request: HttpRequest) -> tuple[dict | None, JsonResponse | None]:
    """Parse JSON from an API request body."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "JSON object body is required"}, status=400)
    return payload, None


def _resolve_env_bearer_context(request: HttpRequest) -> tuple[Environment | None, JsonResponse | None]:
    """Resolve the env bearer used by broker-to-DOH integration endpoints."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return None, JsonResponse({"error": "missing bearer token"}, status=401)
    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return None, JsonResponse({"error": "invalid bearer token"}, status=401)
    return environment, None


def _resolve_owner_user(owner_username: object, environment: Environment) -> tuple[User | None, JsonResponse | None]:
    """Resolve and validate the owner user for the environment's organization."""
    if not isinstance(owner_username, str) or not owner_username:
        return None, JsonResponse({"error": "owner_username is required"}, status=400)
    user = User.objects.filter(
        username=owner_username,
        organization_memberships__organization=environment.aws_account.organization,
    ).first()
    if user is None:
        return None, JsonResponse({"error": "not connected"}, status=404)
    return user, None


def _resolve_owned_app_slug(app_slug: object, environment: Environment, owner_user: User) -> tuple[str | None, JsonResponse | None]:
    """Resolve app_slug and verify it belongs to owner_user in the env's organization."""
    if not isinstance(app_slug, str) or not app_slug:
        return None, JsonResponse({"error": "app_slug is required"}, status=400)
    app = App.objects.filter(
        organization=environment.aws_account.organization,
        slug=app_slug,
    ).first()
    if app is None:
        return None, JsonResponse({"error": "app not found"}, status=404)
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=owner_user.username,
    ).first()
    if owner_tag is None:
        return None, JsonResponse({"error": "app is not owned by requested user"}, status=403)
    return app.slug, None


def _provider_from_payload(provider: object) -> tuple[str | None, JsonResponse | None]:
    """Validate a provider slug for the paste-style vault surface."""
    if provider != IntegrationUserCredential.Provider.TELEGRAM:
        return None, JsonResponse({"error": "unsupported credential provider"}, status=400)
    return str(provider), None


def _normalize_origin(public_origin: object, environment: Environment) -> tuple[str | None, JsonResponse | None]:
    """Return an allowed browser origin for a credential setup session."""
    if not isinstance(public_origin, str) or not public_origin:
        return None, JsonResponse({"error": "public_origin is required"}, status=400)
    parsed = urlparse(public_origin)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None, JsonResponse({"error": "invalid public_origin"}, status=400)
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None, JsonResponse({"error": "invalid public_origin"}, status=400)
    origin = _canonical_origin(scheme=parsed.scheme, hostname=hostname, port=port)
    zone = environment.shared_alb_hosted_zone.lower()
    if zone and (hostname == zone or hostname.endswith("." + zone)):
        return origin, None
    if settings.DEBUG and hostname in ("127.0.0.1", "localhost"):
        return origin, None
    return None, JsonResponse({"error": "public_origin is not allowed for this environment"}, status=400)


def _canonical_origin(scheme: str, hostname: str, port: int | None) -> str:
    """Build the browser Origin form: lowercase host, no userinfo, default ports omitted."""
    scheme = scheme.lower()
    host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _schema_for_provider(provider: str, existing: IntegrationUserCredential | None) -> dict:
    """Return the generic form schema for one paste-style provider."""
    if provider == IntegrationUserCredential.Provider.TELEGRAM:
        allowed_users = []
        secret_configured = False
        metadata = {}
        if existing is not None:
            allowed_users = existing.config.get("allowed_users", [])
            secret_configured = bool(existing.credentials.get("bot_token"))
            metadata = existing.metadata
        return {
            "provider": "telegram",
            "label": "Telegram",
            "status": "connected" if existing is not None else "not_connected",
            "secret_configured": secret_configured,
            "metadata": metadata,
            "message": "Credentials are sent directly to the DevOps Hero vault. Your Hermes agent does not receive or store them.",
            "restart_required_after_save": True,
            "fields": [
                {
                    "name": "bot_token",
                    "label": "Bot token",
                    "kind": "secret",
                    "required": existing is None,
                    "placeholder": "123456789:AA...",
                    "help": "Paste the token from BotFather. Leave blank to keep the current token.",
                },
                {
                    "name": "allowed_users",
                    "label": "Allowed Telegram user IDs",
                    "kind": "textarea",
                    "required": False,
                    "value": "\n".join(allowed_users),
                    "help": "One numeric Telegram user ID per line, or comma-separated.",
                },
            ],
        }
    raise ValueError(f"unsupported provider: {provider!r}")


def _setup_token_payload(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    provider: str,
    allowed_origin: str,
) -> dict:
    """Build the signed setup-session payload."""
    return {
        "purpose": "integration_credential_submit",
        "owner_user_id": str(owner_user.id),
        "environment_id": str(environment.id),
        "app_slug": app_slug,
        "provider": provider,
        "allowed_origin": allowed_origin,
    }


@csrf_exempt
@require_POST
def integrations_credential_setup_session(request: HttpRequest) -> JsonResponse:
    """Mint a short-lived browser-to-DOH credential submission session."""
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
    provider, provider_error = _provider_from_payload(provider=payload.get("provider"))
    if provider_error is not None:
        return provider_error
    allowed_origin, origin_error = _normalize_origin(
        public_origin=payload.get("public_origin"),
        environment=environment,
    )
    if origin_error is not None:
        return origin_error

    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
    ).first()
    submit_payload = _setup_token_payload(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
        allowed_origin=allowed_origin,
    )
    submit_token = signing.dumps(submit_payload, salt=SETUP_TOKEN_SALT, compress=True)
    return JsonResponse({
        "action_url": request.build_absolute_uri("/api/integrations/credentials/submit"),
        "submit_token": submit_token,
        "expires_in": SETUP_TOKEN_MAX_AGE_SECONDS,
        "schema": _schema_for_provider(provider=provider, existing=existing),
    })


def _cors_json_response(payload: dict, status: int, allowed_origin: str) -> JsonResponse:
    """Return a JSON response readable by the origin bound into the setup token."""
    response = JsonResponse(payload, status=status)
    response["Access-Control-Allow-Origin"] = allowed_origin
    response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response


def _load_setup_token(token: object) -> tuple[dict | None, JsonResponse | None]:
    """Verify and return a signed setup token payload."""
    if not isinstance(token, str) or not token:
        return None, JsonResponse({"error": "submit_token is required"}, status=400)
    try:
        payload = signing.loads(
            token,
            salt=SETUP_TOKEN_SALT,
            max_age=SETUP_TOKEN_MAX_AGE_SECONDS,
        )
    except signing.BadSignature:
        return None, JsonResponse({"error": "invalid or expired submit_token"}, status=401)
    if payload.get("purpose") != "integration_credential_submit":
        return None, JsonResponse({"error": "invalid submit_token purpose"}, status=401)
    return payload, None


def _normalize_telegram_allowed_users(value: object) -> tuple[list[str] | None, str | None]:
    """Normalize Telegram user IDs from a textarea/string/list field."""
    if value is None:
        return [], None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    elif isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    else:
        return None, "allowed_users must be a string or list"
    for part in parts:
        if not part.isdigit():
            return None, "allowed_users must contain numeric Telegram user IDs only"
    return parts, None


def _telegram_get_me(bot_token: str) -> tuple[dict | None, str | None]:
    """Validate a Telegram bot token and return the bot identity."""
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=TELEGRAM_GET_ME_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("telegram validation request failed: %s", exc.__class__.__name__)
        return None, "Telegram validation failed. Please try again."
    try:
        body = response.json()
    except ValueError:
        return None, "Telegram validation returned a non-JSON response"
    if response.status_code != 200 or body.get("ok") is not True:
        description = body.get("description")
        if response.status_code == 401 or description == "Unauthorized":
            return None, TELEGRAM_INVALID_TOKEN_MESSAGE
        if isinstance(description, str) and description:
            return None, f"Telegram rejected this bot token: {description}"
        return None, "Telegram rejected this bot token."
    result = body.get("result")
    if not isinstance(result, dict) or result.get("is_bot") is not True:
        return None, "Telegram token did not resolve to a bot"
    return result, None


def _save_telegram_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist Telegram credential/config fields."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
    ).first()
    submitted_token = str(credentials_payload.get("bot_token", "") or "").strip()
    existing_token = existing.credentials.get("bot_token", "") if existing is not None else ""
    bot_token = submitted_token or existing_token
    if not bot_token:
        return None, "bot_token is required"
    if submitted_token and TELEGRAM_TOKEN_RE.match(submitted_token) is None:
        return None, "bot_token does not look like a Telegram bot token"

    allowed_users, allowed_users_error = _normalize_telegram_allowed_users(
        value=config_payload.get("allowed_users"),
    )
    if allowed_users_error is not None:
        return None, allowed_users_error

    bot_identity, telegram_error = _telegram_get_me(bot_token=bot_token)
    if telegram_error is not None:
        return None, telegram_error

    metadata = {
        "bot_id": bot_identity.get("id"),
        "bot_username": bot_identity.get("username", ""),
        "bot_name": bot_identity.get("first_name", ""),
        "validated_at": timezone.now().isoformat(),
    }
    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
        defaults={
            "credentials": {"bot_token": bot_token},
            "config": {"allowed_users": allowed_users},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


@csrf_exempt
@require_POST
def integrations_credential_submit(request: HttpRequest) -> JsonResponse:
    """Accept direct browser-to-DOH credential submissions for setup sessions."""
    payload, parse_error = _parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    token_payload, token_error = _load_setup_token(token=payload.get("submit_token"))
    if token_error is not None:
        return token_error
    allowed_origin = token_payload["allowed_origin"]
    request_origin = request.headers.get("Origin", "")
    if request_origin != allowed_origin:
        return JsonResponse({"error": "origin is not allowed"}, status=403)

    try:
        owner_user = User.objects.get(id=token_payload["owner_user_id"])
        environment = Environment.objects.get(id=token_payload["environment_id"])
    except (User.DoesNotExist, Environment.DoesNotExist):
        return _cors_json_response({"error": "setup context no longer exists"}, status=404, allowed_origin=allowed_origin)

    app_slug, app_error = _resolve_owned_app_slug(
        app_slug=token_payload["app_slug"],
        environment=environment,
        owner_user=owner_user,
    )
    if app_error is not None:
        return _cors_json_response({"error": "app is not available"}, status=403, allowed_origin=allowed_origin)

    credentials_payload = payload.get("credentials", {})
    config_payload = payload.get("config", {})
    if not isinstance(credentials_payload, dict) or not isinstance(config_payload, dict):
        return _cors_json_response({"error": "credentials and config must be objects"}, status=400, allowed_origin=allowed_origin)

    provider = token_payload["provider"]
    if provider == IntegrationUserCredential.Provider.TELEGRAM:
        credential, validation_error = _save_telegram_credentials(
            owner_user=owner_user,
            environment=environment,
            app_slug=app_slug,
            credentials_payload=credentials_payload,
            config_payload=config_payload,
        )
    else:
        credential = None
        validation_error = "unsupported credential provider"
    if validation_error is not None:
        return _cors_json_response({"error": validation_error}, status=400, allowed_origin=allowed_origin)
    return _cors_json_response(
        {
            "ok": True,
            "provider": provider,
            "status": "connected",
            "metadata": credential.metadata,
            "restart_required": True,
        },
        status=200,
        allowed_origin=allowed_origin,
    )


@csrf_exempt
@require_POST
def integrations_credential_disconnect(request: HttpRequest) -> JsonResponse:
    """Delete a paste-style user credential row."""
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
    provider, provider_error = _provider_from_payload(provider=payload.get("provider"))
    if provider_error is not None:
        return provider_error
    IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
    ).delete()
    return JsonResponse({"ok": True, "status": "not_connected"})


@csrf_exempt
@require_POST
def integrations_telegram_token(request: HttpRequest) -> JsonResponse:
    """Return the app-scoped Telegram bot token to the outside-sandbox broker."""
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
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TELEGRAM,
    ).first()
    if credential is None:
        return JsonResponse({"error": "not connected"}, status=404)
    bot_token = credential.credentials.get("bot_token", "")
    if not bot_token:
        return JsonResponse({"error": "not connected"}, status=404)
    return JsonResponse({
        "access_token": bot_token,
        "expires_in": TELEGRAM_BROKER_CACHE_SECONDS,
        "token_type": "TelegramBotToken",
        "config": credential.config,
        "metadata": credential.metadata,
    })
