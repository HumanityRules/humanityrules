"""Access-token cache, HUMR refresh protocol, and durable connection state.

`TokenStore` answers the proxy's one question — what is the real secret for
this host — from an in-memory, expiry-pruned cache of the short-lived secrets
HUMR hands back, refreshed under a single lock. Refresh tokens and OAuth
client secrets never reach this process; HUMR performs the upstream exchange
and returns only what the proxy injects.

Alongside the cache, and deliberately decoupled from it, sits the durable
per-provider connection state that the WebUI status cards and the gateway env
render read. The two have different lifetimes: an access token expires on its
own (1–8h) and is pruned on the injection path, while connection state changes
only on a refresh outcome or an explicit disconnect. A lapsed token is not a
disconnect, so cache presence is not what a status card reports.
"""

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Literal

from humr_client import HumrClient
import tls_provider_catalog


logger = logging.getLogger("tls_token_store")


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


@dataclass(frozen=True)
class RefreshResult:
    """Outcome for one provider in a HUMR refresh response.

    `secrets` is a name→value map (e.g. `{"access_token": "ya29…"}`), so a
    provider can carry more than one credential (Slack's bot + app token).
    `None` for the absent/transient outcomes, which have nothing to cache.
    """

    outcome: RefreshOutcome
    secrets: dict[str, str] | None
    expires_in: int | None
    config: dict
    metadata: dict


@dataclass
class _TokenCacheEntry:
    """One provider's usable secrets for the proxy injection hot path.

    `secrets` is a name→value map; single-secret providers carry one entry.
    This cache holds only the short-lived access token to inject upstream and is
    pruned by expiry on the injection path. Durable connection state — what the
    status cards and gateway env render read — lives in `_ConnState`, so cache
    presence no longer means "connected". `config`/`metadata`/`last_refreshed_at`
    are carried here so `_apply_locked` can project them into `_ConnState` from
    the same has_token result.
    """

    secrets: dict[str, str]
    expires_at: float
    last_refreshed_at: str
    config: dict
    metadata: dict

    def is_fresh(self, now: float, refresh_lead_seconds: int) -> bool:
        """Return true when the token has enough life left to skip refresh."""
        return self.expires_at - now > refresh_lead_seconds

    def is_usable(self, now: float) -> bool:
        """Return true when the token has not yet expired.

        Distinct from `is_fresh`: a token can be unfresh (inside the lead
        window, so we'd prefer to refresh) yet still usable (expires_at is
        in the future). The proxy hot path serves usable tokens when a
        refresh-ahead transiently failed — better than failing the
        sandbox's request because HUMR had a hiccup.
        """
        return self.expires_at > now


@dataclass(frozen=True)
class _ConnState:
    """Durable connection state for one provider — what the status cards + gateway env read.

    Decoupled from `_TokenCacheEntry`: the access-token cache expires on its own
    (1–8h) and is pruned on the injection path, but connection state changes only
    on a refresh OUTCOME (has_token/absent) or an explicit disconnect. `config`/
    `metadata` are the last-known-good values from the most recent has_token, so a
    card or env binding survives token expiry; they refresh on the next successful
    refresh of the slug (connect/disconnect/vault-save always trigger one).
    """

    connected: bool
    last_refreshed_at: str | None
    config: dict
    metadata: dict


async def fetch_provider_tokens_batch(humr_client: HumrClient, slugs: list[str]) -> dict[str, RefreshResult]:
    """Refresh many provider tokens in one POST to HUMR; returns a slug→RefreshResult map.

    HUMR's `/api/integrations/tokens` is the broker's only refresh path —
    both Refresh-all/bootstrap and single-slug refresh (after a
    connect/disconnect) call this with the appropriate slug list. The
    endpoint returns `absent` as a normal entry rather than HTTP 404.

    Any transport-level error, unparseable response, or slug missing
    from the response map surfaces as TRANSIENT for that slug, so the
    cache stays intact.
    """
    # In-VPC JSON POST to our own control plane; healthy P99 is tens
    # of ms. HUMR processes the providers in parallel server-side, so
    # wall-clock = max(per-provider upstream exchange) + DB / JSON
    # overhead. Each helper's upstream timeout is 5s, so the ceiling
    # here is ~5s + a small slack budget for marshalling — 7s. Still
    # well inside supervisor's 10s wait_for_port budget on broker
    # bootstrap.
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
        # Reject (don't coerce) malformed entries: a non-string/empty value
        # would otherwise be cached and sent upstream as a literal bearer
        # token (e.g. str(None) == "None"). Degrade to a cache-preserving
        # transient instead, exactly like a network failure.
        if not all(
            isinstance(name, str) and name and isinstance(value, str) and value
            for name, value in secrets.items()
        ):
            logger.error("refresh has_token entry has non-string/empty secret name or value")
            return _transient_result()
        # config/metadata flow into _ConnState and out to the WebUI card;
        # coerce non-dict values (a malformed CP response) to {} rather than
        # letting them poison the durable connection state.
        config = entry.get("config", {})
        metadata = entry.get("metadata", {})
        return RefreshResult(
            outcome=REFRESH_OUTCOME_HAS_TOKEN,
            secrets=dict(secrets),
            expires_in=int(entry.get("expires_in", 0)),
            config=config if isinstance(config, dict) else {},
            metadata=metadata if isinstance(metadata, dict) else {},
        )
    if outcome == REFRESH_OUTCOME_ABSENT:
        return RefreshResult(outcome=REFRESH_OUTCOME_ABSENT, secrets=None, expires_in=None, config={}, metadata={})
    return _transient_result()


