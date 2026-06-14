"""TLS-intercept proxy runtime for platform-managed provider tokens.

The provider catalog (credential-method dataclasses, `TlsProviderSpec`, and
the per-provider specs) lives in `tls_providers`; this module is the
mechanism that consumes it: the MITM proxy, the token store, and the cert
minter, composed by `TlsInterceptRuntime`.
"""

import asyncio
import base64
import contextlib
import datetime as dt
import ipaddress
import json
import logging
import os
import random
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from doh_client import DohClient
import tls_providers


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

# Browser-facing status strings rendered by the WebUI extension.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"


# Internal tags from DOH's refresh endpoint (distinct from browser
# STATUS_* strings). For each requested slug, DOH returns one of:
# - has_token:  a fresh secrets map (with expiry/config/metadata).
# - absent:     user not connected, or DOH just deleted the row after
#               the upstream provider revoked the refresh_token.
# - transient:  network error or other failure that must not overwrite
#               a working cache entry.
RefreshOutcome = Literal["has_token", "absent", "transient"]

REFRESH_OUTCOME_HAS_TOKEN = "has_token"
REFRESH_OUTCOME_ABSENT = "absent"
REFRESH_OUTCOME_TRANSIENT = "transient"


def _primary_secret(secrets: dict[str, str]) -> str:
    """Return the sole secret for a single-secret provider.

    Single-secret methods (OAuthHeader, VaultUrlRewrite) carry exactly one
    secret, so the primary is unambiguous. Multi-secret providers (Slack)
    select per request in `_rewrite_request_for_provider` and don't use this.
    """
    return next(iter(secrets.values()))


@dataclass(frozen=True)
class RefreshResult:
    """Outcome for one provider in a DOH refresh response.

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
    """One provider's usable secrets plus the metadata we hand to the gateway/UI.

    `secrets` is a name→value map; single-secret providers carry one entry.
    The token store prunes expired entries before returning cache snapshots,
    so cache presence means "connected".
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
        sandbox's request because DOH had a hiccup.
        """
        return self.expires_at > now


async def fetch_provider_tokens_batch(doh_client: DohClient, slugs: list[str]) -> dict[str, RefreshResult]:
    """Refresh many provider tokens in one POST to DOH; returns a slug→RefreshResult map.

    DOH's `/api/integrations/tokens` is the broker's only refresh path —
    both Refresh-all/bootstrap and single-slug refresh (after a
    connect/disconnect) call this with the appropriate slug list. The
    endpoint returns `absent` as a normal entry rather than HTTP 404.

    Any transport-level error, unparseable response, or slug missing
    from the response map surfaces as TRANSIENT for that slug, so the
    cache stays intact.
    """
    # In-VPC JSON POST to our own control plane; healthy P99 is tens
    # of ms. DOH processes the providers in parallel server-side, so
    # wall-clock = max(per-provider upstream exchange) + DB / JSON
    # overhead. Each helper's upstream timeout is 5s, so the ceiling
    # here is ~5s + a small slack budget for marshalling — 7s. Still
    # well inside supervisor's 10s wait_for_port budget on broker
    # bootstrap.
    status, payload = await doh_client.post_json(
        path="/api/integrations/tokens",
        payload={"providers": list(slugs)},
        timeout_seconds=7,
    )
    if not (200 <= status < 300):
        logger.error("refresh against DOH failed (http %d)", status)
        return {slug: _transient_result() for slug in slugs}

    results_payload = payload.get("results")
    if not isinstance(results_payload, dict):
        logger.error("refresh response missing/malformed 'results' map")
        return {slug: _transient_result() for slug in slugs}
    return {slug: _refresh_result_from_entry(entry=results_payload.get(slug)) for slug in slugs}


def _refresh_result_from_entry(entry: object) -> RefreshResult:
    """Translate one slug's entry in the DOH response into a RefreshResult."""
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


def _status_item_for_provider(provider: tls_providers.TlsProviderSpec, entry: _TokenCacheEntry | None) -> dict:
    """Serialize one TLS-intercept provider for the unified integrations payload.

    Connected = cache entry exists; not_connected = it doesn't. The token
    store prunes expired entries before status rendering, so presence means
    the proxy still has a usable token.
    """
    method = provider.credential_method
    is_connected = entry is not None
    return {
        "kind": "tls_intercept",
        "category": provider.category,
        "slug": provider.slug,
        "label": provider.label,
        "logo_url": provider.logo_url,
        "status": STATUS_CONNECTED if is_connected else STATUS_NOT_CONNECTED,
        "last_refreshed_at": entry.last_refreshed_at if is_connected else None,
        "config": entry.config if is_connected else {},
        "metadata": entry.metadata if is_connected else {},
        "connect_mode": method.connect_mode,
        "restart_required_after_save": provider.restart_gateway_after_save or provider.restart_webui_after_save,
        "affects_model_picker": provider.affects_model_picker,
    }


