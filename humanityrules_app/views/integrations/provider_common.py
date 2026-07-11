"""Shared building blocks for the per-user integration providers.

Two provider families live alongside this module, organized symmetrically:

- OAuth providers (`provider_google`, `provider_github`, `provider_openai_codex`,
  `provider_nous`): connect via redirect OAuth or a broker-run device flow,
  refresh by exchanging a stored refresh_token upstream, disconnect by deleting
  the row + best-effort upstream revoke.
- Vault providers (`provider_openrouter`, `provider_slack`, `provider_telegram`):
  connect via the browser-direct setup-session surface (a credential paste
  form, or Telegram's link+poll managed-bot flow), refresh by reading the row
  back, and carry their own non-secret `config`/`metadata`.

This module holds what both families (or both OAuth providers) would
otherwise duplicate: the redirect-flow request helpers, the refresh-exchange
result wrapper, JWT access-token claim helpers, and the broker-outcome
constructors that single-source the `{outcome, secrets?, expires_in?, config?,
metadata?}` contract the batched refresh endpoint returns. See
docs/integrations/integrations_broker_design.md.
"""

import base64
import binascii
import dataclasses
import json
import logging
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlparse

import httpx
from django.db import transaction
from django.utils import timezone

from humanityrules_app.models import (
    App,
    Environment,
    IntegrationSharedCredential,
    IntegrationUserCredential,
    PlatformSharedCredential,
    ResourceTag,
    User,
)

logger = logging.getLogger(__name__)


def resolve_env_by_rd(rd: str, user: User) -> Environment | None:
    """Return the Environment whose shared_alb_hosted_zone suffixes *rd*'s host, or None.

    Accepts any URL whose host is a subdomain of a known env's hosted zone —
    e.g. rd `https://hermes.dev.example.com/x` matches an Environment with
    `shared_alb_hosted_zone = "dev.example.com"`. Scoped to envs in orgs
    *user* is a member of.
    """
    if not rd:
        return None
    parsed = urlparse(rd)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    candidates = (
        Environment.objects
        .exclude(shared_alb_hosted_zone="")
        .filter(aws_account__organization__memberships__user=user)
        .only("id", "slug", "shared_alb_hosted_zone")
    )
    for env in candidates:
        zone = env.shared_alb_hosted_zone.lower()
        if host == zone or host.endswith("." + zone):
            return env
    return None


def resolve_owned_app_slug(app_slug: str, env: Environment, owner_username: str) -> str | None:
    """Return app_slug when it identifies an app owned by *owner_username* in env's org.

    Redirect-flow form: returns None so the caller can render an
    HttpResponseBadRequest. The vault API surface uses its own variant that
    returns a JsonResponse error tuple instead.
    """
    if not app_slug:
        return None
    app = App.objects.filter(
        organization=env.aws_account.organization,
        slug=app_slug,
    ).first()
    if app is None:
        return None
    owner_tag = ResourceTag.objects.filter(
        resource_type=ResourceTag.ResourceType.APP,
        app=app,
        key="owner",
        value=owner_username,
    ).first()
    return app.slug if owner_tag is not None else None


def append_query(url: str, extra: dict[str, str]) -> str:
    """Append *extra* query params to *url*, preserving any existing query string."""
    parsed = urlparse(url)
    encoded = urlencode(extra)
    combined = f"{parsed.query}&{encoded}" if parsed.query else encoded
    return parsed._replace(query=combined).geturl()


@dataclasses.dataclass(frozen=True)
class ExchangeResult:
    """Outcome of an OAuth refresh-token exchange.

    Exactly one of `response`, `revoked`, or `error` is populated on any
    given instance. `revoked` means upstream rejected the refresh_token as
    permanently unusable (caller should delete the row); `error` is any
    transient/other failure (network, 5xx, non-JSON). A None `response` is
    normalized to `{}` so callers can `.response.get(...)` unconditionally.
    """

    response: dict | None
    revoked: bool
    error: str | None

    def __post_init__(self) -> None:
        if self.response is None:
            object.__setattr__(self, "response", {})


