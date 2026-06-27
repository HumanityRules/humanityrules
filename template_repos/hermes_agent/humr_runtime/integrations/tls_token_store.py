"""Token cache and HUMR refresh coordinator for TLS-intercept providers."""

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Literal

from humr_client import HumrClient
import tls_providers


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

# Browser-facing status strings rendered by the WebUI extension.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"


# Internal tags from HUMR's refresh endpoint (distinct from browser
# STATUS_* strings). For each requested slug, HUMR returns one of:
# - has_token:  a fresh secrets map (with expiry/config/metadata).
# - absent:     user not connected, or HUMR just deleted the row after
#               the upstream provider revoked the refresh_token.
# - transient:  network error or other failure that must not overwrite
#               a working cache entry.
RefreshOutcome = Literal["has_token", "absent", "transient"]

REFRESH_OUTCOME_HAS_TOKEN = "has_token"
REFRESH_OUTCOME_ABSENT = "absent"
REFRESH_OUTCOME_TRANSIENT = "transient"


def _primary_secret(secrets: dict[str, str]) -> str:
    """Return the sole secret for a single-secret provider."""
    return next(iter(secrets.values()))


@dataclass(frozen=True)
class RefreshResult:
    """Outcome for one provider in a HUMR refresh response."""

    outcome: RefreshOutcome
    secrets: dict[str, str] | None
    expires_in: int | None
    config: dict
    metadata: dict


@dataclass
class _TokenCacheEntry:
    """One provider's usable secrets for the proxy injection hot path."""

    secrets: dict[str, str]
    expires_at: float
    last_refreshed_at: str
    config: dict
    metadata: dict

    def is_fresh(self, now: float, refresh_lead_seconds: int) -> bool:
        """Return true when the token has enough life left to skip refresh."""
        return self.expires_at - now > refresh_lead_seconds

    def is_usable(self, now: float) -> bool:
        """Return true when the token has not yet expired."""
        return self.expires_at > now


@dataclass(frozen=True)
class _ConnState:
    """Durable connection state for one provider — what the status cards + gateway env read."""

    connected: bool
    last_refreshed_at: str | None
    config: dict
    metadata: dict


async def fetch_provider_tokens_batch(humr_client: HumrClient, slugs: list[str]) -> dict[str, RefreshResult]:
    """Refresh many provider tokens in one POST to HUMR; returns a slug→RefreshResult map."""
    status, payload = await humr_client.post_json(
        path="/api/integrations/tokens",
        payload={"providers": list(slugs)},
        timeout_seconds=7,
    )
    if not (200 <= status < 300):
        logger.error("refresh against HUMR failed (http %d)", status)
        return {slug: _transient_result() for slug in slugs}

    results_payload = payload.get("results")
    if not isinstance(results_payload, dict):
        logger.error("refresh response missing/malformed 'results' map")
        return {slug: _transient_result() for slug in slugs}
    return {slug: _refresh_result_from_entry(entry=results_payload.get(slug)) for slug in slugs}


def _refresh_result_from_entry(entry: object) -> RefreshResult:
    """Translate one slug's entry in the HUMR response into a RefreshResult."""
    if not isinstance(entry, dict):
        return _transient_result()
    outcome = entry.get("outcome")
    if outcome == REFRESH_OUTCOME_HAS_TOKEN:
        secrets = entry.get("secrets")
        if not isinstance(secrets, dict) or not secrets:
            logger.error("refresh has_token entry missing non-empty 'secrets' map")
            return _transient_result()
        if not all(
            isinstance(name, str) and name and isinstance(value, str) and value
            for name, value in secrets.items()
        ):
            logger.error("refresh has_token entry has non-string/empty secret name or value")
            return _transient_result()
        return RefreshResult(
            outcome=REFRESH_OUTCOME_HAS_TOKEN,
            secrets=dict(secrets),
            expires_in=int(entry.get("expires_in", 0)),
            config=entry.get("config", {}),
            metadata=entry.get("metadata", {}),
        )
    if outcome == REFRESH_OUTCOME_ABSENT:
        return RefreshResult(outcome=REFRESH_OUTCOME_ABSENT, secrets=None, expires_in=None, config={}, metadata={})
    return _transient_result()


def _transient_result() -> RefreshResult:
    """Build a transient-outcome RefreshResult sentinel for cache-preserving failures."""
    return RefreshResult(outcome=REFRESH_OUTCOME_TRANSIENT, secrets=None, expires_in=None, config={}, metadata={})


def _cache_entry_from_connected_result(result: RefreshResult, now: float) -> _TokenCacheEntry:
    """Build a cache entry from a connected refresh result."""
    if result.expires_in is None:
        raise ValueError("connected refresh result must carry expires_in")
    if not result.secrets:
        raise ValueError("connected refresh result must carry secrets")
    return _TokenCacheEntry(
        secrets=result.secrets,
        expires_at=now + result.expires_in,
        last_refreshed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        config=result.config,
        metadata=result.metadata,
    )


def _status_item_for_provider(provider: tls_providers.TlsProviderSpec, state: _ConnState | None) -> dict:
    """Serialize one TLS-intercept provider for the unified integrations payload."""
    method = provider.credential_method
    is_connected = state is not None and state.connected
    return {
        "kind": "tls_intercept",
        "category": provider.category,
        "slug": provider.slug,
        "label": provider.label,
        "logo_url": provider.logo_url,
        "status": STATUS_CONNECTED if is_connected else STATUS_NOT_CONNECTED,
        "last_refreshed_at": state.last_refreshed_at if is_connected else None,
        "config": state.config if is_connected else {},
        "metadata": state.metadata if is_connected else {},
        "connect_mode": method.connect_mode,
        "restart_required_after_save": provider.restart_gateway_after_save or provider.restart_webui_after_save,
        "affects_model_picker": provider.affects_model_picker,
    }