class _TokenStore:
    """Token cache + refresh coordinator for TLS-intercept providers.

    A single `asyncio.Lock` serializes every cache mutation and every
    read that needs an internally-consistent view (status render,
    gateway env snapshot, proxy hot-path single-flight refresh).
    Replaces the older per-slug `_refresh_locks` + `_cache_lock` pair —
    the additional cross-slug parallelism that bought us doesn't matter
    in this broker (3 providers, low concurrent traffic, in-VPC DOH),
    and a single lock makes "a parked fetch wrote past an invalidate"
    structurally impossible: fetch and apply always run under the same
    lock together.
    """

    def __init__(self, providers: dict[str, tls_providers.TlsProviderSpec], doh_client: DohClient, refresh_lead_seconds: int) -> None:
        self._providers = providers
        self._host_to_provider = tls_providers.build_host_to_provider(providers=providers)
        self._doh_client = doh_client
        self._refresh_lead_seconds = refresh_lead_seconds
        self._lock = asyncio.Lock()
        self._cache: dict[str, _TokenCacheEntry] = {}

    def provider_for_host(self, host: str) -> tls_providers.TlsProviderSpec | None:
        """Lock-free: reads the immutable host→provider map built at init."""
        slug = self._host_to_provider.get(tls_providers.normalize_connect_host(host=host))
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

    async def token_for_host(self, host: str) -> str | None:
        """Return a single fresh token for an upstream host, or None when disconnected.

        Convenience wrapper over `secrets_for_host` for single-secret providers.
        """
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

    async def refresh(self, slug: str) -> bool:
        """Refetch one provider from DOH even when the cache is fresh; False on transient DOH failure."""
        if slug not in self._providers:
            return True
        async with self._lock:
            return await self._refresh_locked(slugs=[slug])

    async def refresh_all(self) -> bool:
        """Refetch every provider in one batched DOH round-trip; False on transient DOH failure."""
        async with self._lock:
            return await self._refresh_locked(slugs=list(self._providers))

    async def status_items(self) -> list[dict]:
        """Render integration cards from the current cache; never calls DOH.

        Cache writes happen on three paths: boot bootstrap, the proxy hot
        path (`token_for_host` near-expiry refresh), and explicit user
        invalidate/refresh. Status reads are a pure projection of the
        usable cache.
        """
        async with self._lock:
            self._prune_expired_locked(now=time.monotonic())
            return [
                _status_item_for_provider(provider=provider, entry=self._cache.get(provider.slug))
                for provider in self._providers.values()
            ]

    async def gateway_env_snapshot(self) -> list[tuple[tls_providers.TlsProviderSpec, dict]]:
        """Pair every connected provider with its cached config for env rendering.

        Returns `(provider, config)` tuples for providers currently in
        the usable cache. The broker calls `refresh_all()` first when it
        wants the cache aligned with DOH state.
        """
        async with self._lock:
            self._prune_expired_locked(now=time.monotonic())
            return [
                (self._providers[slug], entry.config)
                for slug, entry in self._cache.items()
            ]

    async def _ensure_fresh_locked(self, provider: tls_providers.TlsProviderSpec) -> _TokenCacheEntry | None:
        """Single-flight refresh when the cached token is missing or near expiry. Caller holds `_lock`.

        Returns the cache entry to use for this request, or None when the
        provider is genuinely unavailable. Two cases to keep separate:

        - **Refresh succeeded** (has_token/absent): the cache reflects DOH
          truth, so we return whatever's now in the cache.
        - **Refresh transient-failed**: the cache is untouched. If we had a
          prior entry that's still un-expired, hand it back — the proxy
          can use it for the rest of its expires_at window rather than
          surfacing "not connected" to the sandbox because DOH hiccuped.
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
        """Fetch the slugs in one DOH POST and apply each result. Caller holds `_lock`.

        Holding the lock across both fetch and apply (rather than
        dropping it during the network call) is what prevents a parked
        fetch from overwriting a concurrent invalidate. The cost is
        small in practice: ~tens of ms per refresh, and at most one
        refresh per provider per token lifetime hits this path.

        Returns False when *every* slug came back transient — the
        signature of a failed DOH round-trip — so callers can avoid
        deriving state (e.g. the gateway env file) from a cache that
        does not reflect DOH truth.
        """
        if not slugs:
            return True
        results = await fetch_provider_tokens_batch(doh_client=self._doh_client, slugs=slugs)
        for slug in slugs:
            self._apply_locked(provider=self._providers[slug], result=results[slug])
        return any(result.outcome != REFRESH_OUTCOME_TRANSIENT for result in results.values())

    def _apply_locked(self, provider: tls_providers.TlsProviderSpec, result: RefreshResult) -> None:
        """Apply one refresh outcome to the cache. Caller holds `_lock`.

        - has_token → write the new entry.
        - absent → drop any prior entry (idempotent).
        - transient → leave the cache untouched (don't replace a working
          token with a sentinel; the prior entry, if any, stays available).
        """
        if result.outcome == REFRESH_OUTCOME_HAS_TOKEN:
            self._cache[provider.slug] = _cache_entry_from_connected_result(result=result, now=time.monotonic())
            logger.info("refreshed %s: connected", provider.slug)
            return
        if result.outcome == REFRESH_OUTCOME_ABSENT:
            self._cache.pop(provider.slug, None)
            logger.info("refreshed %s: not_connected", provider.slug)
            return
        logger.info("refreshed %s: transient error (cache untouched)", provider.slug)

    def _prune_expired_locked(self, now: float) -> None:
        """Drop expired cache entries. Caller holds `_lock`."""
        expired_slugs = [
            slug
            for slug, entry in self._cache.items()
            if not entry.is_usable(now=now)
        ]
        for slug in expired_slugs:
            self._cache.pop(slug, None)


class TlsInterceptRuntime:
    """TLS-intercept subsystem: proxy transport, token refresh, and status cards.

    Pure mechanism: invalidate/refresh only touch the token cache. The
    choreography that follows a *credential change* (env re-render, process
    restarts, auth markers) lives in `credentials_service`, which calls down
    into this runtime — never the other way around.
    """

    def __init__(self, providers: dict[str, tls_providers.TlsProviderSpec], doh_client: DohClient, refresh_lead_seconds: int, ca_dir: Path, private_dir: Path) -> None:
        self._token_store = _TokenStore(
            providers=providers,
            doh_client=doh_client,
            refresh_lead_seconds=refresh_lead_seconds,
        )
        self._cert_minter = _CertMinter(ca_dir=ca_dir, private_dir=private_dir)
        self._cert_minter.bootstrap()

    async def start_proxy_server(self, host: str, port: int) -> asyncio.Server:
        """Start the local HTTPS proxy server."""
        async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _handle_proxy_conn(
                reader=reader,
                writer=writer,
                minter=self._cert_minter,
                token_store=self._token_store,
            )

        return await asyncio.start_server(client_connected_cb=handle_conn, host=host, port=port)

    async def status_items(self) -> list[dict]:
        """Return TLS-intercept integration cards."""
        return await self._token_store.status_items()

    async def invalidate(self, slug: str) -> None:
        """Drop one provider's cached token entry."""
        await self._token_store.invalidate(slug=slug)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry."""
        await self._token_store.invalidate_all()

    async def refresh_slug(self, slug: str) -> bool:
        """Force a single-provider refetch; False when the DOH round-trip failed transiently."""
        return await self._token_store.refresh(slug=slug)

    async def refresh_all(self) -> bool:
        """Force a refetch of every provider in one DOH round-trip; False on transient failure."""
        return await self._token_store.refresh_all()

    async def gateway_env_snapshot(self) -> list[tuple[tls_providers.TlsProviderSpec, dict]]:
        """Pair every connected provider with its cached config for env rendering."""
        return await self._token_store.gateway_env_snapshot()


class _CertMinter:
    """Boot-generated CA that mints leaf certs on demand, one per SNI hostname."""

    def __init__(self, ca_dir: Path, private_dir: Path) -> None:
        self._ca_dir = ca_dir
        self._private_dir = private_dir
        self._ca_key: rsa.RSAPrivateKey | None = None
        self._ca_cert: x509.Certificate | None = None
        self._leaf_cache: dict[str, ssl.SSLContext] = {}

    def bootstrap(self) -> None:
        """Generate the CA and write bundle.pem. Called once at broker startup."""
        self._ca_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
        subject = issuer = x509.Name([
            x509.NameAttribute(x509.NameOID.COMMON_NAME, "DOH Integrations Broker CA"),
        ])
        now = dt.datetime.now(dt.timezone.utc)
        ca_ski = x509.SubjectKeyIdentifier.from_public_key(self._ca_key.public_key())
        self._ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self._ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * 5))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_cert_sign=True, crl_sign=True,
                    key_encipherment=False, content_commitment=False, data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(ca_ski, critical=False)
            .sign(private_key=self._ca_key, algorithm=hashes.SHA256())
        )
        self._write_bundle()

    def _write_bundle(self) -> None:
        """Write the CA cert + system roots into bundle.pem for SSL_CERT_FILE."""
        self._ca_dir.mkdir(parents=True, exist_ok=True)
        self._private_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self._private_dir, 0o700)
        our_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM)
        system_roots = b""
        for candidate in (Path("/etc/ssl/certs/ca-certificates.crt"), Path("/etc/pki/tls/certs/ca-bundle.crt")):
            if candidate.exists():
                system_roots = candidate.read_bytes()
                break
        if not system_roots:
            logger.error("no system root bundle found; broker-trusted bundle will be DOH-only")
        bundle_path = self._ca_dir / "bundle.pem"
        bundle_path.write_bytes(our_pem + b"\n" + system_roots)
        os.chmod(bundle_path, 0o644)
        logger.info("wrote CA bundle to %s (system roots included: %s)", bundle_path, bool(system_roots))

    def context_for(self, hostname: str) -> ssl.SSLContext:
        """Return an SSLContext presenting a leaf cert valid for hostname."""
        if hostname in self._leaf_cache:
            return self._leaf_cache[hostname]
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = dt.datetime.now(dt.timezone.utc)
        san_entries: list[x509.GeneralName] = []
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(hostname)))
        except ValueError:
            san_entries.append(x509.DNSName(hostname))
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, hostname)]))
            .issuer_name(self._ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * 2))
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, key_encipherment=True,
                    content_commitment=False, data_encipherment=False,
                    key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()), critical=False)
            .sign(private_key=self._ca_key, algorithm=hashes.SHA256())
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert_path = self._pem_bytes_to_private_tmp(pem=leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path = self._pem_bytes_to_private_tmp(
            pem=leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        try:
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            for path in (cert_path, key_path):
                with contextlib.suppress(FileNotFoundError):
                    Path(path).unlink()
        ctx.set_alpn_protocols(["http/1.1"])
        self._leaf_cache[hostname] = ctx
        logger.info("minted leaf cert for %s", hostname)
        return ctx

    def _pem_bytes_to_private_tmp(self, pem: bytes) -> str:
        """Write PEM to a broker-private tmpfile and return its path."""
        target = self._private_dir / f".leaf-{random.randbytes(8).hex()}.pem"
        target.write_bytes(pem)
        os.chmod(target, 0o600)
        return str(target)


async def _handle_proxy_conn(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    minter: _CertMinter,
    token_store: _TokenStore,
) -> None:
    """Accept a CONNECT, then either intercept known hosts or tunnel."""
    peer = writer.get_extra_info("peername")
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        try:
            method, target, _ = request_line.decode("iso-8859-1").strip().split(" ", 2)
        except ValueError:
            await _send_raw(writer=writer, status=400, body=b"bad request line")
            return
        while True:
            header_line = await reader.readline()
            if header_line in (b"\r\n", b"\n", b""):
                break
        if method.upper() != "CONNECT":
            await _send_raw(writer=writer, status=405, body=b"only CONNECT is supported")
            return
        raw_host, _, port_str = target.partition(":")
        host = tls_providers.normalize_connect_host(host=raw_host)
        port = int(port_str) if port_str else 443
        provider = token_store.provider_for_host(host=host)
        if provider is None:
            await _tunnel_opaque(client_reader=reader, client_writer=writer, host=host, port=port)
            return
        await _intercept_and_forward(
            client_reader=reader,
            client_writer=writer,
            host=host,
            port=port,
            provider=provider,
            minter=minter,
            token_store=token_store,
        )
    except (ConnectionResetError, BrokenPipeError):
        return
    except Exception:
        logger.exception("proxy connection failed (peer=%s)", peer)
    finally:
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()


async def _tunnel_opaque(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter, host: str, port: int) -> None:
    """Straight CONNECT tunnel for hosts we do not intercept."""
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(host=host, port=port)
    except OSError as exc:
        await _send_raw(writer=client_writer, status=502, body=f"upstream connect failed: {exc}".encode())
        return
    client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_writer.drain()
    await _pump_both_ways(a_reader=client_reader, a_writer=client_writer, b_reader=upstream_reader, b_writer=upstream_writer)


async def _intercept_and_forward(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    host: str,
    port: int,
    provider: tls_providers.TlsProviderSpec,
    minter: _CertMinter,
    token_store: _TokenStore,
) -> None:
    """TLS-terminate with a minted leaf, swap Authorization, and forward."""
    client_writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await client_writer.drain()
    ssl_ctx = minter.context_for(hostname=host)
    loop = asyncio.get_running_loop()
    transport = client_writer.transport
    protocol = transport.get_protocol()
    try:
        new_transport = await loop.start_tls(
            transport=transport,
            protocol=protocol,
            sslcontext=ssl_ctx,
            server_side=True,
        )
    except ssl.SSLError as exc:
        logger.error("TLS handshake with sandbox client failed for %s: %s", host, exc)
        return
    tls_reader = client_reader
    tls_writer = asyncio.StreamWriter(transport=new_transport, protocol=protocol, reader=tls_reader, loop=loop)
    try:
        while True:
            request_line = await tls_reader.readline()
            if not request_line:
                return
            headers_raw: list[bytes] = []
            while True:
                line = await tls_reader.readline()
                headers_raw.append(line)
                if line in (b"\r\n", b"\n", b""):
                    break
            headers = _parse_headers(lines=headers_raw)
            path_with_query = request_line.decode("iso-8859-1").split(" ", 2)[1]
            body = await _read_body(reader=tls_reader, headers=headers)
            try:
                addresses_doh_credential = _request_addresses_doh_credential(
                    headers=headers,
                    path_with_query=path_with_query,
                    provider=provider,
                )
            except _SecretSelectionError as exc:
                logger.error("%s secret selection failed: %s", provider.slug, exc)
                await _send_json_error(
                    writer=tls_writer,
                    status=400,
                    message=f"{provider.slug}: {exc}",
                )
                return
            if addresses_doh_credential:
                secrets = await token_store.secrets_for_host(host=host)
                if secrets is None:
                    await _send_provider_not_connected(writer=tls_writer, provider=provider)
                    return
                try:
                    forward_headers, forward_path = _rewrite_request_for_provider(
                        headers=headers,
                        path_with_query=path_with_query,
                        secrets=secrets,
                        provider=provider,
                        upstream_host=host,
                    )
                except _SecretSelectionError as exc:
                    logger.error("%s secret selection failed: %s", provider.slug, exc)
                    await _send_json_error(
                        writer=tls_writer,
                        status=400,
                        message=f"{provider.slug}: {exc}",
                    )
                    return
            else:
                forward_headers = _strip_proxy_headers_and_set_host(headers=headers, upstream_host=host)
                forward_path = path_with_query
            try:
                upstream_status, keep_alive = await _forward_to_upstream(
                    host=host,
                    port=port,
                    method=request_line.decode("iso-8859-1").split(" ", 1)[0],
                    path_with_query=forward_path,
                    headers=forward_headers,
                    body=body,
                    client_writer=tls_writer,
                )
            except Exception as exc:
                logger.exception("forward to %s failed", host)
                await _send_json_error(writer=tls_writer, status=502, message=f"broker upstream error: {exc}")
                return
            # Treat upstream 401 as "the cached token is no longer valid":
            # evict it so the next request refetches from DOH. Covers both
            # transient-after-rotation and user-revoked-on-provider-side.
            # We don't retry within this connection — the user's next
            # request through the proxy hits the refreshed token. Anonymous
            # pass-through 401s must not evict: they never used our token,
            # so the cached entry is not implicated.
            if upstream_status == 401 and addresses_doh_credential:
                await token_store.invalidate(slug=provider.slug)
                logger.info("evicted %s token cache after upstream 401 from %s", provider.slug, host)
            if not keep_alive:
                return
    finally:
        with contextlib.suppress(Exception):
            tls_writer.close()
            await tls_writer.wait_closed()


async def _send_provider_not_connected(writer: asyncio.StreamWriter, provider: tls_providers.TlsProviderSpec) -> None:
    """Return a Google-API-shaped not-connected error to the sandbox client."""
    await _send_json_error(
        writer=writer,
        status=503,
        message=f"{provider.slug} integration not connected in DOH — connect it from the Integrations pane.",
    )


async def _send_json_error(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    """Write a small JSON error response and drain it."""
    body = json.dumps({"error": {"code": status, "message": message}}).encode()
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    await writer.drain()


async def _pump_both_ways(
    a_reader: asyncio.StreamReader,
    a_writer: asyncio.StreamWriter,
    b_reader: asyncio.StreamReader,
    b_writer: asyncio.StreamWriter,
) -> None:
    async def _copy(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                chunk = await src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
                await dst.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                dst.close()
    await asyncio.gather(_copy(src=a_reader, dst=b_writer), _copy(src=b_reader, dst=a_writer))


def _parse_headers(lines: list[bytes]) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = []
    for line in lines:
        if line in (b"\r\n", b"\n", b""):
            break
        if b":" not in line:
            continue
        name, _, value = line.partition(b":")
        headers.append((name.strip().lower(), value.strip().rstrip(b"\r\n")))
    return headers


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    normalized_name = name.lower()
    for n, v in headers:
        if n.lower() == normalized_name:
            return v.lower()
    return None


async def _read_body(reader: asyncio.StreamReader, headers: list[tuple[bytes, bytes]]) -> bytes:
    """Read a request body per Content-Length / Transfer-Encoding."""
    te = _header_value(headers=headers, name=b"transfer-encoding")
    if te == b"chunked":
        return await _read_chunked(reader=reader)
    cl = _header_value(headers=headers, name=b"content-length")
    if cl is None:
        return b""
    remaining = int(cl)
    if remaining == 0:
        return b""
    return await reader.readexactly(remaining)


async def _read_chunked(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    while True:
        size_line = await reader.readline()
        size = int(size_line.strip().split(b";")[0], 16)
        if size == 0:
            while True:
                trailer_line = await reader.readline()
                if trailer_line in (b"\r\n", b"\n", b""):
                    break
            return b"".join(chunks)
        chunks.append(await reader.readexactly(size))
        await reader.readline()


def _build_authorization_value(token: str, auth_format: str) -> bytes:
    """Encode the upstream Authorization header for a given provider's auth format."""
    if auth_format == tls_providers.AUTH_FORMAT_BEARER:
        return b"Bearer " + token.encode()
    if auth_format == tls_providers.AUTH_FORMAT_BASIC_X_ACCESS_TOKEN:
        creds = b"x-access-token:" + token.encode()
        return b"Basic " + base64.b64encode(creds)
    raise ValueError(f"unknown auth_format: {auth_format!r}")


