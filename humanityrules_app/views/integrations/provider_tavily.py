"""Tavily vault provider: API-key validation, persistence, and refresh outcome.

Tavily is a single API key (web search + extract). Like the other vault
providers it can be pasted per user, but its primary use is an org-shared
key an admin provisions once for everybody (the platform web-search key).
The key is validated against Tavily's ``/usage`` endpoint, which returns
200/401 without consuming a search credit.
"""

import logging

import httpx
from django.utils import timezone

from humanityrules_app.models import App, Environment, IntegrationSharedCredential, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


TAVILY_USAGE_URL = "https://api.tavily.com/usage"
TAVILY_VALIDATION_TIMEOUT_SECONDS = 20
TAVILY_BROKER_CACHE_SECONDS = 60 * 60
TAVILY_INVALID_KEY_MESSAGE = "Tavily rejected this API key. Check that you pasted the complete key."


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the generic paste-form schema for Tavily (`app`/`owner_user` unused)."""
    secret_configured = False
    metadata = {}
    if existing is not None:
        secret_configured = bool(existing.credentials.get("api_key"))
        metadata = existing.metadata
    return {
        "provider": "tavily",
        "label": "Tavily",
        "status": "connected" if existing is not None else "not_connected",
        "secret_configured": secret_configured,
        "metadata": metadata,
        "message": "API keys are sent directly to the Humanity Rules vault. Your Hermes agent does not receive or store them.",
        "restart_required_after_save": True,
        "fields": [
            {
                "name": "api_key",
                "label": "API key",
                "kind": "secret",
                "required": existing is None,
                "placeholder": "tvly-...",
                "help": "Paste a Tavily API key. Leave blank to keep the current key.",
            },
        ],
    }


def _tavily_validate_key(api_key: str) -> tuple[dict | None, str | None]:
    """Validate a Tavily API key against the usage endpoint; return (body, error)."""
    try:
        response = httpx.get(
            TAVILY_USAGE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TAVILY_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("tavily validation request failed: %s", exc.__class__.__name__)
        return None, "Tavily validation failed. Please try again."
    if response.status_code == 401:
        return None, TAVILY_INVALID_KEY_MESSAGE
    if response.status_code == 429:
        return None, "Tavily rate-limited the validation request. Please try again shortly."
    if response.status_code != 200:
        logger.error("tavily usage validation failed: status=%d", response.status_code)
        return None, "Tavily rejected this API key."
    try:
        body = response.json()
    except ValueError:
        return None, "Tavily validation returned a non-JSON response"
    if not isinstance(body, dict):
        logger.error("tavily usage validation returned malformed body: %r", body)
        return None, "Tavily validation returned an unexpected response"
    return body, None


def _validation_metadata() -> dict:
    """Non-secret metadata recorded when a Tavily key is validated."""
    return {"validated_at": timezone.now().isoformat()}


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist a Tavily API key for one logical app."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TAVILY,
    ).first()
    submitted_key = str(credentials_payload.get("api_key", "") or "").strip()
    existing_key = existing.credentials.get("api_key", "") if existing is not None else ""
    api_key = submitted_key or existing_key
    if not api_key:
        return None, "api_key is required"

    metadata = existing.metadata if existing is not None else {}
    if submitted_key:
        _, tavily_error = _tavily_validate_key(api_key=api_key)
        if tavily_error is not None:
            return None, tavily_error
        metadata = _validation_metadata()

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TAVILY,
        defaults={
            "credentials": {"api_key": api_key},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for Tavily (plain DB read)."""
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.TAVILY,
    ).first()
    if credential is None:
        return provider_common.absent_outcome()
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent_outcome()
    return provider_common.has_token_outcome(
        secrets={"api_key": api_key},
        expires_in=TAVILY_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )


def refresh_outcome_from_shared(credential: IntegrationSharedCredential) -> dict:
    """Build the broker refresh outcome for an org-shared Tavily credential."""
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent_outcome()
    return provider_common.has_token_outcome(
        secrets={"api_key": api_key},
        expires_in=TAVILY_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )


def validate_shared_key(api_key: str) -> tuple[dict | None, str | None]:
    """Validate an admin-supplied Tavily key for org sharing; return (metadata, error)."""
    api_key = (api_key or "").strip()
    if not api_key:
        return None, "api_key is required"
    _, tavily_error = _tavily_validate_key(api_key=api_key)
    if tavily_error is not None:
        return None, tavily_error
    return _validation_metadata(), None