def exchange_refresh_token(
    *,
    send: Callable[[], httpx.Response],
    revoking_statuses: frozenset[int],
    revoking_error_codes: frozenset[str],
    error_code_of: Callable[[dict], str],
) -> ExchangeResult:
    """Run a refresh-token POST and classify it into revoked / error / response.

    *send* performs the provider's own `httpx.post` (kept in the provider module
    so tests can patch `provider_<slug>.httpx.post`). A 200 yields the parsed
    body; a non-200 whose status is in *revoking_statuses* and whose
    *error_code_of(body)* is in *revoking_error_codes* is `revoked`; anything
    else is a transient `error`. Providers differ only in those three inputs.
    """
    try:
        response = send()
    except httpx.HTTPError as exc:
        return ExchangeResult(response=None, revoked=False, error=f"network: {exc}")

    if response.status_code == 200:
        try:
            return ExchangeResult(response=response.json(), revoked=False, error=None)
        except ValueError:
            return ExchangeResult(response=None, revoked=False, error="non-json-200")

    try:
        body = response.json()
    except ValueError:
        body = {}
    error_code = error_code_of(body)
    if response.status_code in revoking_statuses and error_code in revoking_error_codes:
        return ExchangeResult(response=None, revoked=True, error=None)
    return ExchangeResult(response=None, revoked=False, error=f"http {response.status_code}: {error_code or 'unknown'}")


def now() -> timezone.datetime:
    """Indirection for tests to patch."""
    return timezone.now()


def decode_jwt_payload(token: str) -> dict:
    """Return a JWT's payload claims without verifying the signature, or {} on failure."""
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1]
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}


def expires_in_from_access_token(access_token: str, fallback: int) -> int:
    """Derive seconds-until-expiry from a JWT `exp` claim, or return fallback."""
    claims = decode_jwt_payload(token=access_token)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        remaining = int(exp - time.time())
        if remaining > 0:
            return remaining
    return fallback


def store_oauth_refresh_credential(
    *,
    provider: str,
    environment: Environment,
    owner_user: User,
    app_slug: str,
    refresh_token: object,
    metadata: dict,
) -> tuple[int, dict]:
    """Validate and store a device-flow OAuth refresh token row."""
    if not isinstance(refresh_token, str) or not refresh_token:
        return 400, {"error": "refresh_token is required"}

    IntegrationUserCredential.objects.update_or_create(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
        defaults={
            "credentials": {"refresh_token": refresh_token},
            "config": {},
            "metadata": {"connected_at": now().isoformat(), **metadata},
            "last_refreshed_at": None,
        },
    )
    logger.info("%s integration stored env=%s owner=%s app=%s", provider, environment.slug, owner_user.username, app_slug)
    return 200, {"ok": True, "provider": provider, "status": "connected"}


@dataclasses.dataclass(frozen=True)
class RefreshSecrets:
    """The usable product of a refresh exchange: what to inject + what to persist.

    `secrets`/`expires_in` go to the broker `has_token` outcome; `row_metadata`
    and `row_config` are merged into the credential row before saving (empty
    when the provider keeps no derived values). `row_config` exists for
    providers whose refresh response carries authoritative config — e.g.
    Google returns the access token's effective `scope`, which supersedes the
    scope stored at connect time (project-wide grant merging can change it).
    """

    secrets: dict
    expires_in: int
    row_metadata: dict
    row_config: dict