class _SecretSelectionError(Exception):
    """A request didn't carry a recognizable placeholder, or a required secret is missing from the cache.

    Raised by `_rewrite_request_for_provider`; the proxy loop maps it to a 400
    so an un-rewritten credential is never forwarded upstream.
    """


def _strip_bearer_prefix(value: bytes) -> str:
    """Return the token from a `Bearer <token>` header value (case-insensitive prefix)."""
    text = value.decode("iso-8859-1").strip()
    if text[:7].lower() == "bearer ":
        return text[7:].strip()
    return text


def _request_addresses_doh_credential(headers: list[tuple[bytes, bytes]], path_with_query: str, provider: tls_providers.TlsProviderSpec) -> bool:
    """Decide whether a request asks for DOH's credential or is anonymous public traffic.

    True routes through the token store + rewrite path. OAuth-style methods
    (OAuthHeader, OAuthHeaderMultiInject) are always True: their convention is
    inverted — the sandbox sends no marker and the proxy injects
    unconditionally, so every request implicitly asks for DOH's credential.

    Vault-style methods mark DOH's slot with an explicit placeholder. False
    means every credential slot is empty — anonymous public traffic (e.g.
    OpenRouter's unauthenticated /api/v1/models) the proxy forwards as-is,
    without consulting the token store, so a disconnected provider does not
    cost one DOH refresh per request. A credential that is neither empty nor
    a recognized placeholder raises `_SecretSelectionError` (→ 400): BYO keys
    are neither injected-over nor silently forwarded.
    """
    method = provider.credential_method
    if isinstance(method, (tls_providers.OAuthHeader, tls_providers.OAuthHeaderMultiInject)):
        return True
    if isinstance(method, tls_providers.VaultUrlRewrite):
        # Every Telegram Bot API path embeds a token, so this host has no
        # anonymous surface: a path without the placeholder carries an
        # un-rewritable credential, never public traffic.
        if method.placeholder not in path_with_query:
            raise _SecretSelectionError("request URL must contain the DOH placeholder")
        return True
    if isinstance(method, tls_providers.VaultHeaderInject):
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        if incoming is None:
            return False
        if method.secret_for_placeholder(_strip_bearer_prefix(incoming)) is None:
            raise _SecretSelectionError("request Authorization did not carry a known DOH placeholder")
        return True
    if isinstance(method, tls_providers.VaultApiKeyHeader):
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        if incoming is not None:
            if incoming.decode("iso-8859-1").strip() != method.placeholder:
                raise _SecretSelectionError(f"request {method.header_name} did not carry the DOH placeholder")
            return True
        # No api-key slot, but an Authorization header (e.g. a BYO OAuth
        # bearer) still counts as credentialed — refuse rather than forward.
        if any(n.lower() == b"authorization" for n, _v in headers):
            raise _SecretSelectionError(f"request carried Authorization instead of the {method.header_name} DOH placeholder")
        return False
    raise ValueError(f"unknown credential_method: {method!r}")


