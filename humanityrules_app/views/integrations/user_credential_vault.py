"""DOH-hosted vault endpoints for user-owned integration credentials."""

import dataclasses
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.core import signing
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app.models import App, Environment, IntegrationUserCredential, User
from humanityrules_app.views.integrations import broker_request_context, provider_registry

logger = logging.getLogger(__name__)


SETUP_TOKEN_SALT = "devopshero.integrations.user_credential_setup.v1"
SETUP_TOKEN_MAX_AGE_SECONDS = 30 * 60
EXPIRED_SETUP_TOKEN_MESSAGE = "This setup session expired. Close this dialog and click Connect again."


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


def _schema_for_provider(provider: str, existing: IntegrationUserCredential | None, app: App | None, owner_user: User) -> dict:
    """Return the form schema for one paste-style provider via the registry."""
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT)
    if spec is None:
        raise ValueError(f"unsupported provider: {provider!r}")
    return spec.module.schema(existing, app, owner_user)


def _setup_token_payload(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    provider: str,
    allowed_origin: str,
    provider_state: dict | None,
) -> dict:
    """Build the signed setup-session payload.

    `provider_state` carries provider-generated session state (e.g. Telegram's
    suggested bot username) that the poll endpoint must trust — signing it
    into the token keeps the browser from substituting its own values.
    """
    return {
        "purpose": "integration_credential_submit",
        "owner_user_id": str(owner_user.id),
        "environment_id": str(environment.id),
        "app_slug": app_slug,
        "provider": provider,
        "allowed_origin": allowed_origin,
        "provider_state": provider_state,
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
    schema = _schema_for_provider(provider=provider, existing=existing, app=app, owner_user=owner_user)
    # Providers with a link+poll connect flow generate per-session state in
    # schema() (e.g. the suggested bot username); it travels only inside the
    # signed token, never as a browser-editable field.
    provider_state = schema.pop("signed_state", None)
    submit_payload = _setup_token_payload(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
        allowed_origin=allowed_origin,
        provider_state=provider_state,
    )
    submit_token = signing.dumps(submit_payload, salt=SETUP_TOKEN_SALT, compress=True)
    return JsonResponse({
        "action_url": request.build_absolute_uri("/api/integrations/credentials/submit"),
        "poll_url": request.build_absolute_uri("/api/integrations/credentials/poll"),
        "submit_token": submit_token,
        "expires_in": SETUP_TOKEN_MAX_AGE_SECONDS,
        "schema": schema,
    })


def _cors_json_response(payload: dict, status: int, allowed_origin: str) -> JsonResponse:
    """Return a JSON response readable by the origin bound into the setup token."""
    response = JsonResponse(payload, status=status)
    response["Access-Control-Allow-Origin"] = allowed_origin
    response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response


def _invalid_setup_token_response() -> JsonResponse:
    """Return the intentionally opaque response for invalid setup tokens."""
    return JsonResponse({"error": "invalid or expired submit_token"}, status=401)


def _expired_setup_token_response(token: str, request_origin: str) -> JsonResponse:
    """Return a CORS-readable expiry error only for the token's signed origin."""
    try:
        payload = signing.loads(token, salt=SETUP_TOKEN_SALT, max_age=None)
    except signing.BadSignature:
        return _invalid_setup_token_response()
    if not isinstance(payload, dict) or payload.get("purpose") != "integration_credential_submit":
        return _invalid_setup_token_response()
    allowed_origin = payload.get("allowed_origin")
    if not isinstance(allowed_origin, str) or request_origin != allowed_origin:
        return _invalid_setup_token_response()
    return _cors_json_response({"error": EXPIRED_SETUP_TOKEN_MESSAGE}, status=401, allowed_origin=allowed_origin)


def _load_setup_token(token: object, request_origin: str) -> tuple[dict | None, JsonResponse | None]:
    """Verify and return a signed setup token payload."""
    if not isinstance(token, str) or not token:
        return None, JsonResponse({"error": "submit_token is required"}, status=400)
    try:
        payload = signing.loads(
            token,
            salt=SETUP_TOKEN_SALT,
            max_age=SETUP_TOKEN_MAX_AGE_SECONDS,
        )
    except signing.SignatureExpired:
        return None, _expired_setup_token_response(token=token, request_origin=request_origin)
    except signing.BadSignature:
        return None, _invalid_setup_token_response()
    if not isinstance(payload, dict) or payload.get("purpose") != "integration_credential_submit":
        return None, JsonResponse({"error": "invalid submit_token purpose"}, status=401)
    return payload, None


@dataclasses.dataclass(frozen=True)
class _SetupContext:
    """Resolved identity/scope of one valid browser-direct setup-session request."""

    token_payload: dict
    allowed_origin: str
    owner_user: User
    environment: Environment
    app_slug: str


def _resolve_setup_context(request: HttpRequest, payload: dict) -> tuple[_SetupContext | None, JsonResponse | None]:
    """Verify the setup token + origin and resolve the owner/env/app it binds."""
    request_origin = request.headers.get("Origin", "")
    token_payload, token_error = _load_setup_token(token=payload.get("submit_token"), request_origin=request_origin)
    if token_error is not None:
        return None, token_error
    allowed_origin = token_payload["allowed_origin"]
    if request_origin != allowed_origin:
        return None, JsonResponse({"error": "origin is not allowed"}, status=403)

    try:
        owner_user = User.objects.get(id=token_payload["owner_user_id"])
        environment = Environment.objects.get(id=token_payload["environment_id"])
    except (User.DoesNotExist, Environment.DoesNotExist):
        return None, _cors_json_response({"error": "setup context no longer exists"}, status=404, allowed_origin=allowed_origin)

    app_slug, app_error = broker_request_context.resolve_owned_app_slug(
        app_slug=token_payload["app_slug"],
        environment=environment,
        owner_user=owner_user,
    )
    if app_error is not None:
        return None, _cors_json_response({"error": "app is not available"}, status=403, allowed_origin=allowed_origin)
    return _SetupContext(
        token_payload=token_payload,
        allowed_origin=allowed_origin,
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
    ), None


@csrf_exempt
@require_POST
def integrations_credential_submit(request: HttpRequest) -> JsonResponse:
    """Accept direct browser-to-DOH credential submissions for setup sessions."""
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    context, context_error = _resolve_setup_context(request=request, payload=payload)
    if context_error is not None:
        return context_error
    allowed_origin = context.allowed_origin
    owner_user = context.owner_user
    environment = context.environment
    app_slug = context.app_slug
    token_payload = context.token_payload

    credentials_payload = payload.get("credentials", {})
    config_payload = payload.get("config", {})
    if not isinstance(credentials_payload, dict) or not isinstance(config_payload, dict):
        return _cors_json_response({"error": "credentials and config must be objects"}, status=400, allowed_origin=allowed_origin)

    # The provider was validated against the registry when the signed token
    # was minted, so the spec lookup cannot miss.
    provider = token_payload["provider"]
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT)
    credential, validation_error = spec.module.save_credentials(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        credentials_payload=credentials_payload,
        config_payload=config_payload,
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


@csrf_exempt
@require_POST
def integrations_credential_poll(request: HttpRequest) -> JsonResponse:
    """Browser-direct poll for link-driven vault setups (e.g. Telegram managed bots).

    Providers whose credential is created in an external app return
    `signed_state` from `schema()`; the WebUI polls here with the setup token
    until the provider's `poll_setup` reports the credential as connected.
    """
    payload, parse_error = broker_request_context.parse_json_body(request=request)
    if parse_error is not None:
        return parse_error
    context, context_error = _resolve_setup_context(request=request, payload=payload)
    if context_error is not None:
        return context_error

    # Provider validity is guaranteed by the signed token (see submit view).
    provider = context.token_payload["provider"]
    spec = provider_registry.get_of_kind(provider=provider, kind=provider_registry.ProviderKind.VAULT)
    poll_setup = getattr(spec.module, "poll_setup", None)
    provider_state = context.token_payload.get("provider_state")
    if poll_setup is None or not isinstance(provider_state, dict):
        return _cors_json_response(
            {"error": "this provider does not support setup polling"},
            status=400,
            allowed_origin=context.allowed_origin,
        )

    result, poll_error = poll_setup(
        owner_user=context.owner_user,
        environment=context.environment,
        app_slug=context.app_slug,
        state=provider_state,
    )
    if poll_error is not None:
        logger.error(
            "vault setup poll failed: provider=%s owner=%s env=%s app=%s reason=%r",
            provider,
            context.owner_user.username,
            context.environment.slug,
            context.app_slug,
            poll_error,
        )
        return _cors_json_response({"error": poll_error}, status=400, allowed_origin=context.allowed_origin)
    if result.get("status") == "connected":
        logger.info(
            "vault credential connected via setup poll: provider=%s owner=%s env=%s app=%s",
            provider,
            context.owner_user.username,
            context.environment.slug,
            context.app_slug,
        )
    return _cors_json_response(
        {"ok": True, "provider": provider, **result},
        status=200,
        allowed_origin=context.allowed_origin,
    )
