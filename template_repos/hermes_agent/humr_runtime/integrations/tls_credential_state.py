"""In-memory home for the secrets the TLS-intercept proxy injects, and for the
"is this provider connected?" facts the WebUI and gateway env need.

The proxy's hot path has one dynamic question: given a provider slug, what
real secrets should go into this request right now? The answer never comes
from disk or from the sandbox. It comes from HUMR's control plane, which
owns the long-lived material (refresh tokens, OAuth client secrets, vault
keys) and mints short-lived secrets the broker is allowed to hold. This
module is the broker-side cache of those secrets, plus the thin protocol
that refreshes them.

`CredentialStateStore` is the whole surface. It is keyed only by provider
slug — it does not know hostnames, catalog entries, or HTTP. Callers that
need "Google's card in the integrations panel" join a snapshot from here
with a static `TlsProviderSpec` in `tls_intercept`.

Two kinds of state live side by side and must not be confused:

- The **token cache** holds the injectable secrets (`access_token`, Slack's
  bot + app tokens, and so on) with an expiry. The proxy prunes entries as
  they age out, and refreshes ahead of expiry when a request needs them.
  A missing or expired entry is "we cannot inject right now," not
  "the user disconnected."
- The **connection state** (`ProviderConnectionState`) is whether HUMR
  currently has a grant for this provider, plus the last-known `config`
  and `metadata` that status cards and env bindings need. It changes only
  when a refresh comes back `has_token` or `absent`, or on an explicit
  disconnect. An idle provider whose access token has been pruned still
  reads as connected; its card and env bindings survive.

Every refresh is the same batched call to HUMR
(`POST /api/integrations/tokens`), whether bootstrap is asking about every
slug or the proxy hot path is asking about one. HUMR answers per slug with
one of three outcomes:

- `has_token` — here are fresh secrets (and config/metadata); cache them.
- `absent` — the user is not connected (or the grant was revoked); clear
  the cache and mark disconnected. This is a normal 200 entry, not an
  HTTP error.
- `transient` — HUMR hiccuped; leave whatever we already have alone. A
  still-unexpired cached secret keeps serving so a brief outage does not
  look like a disconnect.

There is no per-provider timer and no background refresh loop. Fetches are
triggered by broker bootstrap, by a request whose cache entry is missing
or near expiry, by a known state change (connect / disconnect / vault
save / device-flow completion), or by an explicit Refresh in the UI.
A single lock serializes cache mutation and any read that needs a
consistent view, so a parked fetch cannot write past a concurrent token drop.
"""

import asyncio
import copy
import datetime as dt
import logging
import time
from dataclasses import dataclass
from typing import Literal

from humr_client import HumrClient


logger = logging.getLogger("tls_credential_state")


# Internal tags from HUMR's refresh endpoint. For each requested slug, HUMR returns one of:
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
    """One provider's usable secrets for the proxy injection hot path."""

    secrets: dict[str, str]
    expires_at: float

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
class ActiveCredential:
    """Secrets to inject plus provenance facts from the same refresh.

    `secrets` is a name→value map (multi-secret providers like Slack need the
    whole map so the request rewrite can pick the right secret per call).
    `platform_shared` is HUMR's provenance stamp on the serving credential —
    True only when HUMR's own platform-tier credential produced these secrets
    (vs the user's personal or an org-shared credential). Read together under
    one lock so the flag can never describe a different credential than the
    secrets came from.
    """

    secrets: dict[str, str]
    platform_shared: bool