def _rewrite_authorization(headers: list[tuple[bytes, bytes]], token: str, auth_format: str, upstream_host: str) -> list[tuple[bytes, bytes]]:
    auth_value = _build_authorization_value(token=token, auth_format=auth_format)
    host_override = upstream_host.encode()
    rewritten: list[tuple[bytes, bytes]] = []
    seen_auth = False
    for name, value in headers:
        if name == b"authorization":
            rewritten.append((b"Authorization", auth_value))
            seen_auth = True
            continue
        if name == b"host":
            rewritten.append((b"Host", host_override))
            continue
        if name in (b"proxy-connection", b"proxy-authorization"):
            continue
        rewritten.append((name, value))
    if not seen_auth:
        rewritten.append((b"Authorization", auth_value))
    return rewritten


def _inject_headers(headers: list[tuple[bytes, bytes]], extra: dict[bytes, bytes]) -> list[tuple[bytes, bytes]]:
    """Force `extra` header values, replacing any the client sent (case-insensitive).

    Used after `_rewrite_authorization` to add broker-owned headers (e.g.
    `ChatGPT-Account-ID`) whose values come from DOH, not the sandbox. A header
    the sandbox sent under the same name is dropped so the sandbox can't spoof
    it; every other client header (Codex's Cloudflare `originator`/`User-Agent`)
    is left as-is.
    """
    lowered = {name.lower() for name in extra}
    kept = [(name, value) for name, value in headers if name.lower() not in lowered]
    return kept + list(extra.items())


