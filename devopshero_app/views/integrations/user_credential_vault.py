"""DOH-hosted vault endpoints for user-owned integration credentials."""

import logging
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from devopshero_app.models import App, Environment, IntegrationUserCredential, User
from devopshero_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


SETUP_TOKEN_SALT = "devopshero.integrations.user_credential_setup.v1"
SETUP_TOKEN_MAX_AGE_SECONDS = 5 * 60


def _provider_from_payload(provider: object) -> tuple[str | None, JsonResponse | None]:
    """Validate a provider slug for the paste-style vault surface."""
    if not isinstance(provider, str) or provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT) is None:
        return None, JsonResponse({"error": "unsupported credential provider"}, status=400)
    return provider, None


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


def _schema_for_provider(provider: str, existing: IntegrationUserCredential | None, app: App | None) -> dict:
    """Return the form schema for one paste-style provider via the registry."""
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT)
    if spec is None:
        raise ValueError(f"unsupported provider: {provider!r}")
    return spec.module.schema(existing, app)


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
    # The Slack schema seeds its default app name from the deploying app's
    # template; resolve_owned_app_slug already verified ownership by slug.
    app = App.objects.select_related("source_template").filter(
        organization=environment.aws_account.organization,
        slug=app_slug,
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
        "schema": _schema_for_provider(provider=provider, existing=existing, app=app),
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


@csrf_exempt
@require_POST
def integrations_credential_submit(request: HttpRequest) -> JsonResponse:
    """Accept direct browser-to-DOH credential submissions for setup sessions."""
    payload, parse_error = broker_request_context.parse_json_body(request=request)
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

    app_slug, app_error = broker_request_context.resolve_owned_app_slug(
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
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT)
    if spec is not None:
        credential, validation_error = spec.module.save_credentials(
            owner_user=owner_user,
            environment=environment,
            app_slug=app_slug,
            credentials_payload=credentials_payload,
            config_payload=config_payload,
        )
    else:
        credential = None
        validation_error = "unsupported credential provider"
        logger.error(
            "vault credential submit: unsupported provider=%r owner=%s env=%s app=%s",
            provider,
            owner_user.username,
            environment.slug,
            app_slug,
        )
    if validation_error is not None:
        logger.error(
            "vault credential save rejected: provider=%s owner=%s env=%s app=%s reason=%r",
            provider,
            owner_user.username,
            environment.slug,
            app_slug,
            validation_error,
        )
        return _cors_json_response({"error": validation_error}, status=400, allowed_origin=allowed_origin)
    logger.info(
        "vault credential connected: provider=%s owner=%s env=%s app=%s metadata=%r",
        provider,
        owner_user.username,
        environment.slug,
        app_slug,
        credential.metadata,
    )
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