def run_refresh_exchange(
    *,
    provider: str,
    logger: logging.Logger,
    environment: Environment,
    owner_user: User,
    app_slug: str,
    exchange: Callable[[str], ExchangeResult],
    build_secrets: Callable[[str, dict], RefreshSecrets | None],
    outcome_metadata: Callable[[IntegrationUserCredential], dict] | None,
    tombstone_on_revoke: bool,
) -> dict:
    """Run the standard refresh-token exchange for one OAuth credential row.

    Used by all OAuth providers: redirect-OAuth (Google, X) and device-flow
    (Codex, Nous).

    Fetches the row, exchanges its stored refresh_token via exchange(), handles
    the revoked/transient/missing-access_token branches uniformly, rotates a
    returned refresh_token, then asks *build_secrets* to turn the access token
    + response into the broker `has_token` payload (returning None →
    `transient`, for a token the provider deems unusable).

    outcome_metadata() (or None) builds the `has_token` outcome's metadata from
    the refreshed row — the channel the WebUI card reads for provider-derived
    status such as grants.

    Row writes are compare-and-swap CAS on the exchanged refresh_token: the
    exchange holds a stale instance across a network call, and a concurrent
    reconnect (OAuth callback) may replace the row's token meanwhile. Without
    the guard, a stale `invalid_grant` would delete the freshly reconnected
    credential, and a stale success would write the old token back over it.
    """
    integration = IntegrationUserCredential.objects.filter(
        owner_user=owner_user,
        environment=environment,
        app_slug=app_slug,
        provider=provider,
    ).first()
    if integration is None:
        return absent_outcome()

    if not integration.credentials:
        # A tombstone row (credentials deliberately blanked, e.g. Google's
        # narrow flow awaiting re-consent): disconnected, but not an anomaly.
        return absent_outcome()
    refresh_token = integration.credentials.get("refresh_token", "")
    if not refresh_token:
        logger.error("%s token refresh: row missing refresh_token env=%s owner=%s app=%s", provider, environment.slug, owner_user.username, app_slug)
        return absent_outcome()

    exchange_result = exchange(refresh_token)
    if exchange_result.revoked:
        # Same CAS (Compare-and-Swap) on both branches: only touch the row if it still holds the
        # token upstream just rejected. `tombstone_on_revoke` providers (Google) blank the
        # row and stamp `revoked_at_epoch` instead of deleting, preserving the generation
        # marker that stops a pending OAuth flow started before the revocation from
        # recreating the credential as if nothing happened.
        revoked_row = IntegrationUserCredential.objects.filter(
            pk=integration.pk, credentials__refresh_token=refresh_token,
        )
        if tombstone_on_revoke:
            changed_count = revoked_row.update(
                credentials={},
                config={"scope": ""},
                metadata={**integration.metadata, "revoked_at_epoch": time.time()},
                updated_at=now(),
            )
        else:
            changed_count, _ = revoked_row.delete()
        if not changed_count:
            # Lost CAS: a reconnect replaced the token while this exchange ran.
            # The revoked verdict applies to the OLD token only — an
            # authoritative `absent` would wrongly flip the fresh credential
            # to disconnected on the broker.
            logger.info("%s token refresh: revoked upstream but row was reconnected meanwhile env=%s owner=%s app=%s", provider, environment.slug, owner_user.username, app_slug)
            return transient_outcome()
        logger.info("%s token refresh: revoked upstream, disconnecting row env=%s owner=%s app=%s", provider, environment.slug, owner_user.username, app_slug)
        return absent_outcome()
    
    if exchange_result.error is not None:
        logger.error("%s token refresh failed env=%s owner=%s app=%s error=%s", provider, environment.slug, owner_user.username, app_slug, exchange_result.error)
        return transient_outcome()

    access_token = exchange_result.response.get("access_token", "")
    if not access_token:
        logger.error("%s token refresh: response missing access_token env=%s owner=%s app=%s", provider, environment.slug, owner_user.username, app_slug)
        return transient_outcome()

    built = build_secrets(access_token, exchange_result.response)
    if built is None:
        return transient_outcome()

    updates: dict = {"last_refreshed_at": now(), "updated_at": now()}
    
    new_refresh = exchange_result.response.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        integration.credentials = {**integration.credentials, "refresh_token": new_refresh}
        updates["credentials"] = integration.credentials

    if built.row_metadata:
        integration.metadata = {**integration.metadata, **built.row_metadata}
        updates["metadata"] = integration.metadata

    if built.row_config:
        integration.config = {**integration.config, **built.row_config}
        updates["config"] = integration.config

    # `updated_at` is set explicitly because queryset .update() bypasses
    # auto_now. 
    # Zero rows matched means a concurrent reconnect replaced the
    # row — skip the write; the token we just exchanged is still valid to
    # serve this one outcome.
    updated_count = IntegrationUserCredential.objects.filter(
        pk=integration.pk, credentials__refresh_token=refresh_token,
    ).update(**updates)

    # On a lost CAS the row now belongs to a different grant — publishing
    # metadata derived from OUR stale view would misstate it. Empty metadata
    # self-heals on the next refresh cycle.
    metadata = outcome_metadata(integration) if outcome_metadata is not None and updated_count else {}
    return has_token_outcome(secrets=built.secrets, expires_in=built.expires_in, config={}, metadata=metadata)