class _TokenStore:
    """Token cache + refresh coordinator for TLS-intercept providers."""

    def __init__(self, providers: dict[str, tls_providers.TlsProviderSpec], humr_client: HumrClient, refresh_lead_seconds: int) -> None:
        self._providers = providers
        self._host_to_provider = tls_providers.build_host_to_provider(providers=providers)
        self._humr_client = humr_client
        self._refresh_lead_seconds = refresh_lead_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, _TokenCacheEntry] = {}
        self._conn: dict[str, _ConnState] = {}

    def provider_for_host(self, host: str) -> tls_providers.TlsProviderSpec | None:
        """Lock-free: reads the immutable host→provider map built at init."""
        slug = self._host_to_provider.get(tls_providers.normalize_connect_host(host=host))
        if slug is None:
            return None
        return self._providers[slug]

    async def secrets_for_host(self, host: str) -> dict[str, str] | None:
        """Return the fresh secrets map for an upstream host, or None when disconnected."""
        provider = self.provider_for_host(host=host)
        if provider is None:
            return None
        async with self._lock:
            entry = await self._ensure_fresh_locked(provider=provider)
        if entry is None:
            return None
        return entry.secrets

    async def token_for_host(self, host: str) -> str | None:
        """Return a single fresh token for an upstream host, or None when disconnected."""
        secrets = await self.secrets_for_host(host=host)
        if secrets is None:
            return None
        return _primary_secret(secrets)

    async def invalidate(self, slug: str) -> None:
        """Drop the cached token for a provider."""
        async with self._lock:
            self._cache.pop(slug, None)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry."""
        async with self._lock:
            self._cache.clear()

    async def mark_disconnected(self, slug: str) -> None:
        """Authoritatively mark a provider disconnected after a confirmed disconnect."""
        if slug not in self._providers:
            return
        async with self._lock:
            self._cache.pop(slug, None)
            self._conn[slug] = _ConnState(connected=False, last_refreshed_at=None, config={}, metadata={})

    async def refresh(self, slug: str) -> bool:
        """Refetch one provider from HUMR even when the cache is fresh; False on transient HUMR failure."""
        if slug not in self._providers:
            return True
        async with self._lock:
            return await self._refresh_locked(slugs=[slug])

    async def refresh_all(self) -> bool:
        """Refetch every provider in one batched HUMR round-trip; False on transient HUMR failure."""
        async with self._lock:
            return await self._refresh_locked(slugs=list(self._providers))

    async def status_items(self) -> list[dict]:
        """Render integration cards from the durable connection state; never calls HUMR."""
        async with self._lock:
            return [
                _status_item_for_provider(provider=provider, state=self._conn.get(provider.slug))
                for provider in self._providers.values()
            ]

    async def gateway_env_snapshot(self) -> list[tuple[tls_providers.TlsProviderSpec, dict]]:
        """Pair every connected provider with its last-known config for env rendering."""
        async with self._lock:
            return [
                (self._providers[slug], state.config)
                for slug, state in self._conn.items()
                if state.connected
            ]

    async def _ensure_fresh_locked(self, provider: tls_providers.TlsProviderSpec) -> _TokenCacheEntry | None:
        """Single-flight refresh when the cached token is missing or near expiry. Caller holds `_lock`."""
        now = time.monotonic()
        self._prune_expired_locked(now=now)
        entry = self._cache.get(provider.slug)
        if entry is not None and entry.is_fresh(now=now, refresh_lead_seconds=self._refresh_lead_seconds):
            return entry
        await self._refresh_locked(slugs=[provider.slug])
        return self._cache.get(provider.slug)

    async def _refresh_locked(self, slugs: list[str]) -> bool:
        """Fetch the slugs in one HUMR POST and apply each result. Caller holds `_lock`."""
        if not slugs:
            return True
        import tls_intercept
        results = await tls_intercept.fetch_provider_tokens_batch(humr_client=self._humr_client, slugs=slugs)
        for slug in slugs:
            self._apply_locked(provider=self._providers[slug], result=results[slug])
        return any(result.outcome != REFRESH_OUTCOME_TRANSIENT for result in results.values())

    def _apply_locked(self, provider: tls_providers.TlsProviderSpec, result: RefreshResult) -> None:
        """Apply one refresh outcome to the injection cache and the durable connection state. Caller holds `_lock`."""
        if result.outcome == REFRESH_OUTCOME_HAS_TOKEN:
            entry = _cache_entry_from_connected_result(result=result, now=time.monotonic())
            self._cache[provider.slug] = entry
            self._conn[provider.slug] = _ConnState(
                connected=True,
                last_refreshed_at=entry.last_refreshed_at,
                config=result.config,
                metadata=result.metadata,
            )
            logger.info("refreshed %s: connected", provider.slug)
            return
        if result.outcome == REFRESH_OUTCOME_ABSENT:
            self._cache.pop(provider.slug, None)
            self._conn[provider.slug] = _ConnState(connected=False, last_refreshed_at=None, config={}, metadata={})
            logger.info("refreshed %s: not_connected", provider.slug)
            return
        logger.info("refreshed %s: transient error (cache + connection state untouched)", provider.slug)

    def _prune_expired_locked(self, now: float) -> None:
        """Drop expired cache entries. Caller holds `_lock`."""
        expired_slugs = [
            slug
            for slug, entry in self._cache.items()
            if not entry.is_usable(now=now)
        ]
        for slug in expired_slugs:
            self._cache.pop(slug, None)
