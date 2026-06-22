"""OpenRouter vault provider: API-key validation, persistence, and refresh outcome."""

import logging

import httpx
from django.utils import timezone

from humanityrules_app.models import App, Environment, IntegrationSharedCredential, IntegrationUserCredential, User
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


OPENROUTER_CURRENT_KEY_URL = "https://openrouter.ai/api/v1/key"
OPENROUTER_VALIDATION_TIMEOUT_SECONDS = 20
OPENROUTER_BROKER_CACHE_SECONDS = 60 * 60
OPENROUTER_INVALID_KEY_MESSAGE = "OpenRouter rejected this API key. Check that you pasted the complete key."


def schema(existing: IntegrationUserCredential | None, app: App | None, owner_user: User | None) -> dict:
    """Build the generic paste-form schema for OpenRouter (`app`/`owner_user` unused)."""
    secret_configured = False
    metadata = {}
    if existing is not None:
        secret_configured = bool(existing.credentials.get("api_key"))
        metadata = existing.metadata
    return {
        "provider": "openrouter",
        "label": "OpenRouter",
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
                "placeholder": "sk-or-v1-...",
                "help": "Paste an OpenRouter API key. Leave blank to keep the current key.",
            },
        ],
    }


def _openrouter_current_key(api_key: str) -> tuple[dict | None, str | None]:
    """Validate an OpenRouter API key and return safe display metadata."""
    try:
        response = httpx.get(
            OPENROUTER_CURRENT_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=OPENROUTER_VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.error("openrouter validation request failed: %s", exc.__class__.__name__)
        return None, "OpenRouter validation failed. Please try again."
    try:
        body = response.json()
    except ValueError:
        return None, "OpenRouter validation returned a non-JSON response"
    if response.status_code == 401:
        return None, OPENROUTER_INVALID_KEY_MESSAGE
    if response.status_code != 200:
        logger.error("openrouter current-key validation failed: status=%d", response.status_code)
        return None, "OpenRouter rejected this API key."
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        logger.error("openrouter current-key validation returned malformed body: %r", body)
        return None, "OpenRouter validation returned an unexpected response"
    return data, None


def _metadata_from_key_data(key_data: dict) -> dict:
    """Keep only non-secret fields worth showing in the integrations UI."""
    metadata = {
        "validated_at": timezone.now().isoformat(),
    }
    for key in ("label", "limit", "limit_remaining", "limit_reset", "is_free_tier", "expires_at"):
        value = key_data.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def save_credentials(
    owner_user: User,
    environment: Environment,
    app_slug: str,
    credentials_payload: dict,
    config_payload: dict,
) -> tuple[IntegrationUserCredential | None, str | None]:
    """Validate and persist an OpenRouter API key for one logical app."""
    existing = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENROUTER,
    ).first()
    submitted_key = str(credentials_payload.get("api_key", "") or "").strip()
    existing_key = existing.credentials.get("api_key", "") if existing is not None else ""
    api_key = submitted_key or existing_key
    if not api_key:
        return None, "api_key is required"

    metadata = existing.metadata if existing is not None else {}
    if submitted_key:
        key_data, openrouter_error = _openrouter_current_key(api_key=api_key)
        if openrouter_error is not None:
            return None, openrouter_error
        metadata = _metadata_from_key_data(key_data=key_data)

    credential, _ = IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENROUTER,
        defaults={
            "credentials": {"api_key": api_key},
            "config": {},
            "metadata": metadata,
            "last_refreshed_at": None,
        },
    )
    return credential, None


def refresh_outcome(environment: Environment, owner_user: User, app_slug: str) -> dict:
    """Compute the broker-shaped refresh outcome for OpenRouter (plain DB read)."""
    credential = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=IntegrationUserCredential.Provider.OPENROUTER,
    ).first()
    if credential is None:
        return provider_common.absent_outcome()
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent_outcome()
    return provider_common.has_token_outcome(
        secrets={"api_key": api_key},
        expires_in=OPENROUTER_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )


def refresh_outcome_from_shared(credential: IntegrationSharedCredential) -> dict:
    """Build the broker refresh outcome for an org-shared OpenRouter credential."""
    api_key = credential.credentials.get("api_key", "")
    if not api_key:
        return provider_common.absent_outcome()
    return provider_common.has_token_outcome(
        secrets={"api_key": api_key},
        expires_in=OPENROUTER_BROKER_CACHE_SECONDS,
        config=credential.config,
        metadata=credential.metadata,
    )


def validate_shared_key(api_key: str) -> tuple[dict | None, str | None]:
    """Validate an admin-supplied OpenRouter key for org sharing; return (metadata, error)."""
    api_key = (api_key or "").strip()
    if not api_key:
        return None, "api_key is required"
    key_data, openrouter_error = _openrouter_current_key(api_key=api_key)
    if openrouter_error is not None:
        return None, openrouter_error
    return _metadata_from_key_data(key_data=key_data), None