@dataclass(frozen=True)
class ProviderConnectionState:
    """Cache-independent connection state for one provider.

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
        # config/metadata flow into ProviderConnectionState and out through the
        # runtime projections. Coerce malformed CP values to {} rather than let
        # them poison the connection state.
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
    )


class CredentialStateStore:
    """Token cache + connection-state refresh coordinator, keyed by provider slug.

    A single `asyncio.Lock` serializes every cache mutation and every
    read that needs an internally-consistent view (connection snapshot,
    proxy hot-path single-flight refresh).
    Replaces the older per-slug `_refresh_locks` + `_cache_lock` pair —
    the additional cross-slug parallelism that bought us doesn't matter
    in this broker (low concurrent traffic, in-VPC HUMR),
    and a single lock makes "a parked fetch wrote past a concurrent token drop"
    structurally impossible: fetch and apply always run under the same
    lock together.
    """

    def __init__(self, provider_slugs: tuple[str, ...], humr_client: HumrClient, refresh_lead_seconds: int) -> None:
        self._provider_slugs = provider_slugs
        self._provider_slug_set = frozenset(provider_slugs)
        self._humr_client = humr_client
        self._refresh_lead_seconds = refresh_lead_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, _TokenCacheEntry] = {}
        self._connection_states: dict[str, ProviderConnectionState] = {}

    async def credential_for_slug(self, slug: str) -> ActiveCredential | None:
        """Return the fresh active credential for a provider slug, or None when unknown or disconnected.

        Secrets and the `platform_shared` provenance flag are read under the
        same lock, so they always describe the same refresh outcome
        (`_apply_locked` writes cache and connection state together).
        """
        if slug not in self._provider_slug_set:
            return None
        async with self._lock:
            entry = await self._ensure_fresh_locked(slug=slug)
            if entry is None:
                return None
            state = self._connection_states.get(slug)
            platform_shared = state is not None and bool(state.metadata.get("platform_shared"))
            return ActiveCredential(secrets=dict(entry.secrets), platform_shared=platform_shared)

    async def drop_cached_token(self, slug: str) -> None:
        """Drop the cached token for a provider."""
        async with self._lock:
            self._cache.pop(slug, None)

    async def drop_all_cached_tokens(self) -> None:
        """Drop every cached token entry."""
        async with self._lock:
            self._cache.clear()

    async def mark_disconnected(self, slug: str) -> None:
        """Authoritatively mark a provider disconnected after a confirmed disconnect.

        Mirrors an `absent` apply (drop the cache entry and mark the state not-connected)
        but driven by a known disconnect rather than a refresh outcome. The
        disconnect handler holds HUMR's authoritative row-deletion, so applying it
        directly here keeps a transient follow-up refresh — which leaves connection state
        untouched — from leaving the card connected after the user just disconnected.
        Unlike `drop_cached_token` (used for connect-of-another-slug and the 401 evict,
        where truth is only known after the next refresh), the disconnect outcome
        is already known, so it's safe to flip the connection state here.
        """
        if slug not in self._provider_slug_set:
            return
        async with self._lock:
            self._cache.pop(slug, None)
            self._connection_states[slug] = ProviderConnectionState(connected=False, last_refreshed_at=None, config={}, metadata={})

    async def refresh(self, slug: str) -> bool:
        """Refetch one provider from HUMR even when the cache is fresh; False on transient HUMR failure."""
        if slug not in self._provider_slug_set:
            return True
        async with self._lock:
            return await self._refresh_locked(slugs=[slug])

    async def refresh_all(self) -> bool:
        """Refetch every provider in one batched HUMR round-trip; False on transient HUMR failure."""
        async with self._lock:
            return await self._refresh_locked(slugs=list(self._provider_slugs))

    async def connection_snapshot(self) -> dict[str, ProviderConnectionState]:
        """Copy the cache-independent connection state without calling HUMR."""
        async with self._lock:
            return {
                slug: ProviderConnectionState(
                    connected=state.connected,
                    last_refreshed_at=state.last_refreshed_at,
                    config=copy.deepcopy(state.config),
                    metadata=copy.deepcopy(state.metadata),
                )
                for slug, state in self._connection_states.items()
            }

    async def _ensure_fresh_locked(self, slug: str) -> _TokenCacheEntry | None:
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
        entry = self._cache.get(slug)
        if entry is not None and entry.is_fresh(now=now, refresh_lead_seconds=self._refresh_lead_seconds):
            return entry
        await self._refresh_locked(slugs=[slug])
        return self._cache.get(slug)

    async def _refresh_locked(self, slugs: list[str]) -> bool:
        """Fetch the slugs in one HUMR POST and apply each result. Caller holds `_lock`.

        Holding the lock across both fetch and apply (rather than
        dropping it during the network call) is what prevents a parked
        fetch from overwriting a concurrent token drop. The cost is
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
            self._apply_locked(slug=slug, result=results[slug])
        return any(result.outcome != REFRESH_OUTCOME_TRANSIENT for result in results.values())

    def _apply_locked(self, slug: str, result: RefreshResult) -> None:
        """Apply one refresh outcome to the injection cache and connection state. Caller holds `_lock`.

        - has_token → write the injection entry AND mark the connection state connected.
        - absent → drop any prior entry AND mark the connection state not-connected (idempotent).
        - transient → leave BOTH untouched (don't replace a working token with a
          sentinel, and don't flip a card on a network blip; the prior state, if
          any, stays available).

        Every refresh caller flows through here — bootstrap, explicit refresh, and
        the proxy hot path (`_ensure_fresh_locked`) — so a card self-heals on the
        first real request after an idle token expiry, exactly as the cache does.
        """
        if result.outcome == REFRESH_OUTCOME_HAS_TOKEN:
            entry = _cache_entry_from_connected_result(result=result, now=time.monotonic())
            self._cache[slug] = entry
            self._connection_states[slug] = ProviderConnectionState(
                connected=True,
                last_refreshed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                config=result.config,
                metadata=result.metadata,
            )
            logger.info("refreshed %s: connected", slug)
            return
        if result.outcome == REFRESH_OUTCOME_ABSENT:
            self._cache.pop(slug, None)
            self._connection_states[slug] = ProviderConnectionState(connected=False, last_refreshed_at=None, config={}, metadata={})
            logger.info("refreshed %s: not_connected", slug)
            return
        logger.info("refreshed %s: transient error (cache + connection state untouched)", slug)

    def _prune_expired_locked(self, now: float) -> None:
        """Drop expired cache entries. Caller holds `_lock`."""
        expired_slugs = [
            slug
            for slug, entry in self._cache.items()
            if not entry.is_usable(now=now)
        ]
        for slug in expired_slugs:
            self._cache.pop(slug, None)