def _transient_result() -> RefreshResult:
    """Build a transient-outcome RefreshResult sentinel for cache-preserving failures."""
    return RefreshResult(outcome=REFRESH_OUTCOME_TRANSIENT, secrets=None, expires_in=None, config={}, metadata={})


def _cache_entry_from_connected_result(result: RefreshResult, now: float) -> _TokenCacheEntry:
    """Build a cache entry from a connected refresh result.

    Caller is responsible for only invoking this on `REFRESH_OUTCOME_HAS_TOKEN`
    results — absent/transient outcomes don't have a token to cache.
    """
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


def _status_item_for_provider(provider: tls_provider_catalog.TlsProviderSpec, state: _ConnState | None) -> dict:
    """Serialize one TLS-intercept provider for the unified integrations payload.

    Connected = a durable `_ConnState` says so; not_connected = it doesn't, or
    there's no state yet. Independent of the access-token cache: a connected
    provider whose injection token has expired (and been pruned) still renders
    connected until a refresh outcome or disconnect says otherwise.
    """
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


class TokenStore:
    """Token cache + refresh coordinator for TLS-intercept providers.

    A single `asyncio.Lock` serializes every cache mutation and every
    read that needs an internally-consistent view (status render,
    gateway env snapshot, proxy hot-path single-flight refresh).
    Replaces the older per-slug `_refresh_locks` + `_cache_lock` pair —
    the additional cross-slug parallelism that bought us doesn't matter
    in this broker (3 providers, low concurrent traffic, in-VPC HUMR),
    and a single lock makes "a parked fetch wrote past an invalidate"
    structurally impossible: fetch and apply always run under the same
    lock together.
    """

    def __init__(self, providers: dict[str, tls_provider_catalog.TlsProviderSpec], humr_client: HumrClient, refresh_lead_seconds: int) -> None:
        self._providers = providers
        self._host_to_provider = tls_provider_catalog.build_host_to_provider(providers=providers)
        self._humr_client = humr_client
        self._refresh_lead_seconds = refresh_lead_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, _TokenCacheEntry] = {}
        self._conn: dict[str, _ConnState] = {}

    def provider_for_host(self, host: str) -> tls_provider_catalog.TlsProviderSpec | None:
        """Lock-free: reads the immutable host→provider map built at init."""
        slug = self._host_to_provider.get(tls_provider_catalog.normalize_connect_host(host=host))
        if slug is None:
            return None
        return self._providers[slug]

    async def secrets_for_host(self, host: str) -> dict[str, str] | None:
        """Return the fresh secrets map for an upstream host, or None when disconnected.

        Multi-secret providers (Slack) need the whole map so the request
        rewrite can pick the right secret per call; single-secret providers
        get a one-entry map.
        """
        provider = self.provider_for_host(host=host)
        if provider is None:
            return None
        async with self._lock:
            entry = await self._ensure_fresh_locked(provider=provider)
        if entry is None:
            return None
        return entry.secrets

    async def invalidate(self, slug: str) -> None:
        """Drop the cached token for a provider."""
        async with self._lock:
            self._cache.pop(slug, None)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry."""
        async with self._lock:
            self._cache.clear()

    async def mark_disconnected(self, slug: str) -> None:
        """Authoritatively mark a provider disconnected after a confirmed disconnect.

        Mirrors an `absent` apply (drop the cache entry, set `_conn` not-connected)
        but driven by a known disconnect rather than a refresh outcome. The
        disconnect handler holds HUMR's authoritative row-deletion, so applying it
        directly here keeps a transient follow-up refresh — which leaves `_conn`
        untouched — from leaving the card connected after the user just disconnected.
        Unlike `invalidate` (used for connect-of-another-slug and the 401 evict,
        where truth is only known after the next refresh), the disconnect outcome
        is already known, so it's safe to flip `_conn` here.
        """
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
        """Render integration cards from the durable connection state; never calls HUMR.

        A pure projection of `_conn`, which `_apply_locked` updates from refresh
        outcomes (boot bootstrap, the proxy hot path, explicit invalidate/refresh)
        and `mark_disconnected` updates on an explicit disconnect. Deliberately
        does NOT prune by token expiry: an idle provider whose injection token
        lapsed is still connected — expiry-pruning belongs only on the injection
        path, so a card answers "does HUMR hold a usable credential", not "is a
        warm token cached".
        """
        async with self._lock:
            return [
                _status_item_for_provider(provider=provider, state=self._conn.get(provider.slug))
                for provider in self._providers.values()
            ]

    async def gateway_env_snapshot(self) -> list[tuple[tls_provider_catalog.TlsProviderSpec, dict]]:
        """Pair every connected provider with its last-known config for env rendering.

        Projects `_conn` (durable connection state), not the access-token cache, so
        a connected vault provider's env bindings survive token expiry — otherwise
        an idle provider past its cache TTL would be stripped from the managed block
        and trigger a spurious gateway restart while its card still showed connected.
        The broker calls `refresh_all()` first when it wants `_conn` aligned with
        HUMR state.
        """
        async with self._lock:
            return [
                (self._providers[slug], state.config)
                for slug, state in self._conn.items()
                if state.connected
            ]

    async def _ensure_fresh_locked(self, provider: tls_provider_catalog.TlsProviderSpec) -> _TokenCacheEntry | None:
        """Single-flight refresh when the cached token is missing or near expiry. Caller holds `_lock`.

        Returns the cache entry to use for this request, or None when the
        provider is genuinely unavailable. Two cases to keep separate:

        - **Refresh succeeded** (has_token/absent): the cache reflects HUMR
          truth, so we return whatever's now in the cache.
        - **Refresh transient-failed**: the cache is untouched. If we had a
          prior entry that's still un-expired, hand it back — the proxy
          can use it for the rest of its expires_at window rather than
          surfacing "not connected" to the sandbox because HUMR hiccuped.
          Only return None when even the prior token is past expiry.
        """
        now = time.monotonic()
        self._prune_expired_locked(now=now)
        entry = self._cache.get(provider.slug)
        if entry is not None and entry.is_fresh(now=now, refresh_lead_seconds=self._refresh_lead_seconds):
            return entry
        await self._refresh_locked(slugs=[provider.slug])
        return self._cache.get(provider.slug)

    async def _refresh_locked(self, slugs: list[str]) -> bool:
        """Fetch the slugs in one HUMR POST and apply each result. Caller holds `_lock`.

        Holding the lock across both fetch and apply (rather than
        dropping it during the network call) is what prevents a parked
        fetch from overwriting a concurrent invalidate. The cost is
        small in practice: ~tens of ms per refresh, and at most one
        refresh per provider per token lifetime hits this path.

        Returns False when *every* slug came back transient — the
        signature of a failed HUMR round-trip — so callers can avoid
        deriving state (e.g. the gateway env file) from a cache that
        does not reflect HUMR truth.
        """
        if not slugs:
            return True
        results = await fetch_provider_tokens_batch(humr_client=self._humr_client, slugs=slugs)
        for slug in slugs:
            self._apply_locked(provider=self._providers[slug], result=results[slug])
        return any(result.outcome != REFRESH_OUTCOME_TRANSIENT for result in results.values())

    def _apply_locked(self, provider: tls_provider_catalog.TlsProviderSpec, result: RefreshResult) -> None:
        """Apply one refresh outcome to the injection cache and the durable connection state. Caller holds `_lock`.

        - has_token → write the injection entry AND mark `_conn` connected.
        - absent → drop any prior entry AND mark `_conn` not-connected (idempotent).
        - transient → leave BOTH untouched (don't replace a working token with a
          sentinel, and don't flip a card on a network blip; the prior state, if
          any, stays available).

        Every refresh caller flows through here — bootstrap, explicit refresh, and
        the proxy hot path (`_ensure_fresh_locked`) — so a card self-heals on the
        first real request after an idle token expiry, exactly as the cache does.
        """
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