# Refresh a shared credential's access token slightly before it lapses, so a
# broker reading the cache always gets a token with usable life left.
SHARED_TOKEN_REFRESH_MARGIN_SECONDS = 60


def _cached_shared_outcome(token_cache: dict, margin_seconds: int) -> dict | None:
    """Return a `has_token` outcome from a still-fresh cached access token, or None."""
    secrets = token_cache.get("secrets")
    expires_at = token_cache.get("expires_at")
    if not isinstance(secrets, dict) or not secrets or not isinstance(expires_at, (int, float)):
        return None
    remaining = int(expires_at - time.time())
    if remaining <= margin_seconds:
        return None
    return has_token_outcome(secrets=secrets, expires_in=remaining, config={}, metadata={})


def run_shared_refresh_exchange(
    *,
    credential: IntegrationSharedCredential | PlatformSharedCredential,
    logger: logging.Logger,
    margin_seconds: int,
    exchange: Callable[[str], ExchangeResult],
    build_secrets: Callable[[str, dict], RefreshSecrets | None],
) -> dict:
    """Refresh a *shared* OAuth credential's access token once and cache it on the row.

    A shared refresh_token (platform- or org-provisioned) backs many brokers. The
    control plane exchanges it once and caches the access token on the credential
    row (`token_cache`), fanning the cached token out so the broker swarm does not
    each hit the provider — and, for providers that rotate the refresh_token on
    exchange, do not race and orphan one another. Concurrent refreshes serialize
    on a row lock (`select_for_update`): the first exchanges, the rest read the
    freshly written cache. A revoked refresh_token is NOT auto-deleted (an admin
    must reconnect the shared credential); it surfaces `absent` and clears the cache.
    """
    fresh = _cached_shared_outcome(token_cache=credential.token_cache, margin_seconds=margin_seconds)
    if fresh is not None:
        return fresh

    model = type(credential)
    with transaction.atomic():
        locked = model.objects.select_for_update().filter(pk=credential.pk).first()
        if locked is None:
            return absent_outcome()
        # Double-checked: another worker may have refreshed while we waited on the lock.
        fresh = _cached_shared_outcome(token_cache=locked.token_cache, margin_seconds=margin_seconds)
        if fresh is not None:
            return fresh

        refresh_token = locked.credentials.get("refresh_token", "")
        if not refresh_token:
            logger.error("shared %s refresh: row missing refresh_token id=%s", locked.provider, locked.pk)
            return absent_outcome()

        result = exchange(refresh_token)
        if result.revoked:
            logger.error("shared %s refresh: refresh_token revoked upstream id=%s", locked.provider, locked.pk)
            locked.token_cache = {}
            locked.save(update_fields=["token_cache", "updated_at"])
            return absent_outcome()
        if result.error is not None:
            logger.error("shared %s refresh failed id=%s error=%s", locked.provider, locked.pk, result.error)
            return transient_outcome()

        access_token = result.response.get("access_token", "")
        if not access_token:
            logger.error("shared %s refresh: response missing access_token id=%s", locked.provider, locked.pk)
            return transient_outcome()

        built = build_secrets(access_token, result.response)
        if built is None:
            return transient_outcome()

        update_fields = ["token_cache", "updated_at"]
        new_refresh = result.response.get("refresh_token")
        if new_refresh and new_refresh != refresh_token:
            locked.credentials = {**locked.credentials, "refresh_token": new_refresh}
            update_fields.append("credentials")
        if built.row_metadata:
            locked.metadata = {**locked.metadata, **built.row_metadata}
            update_fields.append("metadata")
        locked.token_cache = {"secrets": built.secrets, "expires_at": time.time() + built.expires_in}
        locked.save(update_fields=update_fields)
        return has_token_outcome(secrets=built.secrets, expires_in=built.expires_in, config={}, metadata={})