def _strip_proxy_headers_and_set_host(headers: list[tuple[bytes, bytes]], upstream_host: str) -> list[tuple[bytes, bytes]]:
    """Remove proxy-only headers and force Host to the upstream hostname."""
    host_override = upstream_host.encode()
    rewritten: list[tuple[bytes, bytes]] = []
    seen_host = False
    for name, value in headers:
        if name == b"host":
            rewritten.append((b"Host", host_override))
            seen_host = True
            continue
        if name in (b"proxy-connection", b"proxy-authorization", b"authorization"):
            continue
        rewritten.append((name, value))
    if not seen_host:
        rewritten.append((b"Host", host_override))
    return rewritten


def _normalize_forward_headers(headers: list[tuple[bytes, bytes]], body_length: int) -> list[tuple[bytes, bytes]]:
    """Strip stale client-side framing before replaying the request upstream."""
    headers_to_strip = frozenset({
        b"connection",
        b"content-length",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
    })
    normalized: list[tuple[bytes, bytes]] = []
    body_was_framed = False
    for name, value in headers:
        lowered_name = name.lower()
        if lowered_name in (b"content-length", b"transfer-encoding"):
            body_was_framed = True
        if lowered_name in headers_to_strip:
            continue
        normalized.append((name, value))
    if body_length > 0 or body_was_framed:
        normalized.append((b"Content-Length", str(body_length).encode()))
    return normalized


def _rewrite_request_for_provider(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    secrets: dict[str, str],
    provider: tls_providers.TlsProviderSpec,
    upstream_host: str,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Rewrite credentials for the provider-specific upstream API shape.

    Single-secret methods (OAuthHeader, VaultUrlRewrite) use the sole secret.
    OAuthHeaderMultiInject swaps the bearer and injects its extra header(s)
    from named secrets, leaving other client headers intact. VaultHeaderInject
    selects per request by reverse-mapping the incoming placeholder bearer to
    its secret name. Raises `_SecretSelectionError` when a required secret is
    missing or the request doesn't carry a recognizable placeholder.
    """
    method = provider.credential_method
    if isinstance(method, tls_providers.OAuthHeader):
        return (
            _rewrite_authorization(
                headers=headers,
                token=_primary_secret(secrets),
                auth_format=method.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if isinstance(method, tls_providers.OAuthHeaderMultiInject):
        bearer = secrets.get(method.bearer_secret)
        if not bearer:
            raise _SecretSelectionError(f"no cached secret for {method.bearer_secret!r}")
        rewritten = _rewrite_authorization(
            headers=headers,
            token=bearer,
            auth_format=method.auth_format,
            upstream_host=upstream_host,
        )
        extra: dict[bytes, bytes] = {}
        for secret_name, header_name in method.header_secrets.items():
            value = secrets.get(secret_name)
            if not value:
                raise _SecretSelectionError(f"no cached secret for {secret_name!r}")
            extra[header_name.encode()] = value.encode()
        return (_inject_headers(headers=rewritten, extra=extra), path_with_query)
    if isinstance(method, tls_providers.VaultUrlRewrite):
        token = _primary_secret(secrets)
        if method.placeholder not in path_with_query:
            raise _SecretSelectionError("request URL must contain the DOH placeholder")
        return (
            _strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host),
            path_with_query.replace(method.placeholder, token),
        )
    if isinstance(method, tls_providers.VaultHeaderInject):
        # Read the raw (case-preserving) Authorization value — _header_value
        # lowercases, which would mangle a mixed-case placeholder token.
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        bearer = _strip_bearer_prefix(incoming) if incoming is not None else None
        secret_name = method.secret_for_placeholder(bearer) if bearer is not None else None
        if secret_name is None:
            raise _SecretSelectionError("request Authorization did not carry a known DOH placeholder")
        token = secrets.get(secret_name)
        if not token:
            raise _SecretSelectionError(f"no cached secret for {secret_name!r}")
        return (
            _rewrite_authorization(
                headers=headers,
                token=token,
                auth_format=method.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if isinstance(method, tls_providers.VaultApiKeyHeader):
        # Confirm the request carries our placeholder in the named auth header
        # (case-preserving read), then swap in the real key. Other headers — incl.
        # Anthropic's required anthropic-version — pass through untouched.
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        incoming_value = incoming.decode("iso-8859-1").strip() if incoming is not None else None
        if incoming_value != method.placeholder:
            raise _SecretSelectionError(f"request {method.header_name} did not carry the DOH placeholder")
        token = _primary_secret(secrets)
        stripped = _strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host)
        return (
            _inject_headers(headers=stripped, extra={method.header_name.encode(): token.encode()}),
            path_with_query,
        )
    raise ValueError(f"unknown credential_method: {method!r}")


async def _forward_to_upstream(
    host: str,
    port: int,
    method: str,
    path_with_query: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    client_writer: asyncio.StreamWriter,
) -> tuple[int, bool]:
    """Replay the request to the real upstream and relay the response to the client.

    Server-sent-event responses are relayed frame-by-frame so tokens reach the
    sandbox client live (the agent's per-delta stream callbacks then fire as
    they arrive instead of all at once); every other response is buffered and
    re-rendered with a computed Content-Length, preserving the per-object X cost
    audit. Returns ``(status, keep_alive)``; ``keep_alive`` is False when the
    client connection must be torn down after this exchange.
    """
    ctx = ssl.create_default_context()
    upstream_reader, upstream_writer = await asyncio.open_connection(host=host, port=port, ssl=ctx, server_hostname=host)
    try:
        normalized_headers = _normalize_forward_headers(headers=headers, body_length=len(body))
        request = method.encode() + b" " + path_with_query.encode() + b" HTTP/1.1\r\n"
        for name, value in normalized_headers:
            request += name + b": " + value + b"\r\n"
        request += b"\r\n"
        upstream_writer.write(request)
        if body:
            upstream_writer.write(body)
        await upstream_writer.drain()
        status_line = await upstream_reader.readline()
        try:
            status = int(status_line.split(b" ", 2)[1])
        except (IndexError, ValueError):
            raise RuntimeError(f"bad upstream status line: {status_line!r}")
        response_headers: list[tuple[bytes, bytes]] = []
        while True:
            line = await upstream_reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if b":" not in line:
                continue
            name, _, value = line.partition(b":")
            response_headers.append((name.strip(), value.strip().rstrip(b"\r\n")))
        connection_keep_alive = _header_value(headers=response_headers, name=b"connection") != b"close"

        # Statuses that cannot carry a body (HEAD, 1xx, 204, 304) fall through
        # to the buffered no-body path even when mislabeled text/event-stream —
        # the streaming relay would otherwise wait on EOF as the body delimiter
        # and hang a perfectly valid response.
        _can_have_body = not (method.upper() == "HEAD" or 100 <= status < 200 or status in (204, 304))
        if _can_have_body and _response_is_event_stream(headers=response_headers):
            framed = await _relay_streaming_response(
                upstream_reader=upstream_reader,
                client_writer=client_writer,
                status=status,
                headers=response_headers,
            )
            return status, connection_keep_alive and framed

        resp_body = await _read_response_body(reader=upstream_reader, headers=response_headers, status=status, method=method)
        # Cost audit for X: X meters per object returned (not per request), and
        # posts and users are *separately* billed meters (~$0.005 vs ~$0.01),
        # deduped per object per 24h. Count each by sniffing object shape (a user
        # object has `username`; a post has `text`/`edit_history_tweet_ids`)
        # across data[] + includes[] so each audit line maps onto the console's
        # two meters. Lists/media/DM-events fall into neither and aren't billed.
        if host == "api.x.com":
            posts_n = users_n = -1
            try:
                parsed = json.loads(resp_body)
                posts_n = users_n = 0
                for bucket in (parsed.get("data"), (parsed.get("includes") or {}).get("tweets"), (parsed.get("includes") or {}).get("users")):
                    items = bucket if isinstance(bucket, list) else ([bucket] if isinstance(bucket, dict) else [])
                    for item in items:
                        if "username" in item:
                            users_n += 1
                        elif "text" in item or "edit_history_tweet_ids" in item:
                            posts_n += 1
            except (ValueError, AttributeError, TypeError):
                pass
            logger.info(
                "X-COST-AUDIT method=%s path=%s status=%s posts=%s users=%s body_bytes=%s",
                method, path_with_query, status, posts_n, users_n, len(resp_body),
            )
        client_writer.write(_render_response(status=status, headers=response_headers, body=resp_body))
        await client_writer.drain()
        return status, connection_keep_alive
    finally:
        with contextlib.suppress(Exception):
            upstream_writer.close()
            await upstream_writer.wait_closed()


def _response_is_event_stream(headers: list[tuple[bytes, bytes]]) -> bool:
    """True when the upstream response is a server-sent-event stream."""
    content_type = _header_value(headers=headers, name=b"content-type") or b""
    return b"text/event-stream" in content_type


async def _relay_streaming_response(
    upstream_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    status: int,
    headers: list[tuple[bytes, bytes]],
) -> bool:
    """Relay an SSE response head + body to the client, flushing per frame.

    Headers are forwarded verbatim (unlike the buffered path, the upstream
    framing — ``Transfer-Encoding``/``Content-Length`` — is preserved so the
    client can delimit the body). Returns whether the client connection may be
    reused: False whenever the body didn't terminate cleanly (connection-close
    framing, a short ``Content-Length``, or a chunked body without its
    terminating 0-chunk), since the client's parser can't find a clean boundary
    and would misframe or hang on the next response sent over the same socket.
    """
    head = b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
    for name, value in headers:
        head += name + b": " + value + b"\r\n"
    head += b"\r\n"
    client_writer.write(head)
    await client_writer.drain()

    transfer_encoding = None
    content_length = None
    for name, value in headers:
        lowered = name.lower()
        if lowered == b"transfer-encoding":
            transfer_encoding = value.lower()
        elif lowered == b"content-length":
            content_length = value
    if transfer_encoding == b"chunked":
        return await _relay_chunked_stream(upstream_reader=upstream_reader, client_writer=client_writer)
    if content_length is not None:
        remaining = int(content_length)
        while remaining > 0:
            chunk = await upstream_reader.read(min(65536, remaining))
            if not chunk:
                # Upstream EOF before the advertised length — the client is
                # still waiting on the unfulfilled Content-Length, so the
                # socket can't carry another response.
                return False
            remaining -= len(chunk)
            client_writer.write(chunk)
            await client_writer.drain()
        return True
    # No explicit framing: relay until upstream EOF — the client learns the
    # body ended only when we close the connection, so it can't be reused.
    while True:
        chunk = await upstream_reader.read(65536)
        if not chunk:
            break
        client_writer.write(chunk)
        await client_writer.drain()
    return False


async def _relay_chunked_stream(upstream_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> bool:
    """Relay a chunked upstream body to the client one chunk at a time, flushing each.

    The chunk framing is forwarded verbatim (size line, payload, trailing CRLF,
    final 0-chunk + trailers) so the client's chunked decoder sees each SSE
    frame the instant it arrives. Returns True only when the stream closed
    cleanly with its terminating 0-chunk; a premature EOF, malformed size line,
    or truncated payload returns False so the caller tears the client
    connection down rather than reuse a socket the client can't reframe.
    """
    while True:
        size_line = await upstream_reader.readline()
        if not size_line:
            return False
        try:
            size = int(size_line.strip().split(b";")[0], 16)
        except ValueError:
            return False
        client_writer.write(size_line)
        if size == 0:
            # Forward the trailer section up to its terminating blank line. A
            # bare EOF (b"") before that blank line means the chunked
            # terminator (0-chunk + trailers + CRLF) never completed, so the
            # client can't reframe — relay the partial bytes but report
            # non-reuse.
            while True:
                trailer_line = await upstream_reader.readline()
                if trailer_line == b"":
                    await client_writer.drain()
                    return False
                client_writer.write(trailer_line)
                if trailer_line in (b"\r\n", b"\n"):
                    break
            await client_writer.drain()
            return True
        try:
            chunk = await upstream_reader.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            # Forward whatever bytes did arrive so an in-flight frame isn't
            # silently dropped, but the chunk is short of its declared size —
            # the client's decoder can't trust the framing from here on.
            client_writer.write(exc.partial)
            await client_writer.drain()
            return False
        crlf = await upstream_reader.readline()
        client_writer.write(chunk + crlf)
        await client_writer.drain()


async def _read_response_body(reader: asyncio.StreamReader, headers: list[tuple[bytes, bytes]], status: int, method: str) -> bytes:
    if method.upper() == "HEAD" or 100 <= status < 200 or status in (204, 304):
        return b""
    te_value = None
    for n, v in headers:
        if n.lower() == b"transfer-encoding":
            te_value = v.lower()
            break
    if te_value == b"chunked":
        return await _read_chunked(reader=reader)
    cl_value = None
    for n, v in headers:
        if n.lower() == b"content-length":
            cl_value = v
            break
    if cl_value is not None:
        remaining = int(cl_value)
        if remaining == 0:
            return b""
        return await reader.readexactly(remaining)
    chunks: list[bytes] = []
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _render_response(status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> bytes:
    reason = _http_reason(status=status)
    lines = [b"HTTP/1.1 " + str(status).encode() + b" " + reason.encode() + b"\r\n"]
    skip = {b"transfer-encoding", b"connection", b"content-length"}
    for name, value in headers:
        if name.lower() in skip:
            continue
        lines.append(name + b": " + value + b"\r\n")
    lines.append(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    lines.append(b"Connection: close\r\n")
    lines.append(b"\r\n")
    return b"".join(lines) + body


def _http_reason(status: int) -> str:
    return {
        200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently",
        302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized",
        403: "Forbidden", 404: "Not Found", 409: "Conflict", 410: "Gone",
        429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status, "OK")


async def _send_raw(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()