# --- Broker refresh-outcome contract -----------------------------------------
# The batched refresh endpoint (provider_registry-driven token_refresh_batch)
# returns one of these per provider slug. `outcome` is the discriminator:
# `has_token` carries the secrets to inject + the broker cache lifetime;
# `absent` means no connected row (a normal 200 entry, not an error);
# `transient` means refresh failed and the broker should keep serving its
# still-valid cached token.


def has_token_outcome(secrets: dict, expires_in: int, config: dict, metadata: dict) -> dict:
    """Build the broker `has_token` outcome.

    OAuth providers pass empty `config`/`metadata` (the access token is the
    whole payload); vault providers pass the row's stored values.
    """
    return {
        "outcome": "has_token",
        "secrets": secrets,
        "expires_in": expires_in,
        "config": config,
        "metadata": metadata,
    }


def absent_outcome() -> dict:
    """Build the broker `absent` outcome: no connected credential row."""
    return {"outcome": "absent"}


def transient_outcome() -> dict:
    """Build the broker `transient` outcome: refresh failed; serve the cached token."""
    return {"outcome": "transient"}


# --- Control-plane device-login driver ---------------------------------------
# A *shared* OAuth credential (platform- or org-provisioned) has no per-user
# broker to run OpenAI's / Nous's device handshake, so the control plane drives
# it itself. The broker can afford an always-on asyncio poll loop; Django is
# request-scoped, so the admin's browser self-polls instead — every poll is ONE
# attempt: `device_poll` returns `pending` and the browser retries, rather than
# the request thread blocking for minutes. Each device-flow provider exposes
# `device_authorize()` (start) + `device_poll(opaque)` (one attempt); the connect
# view (`org_shared_keys.py`) owns the session state and stores the result.
# See docs/integrations/device_flow_integration_design.md for the broker analogue.

DEVICE_HTTP_TIMEOUT_SECONDS = 10

DEVICE_PENDING = "pending"
DEVICE_COMPLETED = "completed"
DEVICE_FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class DeviceAuthorization:
    """Provider-normalized device-code start result shown to the connecting admin.

    `opaque` is the provider's poll handle (e.g. Codex `device_auth_id`, Nous
    `device_code`); the connect view stashes it in the session and hands it back
    to `device_poll`. `verification_uri` is where the admin approves in a browser.
    """

    user_code: str
    verification_uri: str
    interval: int
    expires_in: int
    opaque: dict


@dataclasses.dataclass(frozen=True)
class DevicePollResult:
    """Outcome of ONE `device_poll` attempt.

    `status` discriminates: DEVICE_PENDING (not approved yet — keep polling),
    DEVICE_COMPLETED (carries the `refresh_token` to store + provider-derived
    `row_metadata`), DEVICE_FAILED (carries a human-facing `error`).
    """

    status: str
    refresh_token: str
    row_metadata: dict
    error: str


def device_pending() -> DevicePollResult:
    """A `device_poll` result meaning the admin has not approved yet."""
    return DevicePollResult(status=DEVICE_PENDING, refresh_token="", row_metadata={}, error="")


def device_completed(refresh_token: str, row_metadata: dict) -> DevicePollResult:
    """A `device_poll` result carrying the approved refresh_token to store."""
    return DevicePollResult(status=DEVICE_COMPLETED, refresh_token=refresh_token, row_metadata=row_metadata, error="")


def device_failed(error: str) -> DevicePollResult:
    """A `device_poll` result carrying a terminal, human-facing failure."""
    return DevicePollResult(status=DEVICE_FAILED, refresh_token="", row_metadata={}, error=error)
