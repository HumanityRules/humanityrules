"""TLS-intercept proxy runtime for platform-managed provider tokens."""

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
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

# Browser-facing status strings rendered by the WebUI extension.
STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"

# Authorization header encodings used by OAuthHeader providers.
AUTH_FORMAT_BEARER = "bearer"
AUTH_FORMAT_BASIC_X_ACCESS_TOKEN = "basic_x_access_token"


@dataclass(frozen=True)
class OAuthHeader:
    """Browser-OAuth integration; token injected as Authorization header.

    `auth_format` selects the header encoding: Google takes plain Bearer;
    GitHub git-smart-HTTP needs HTTP Basic with the token as the password
    under the `x-access-token` username.
    """

    auth_format: str
    connect_mode: ClassVar[str] = "oauth"
    restart_required_after_save: ClassVar[bool] = False


@dataclass(frozen=True)
class GatewayEnvBinding:
    """One env var the in-sandbox gateway reads at startup.

    `source` is either the literal `"placeholder"` (use the URL-rewrite
    placeholder verbatim — the broker swaps the real token on the wire)
    or a key in the connected provider's `config` dict. `list_separator`
    joins list-shaped config (e.g. `allowed_users`) into a single string.
    """

    env_var: str
    source: str
    list_separator: str | None = None


@dataclass(frozen=True)
class VaultUrlRewrite:
    """Vault-pasted credential; injected by replacing a placeholder in the URL.

    The sandbox client uses `placeholder` in the URL where the real secret
    would go (e.g. Telegram's `/bot{token}/` path); the proxy substitutes
    the live token before forwarding.

    `gateway_env` declares the env vars the in-sandbox Hermes gateway
    needs at startup to activate the matching platform binding (presence
    of the var, not its value, is what gates activation — see
    `hermes_cli/tools_config.py:_get_enabled_platforms`).
    """

    placeholder: str
    gateway_env: tuple[GatewayEnvBinding, ...] = ()
    connect_mode: ClassVar[str] = "vault"
    restart_required_after_save: ClassVar[bool] = True


CredentialMethod = OAuthHeader | VaultUrlRewrite


@dataclass(frozen=True)
class TlsProviderSpec:
    """Static config for one provider whose HTTPS traffic is intercepted."""

    slug: str
    label: str
    refresh_path: str
    hosts: tuple[str, ...]
    logo_url: str
    credential_method: CredentialMethod


@dataclass(frozen=True)
class DohRefreshConfig:
    """DOH identity and endpoint config used to refresh provider access tokens."""

    control_plane_url: str
    bearer: str
    owner_username: str
    app_slug: str


# DOH's per-provider token endpoint can return one of three logical outcomes:
# - "connected": a fresh access_token (with expiry/config/metadata).
# - "absent":    the user is not connected (404), or DOH just deleted the row
#                after the provider revoked the refresh_token (410). The two
#                are indistinguishable for the broker — both mean "no token
#                available, the user must (re)connect".
# - "transient": network error, 5xx, or any other failure we should not let
#                overwrite a working cache entry.
REFRESH_OUTCOME_CONNECTED = "connected"
REFRESH_OUTCOME_ABSENT = "absent"
REFRESH_OUTCOME_TRANSIENT = "transient"


@dataclass(frozen=True)
class RefreshResult:
    """Outcome from DOH's per-provider token endpoint."""

    outcome: str
    access_token: str | None
    expires_in: int | None
    config: dict
    metadata: dict


@dataclass
class _TokenCacheEntry:
    """One working access_token plus the metadata we hand to the gateway/UI.

    The cache contains entries ONLY for connected providers. A missing entry
    means "not connected".
    """

    access_token: str
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


TLS_INTERCEPT_PROVIDER_SPECS = (
    TlsProviderSpec(
        slug="google",
        label="Google Workspace",
        refresh_path="/api/integrations/google/token",
        hosts=(
            "gmail.googleapis.com",
            "calendar-json.googleapis.com",
            "drive.googleapis.com",
            "docs.googleapis.com",
            "sheets.googleapis.com",
            "people.googleapis.com",
            "www.googleapis.com",
            "oauth2.googleapis.com",
        ),
        logo_url="/extensions/google-workspace.svg",
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BEARER),
    ),
    TlsProviderSpec(
        slug="github",
        label="GitHub",
        refresh_path="/api/integrations/github/token",
        hosts=(
            # github.com handles git smart-HTTP (clone/push) and OAuth
            # endpoints; api.github.com handles REST (incl. `gh` CLI);
            # codeload.github.com serves archive/tarball downloads after a
            # github.com redirect.
            "github.com",
            "api.github.com",
            "codeload.github.com",
        ),
        logo_url="/extensions/github.svg",
        credential_method=OAuthHeader(auth_format=AUTH_FORMAT_BASIC_X_ACCESS_TOKEN),
    ),
    TlsProviderSpec(
        slug="telegram",
        label="Telegram",
        refresh_path="/api/integrations/telegram/token",
        hosts=("api.telegram.org",),
        logo_url="/extensions/telegram.svg",
        credential_method=VaultUrlRewrite(
            placeholder="000000:DOH_PLACEHOLDER",
            gateway_env=(
                GatewayEnvBinding(env_var="TELEGRAM_BOT_TOKEN", source="placeholder"),
                GatewayEnvBinding(env_var="TELEGRAM_ALLOWED_USERS", source="allowed_users", list_separator=","),
            ),
        ),
    ),
)


def build_provider_registry(provider_specs: tuple[TlsProviderSpec, ...]) -> dict[str, TlsProviderSpec]:
    """Index provider specs by slug and fail fast on duplicate slugs."""
    providers: dict[str, TlsProviderSpec] = {}
    for spec in provider_specs:
        if spec.slug in providers:
            raise RuntimeError(f"duplicate TLS-intercept provider slug: {spec.slug}")
        providers[spec.slug] = spec
    return providers


def build_host_to_provider(providers: dict[str, TlsProviderSpec]) -> dict[str, str]:
    """Map intercepted upstream hosts to provider slugs and fail on overlap."""
    host_to_provider: dict[str, str] = {}
    for slug, spec in providers.items():
        for host in spec.hosts:
            existing_slug = host_to_provider.get(host)
            if existing_slug is not None:
                raise RuntimeError(
                    f"TLS-intercept host {host!r} is claimed by both {existing_slug!r} and {slug!r}"
                )
            host_to_provider[host] = slug
    return host_to_provider


TLS_INTERCEPT_PROVIDERS = build_provider_registry(provider_specs=TLS_INTERCEPT_PROVIDER_SPECS)
HOST_TO_TLS_PROVIDER = build_host_to_provider(providers=TLS_INTERCEPT_PROVIDERS)


def fetch_provider_token(refresh_config: DohRefreshConfig, provider: TlsProviderSpec) -> RefreshResult:
    """Call DOH's per-provider refresh endpoint and classify the response.

    404 (no IntegrationUserCredential row) and 410 (refresh_token revoked
    upstream; DOH just deleted the row) both collapse to `absent` — once the
    row is gone, the only user action either signal warrants is "connect".
    """
    url = f"{refresh_config.control_plane_url.rstrip('/')}{provider.refresh_path}"
    body = json.dumps({
        "owner_username": refresh_config.owner_username,
        "app_slug": refresh_config.app_slug,
    }).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {refresh_config.bearer}", "Content-Type": "application/json"},
    )
    try:
        # In-VPC JSON POST to our own control plane; healthy P99 is tens of
        # ms. 5s is a generous ceiling that still keeps the proxy hot path,
        # the post-vault-save modal, and broker bootstrap (under
        # supervisor's 10s wait_for_port) all well inside their budgets.
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
    except Exception as exc:
        logger.error("refresh network error for %s: %s", provider.refresh_path, exc)
        return RefreshResult(outcome=REFRESH_OUTCOME_TRANSIENT, access_token=None, expires_in=None, config={}, metadata={})

    if status == 200:
        return RefreshResult(
            outcome=REFRESH_OUTCOME_CONNECTED,
            access_token=payload["access_token"],
            expires_in=int(payload.get("expires_in", 0)),
            config=payload.get("config", {}),
            metadata=payload.get("metadata", {}),
        )
    if status in (404, 410):
        return RefreshResult(outcome=REFRESH_OUTCOME_ABSENT, access_token=None, expires_in=None, config={}, metadata={})
    logger.error("refresh got http %d for %s: %s", status, provider.refresh_path, payload.get("error", ""))
    return RefreshResult(outcome=REFRESH_OUTCOME_TRANSIENT, access_token=None, expires_in=None, config={}, metadata={})


GATEWAY_ENV_BLOCK_BEGIN = "# === DOH-MANAGED-INTEGRATIONS BEGIN ==="
GATEWAY_ENV_BLOCK_END = "# === DOH-MANAGED-INTEGRATIONS END ==="


def _render_gateway_env_lines(provider: TlsProviderSpec, method: VaultUrlRewrite, config: dict) -> list[str]:
    """Project one connected vault provider's gateway_env bindings into KEY=VALUE lines."""
    lines: list[str] = []
    for binding in method.gateway_env:
        if binding.source == "placeholder":
            value = method.placeholder
        else:
            raw = config.get(binding.source)
            if raw is None:
                continue
            if isinstance(raw, list):
                if binding.list_separator is None:
                    raise ValueError(
                        f"{provider.slug}: list-shaped config {binding.source!r} requires list_separator"
                    )
                value = binding.list_separator.join(str(item) for item in raw if str(item))
                if not value:
                    continue
            else:
                value = str(raw)
        lines.append(f"{binding.env_var}={value}")
    return lines


def render_managed_block(snapshot: list[tuple[TlsProviderSpec, dict]]) -> str:
    """Render the gateway env managed block from a token-store snapshot.

    The snapshot lists only connected providers (cache presence == connected,
    by the token-store contract). Each tuple is `(provider, config)`. Only
    `VaultUrlRewrite` credential methods carry `gateway_env` bindings today,
    so they're the only ones that produce lines here; if an OAuth provider
    ever needs to surface env vars to the gateway, we'll need a different
    way to signal "this provider has gateway env to render".
    """
    body_lines: list[str] = []
    for provider, config in snapshot:
        method = provider.credential_method
        if not isinstance(method, VaultUrlRewrite):
            continue
        body_lines.extend(_render_gateway_env_lines(provider=provider, method=method, config=config))
    if not body_lines:
        return ""
    return "\n".join([GATEWAY_ENV_BLOCK_BEGIN, *body_lines, GATEWAY_ENV_BLOCK_END]) + "\n"


def write_gateway_env_file(env_path: Path, managed_block: str) -> None:
    """Replace the DOH-managed block in `env_path` atomically.

    Lines outside the sentinels (user/onboarding-set keys) are preserved.
    Empty managed block (no vault providers connected) strips the sentinels
    entirely.
    """
    existing_lines: list[str] = []
    if env_path.exists():
        existing_lines = env_path.read_text(encoding="utf-8").splitlines()
    preserved: list[str] = []
    in_block = False
    for line in existing_lines:
        stripped = line.strip()
        if stripped == GATEWAY_ENV_BLOCK_BEGIN:
            in_block = True
            continue
        if stripped == GATEWAY_ENV_BLOCK_END:
            in_block = False
            continue
        if not in_block:
            preserved.append(line)
    while preserved and preserved[-1] == "":
        preserved.pop()
    parts: list[str] = []
    if preserved:
        parts.append("\n".join(preserved) + "\n")
    if managed_block:
        if parts:
            parts.append("\n")
        parts.append(managed_block)
    new_contents = "".join(parts)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp_path.write_text(new_contents, encoding="utf-8")
    os.replace(tmp_path, env_path)


def _cache_entry_from_connected_result(result: RefreshResult, now: float) -> _TokenCacheEntry:
    """Build a cache entry from a connected refresh result.

    Caller is responsible for only invoking this on `REFRESH_OUTCOME_CONNECTED`
    results — absent/transient outcomes don't have a token to cache.
    """
    if result.expires_in is None:
        raise ValueError("connected refresh result must carry expires_in")
    return _TokenCacheEntry(
        access_token=result.access_token,
        expires_at=now + result.expires_in,
        last_refreshed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        config=result.config,
        metadata=result.metadata,
    )


def _status_item_for_provider(provider: TlsProviderSpec, entry: _TokenCacheEntry | None) -> dict:
    """Serialize one TLS-intercept provider for the unified integrations payload.

    Connected = we have a cache entry; not_connected = we don't. There's no
    third state on this path: transient errors during refresh leave the cache
    untouched, so the previous entry (if any) keeps representing the truth.
    """
    method = provider.credential_method
    is_connected = entry is not None
    return {
        "kind": "tls_intercept",
        "slug": provider.slug,
        "label": provider.label,
        "logo_url": provider.logo_url,
        "status": STATUS_CONNECTED if is_connected else STATUS_NOT_CONNECTED,
        "last_refreshed_at": entry.last_refreshed_at if is_connected else None,
        "config": entry.config if is_connected else {},
        "metadata": entry.metadata if is_connected else {},
        "connect_mode": method.connect_mode,
        "restart_required_after_save": method.restart_required_after_save,
    }


class _TokenStore:
    """Token cache and refresh coordinator for TLS-intercept providers."""

    def __init__(self, providers: dict[str, TlsProviderSpec], refresh_config: DohRefreshConfig, refresh_lead_seconds: int) -> None:
        self._providers = providers
        self._host_to_provider = build_host_to_provider(providers=providers)
        self._refresh_config = refresh_config
        self._refresh_lead_seconds = refresh_lead_seconds
        self._refresh_locks = {slug: asyncio.Lock() for slug in providers}
        self._cache: dict[str, _TokenCacheEntry] = {}
        self._cache_lock = asyncio.Lock()

    def provider_for_host(self, host: str) -> TlsProviderSpec | None:
        """Return the provider that owns an upstream hostname."""
        slug = self._host_to_provider.get(host)
        if slug is None:
            return None
        return self._providers[slug]

    async def token_for_host(self, host: str) -> str | None:
        """Return a fresh token for an upstream host, or None when disconnected."""
        provider = self.provider_for_host(host=host)
        if provider is None:
            return None
        entry = await self._ensure_fresh(provider=provider)
        if entry is None:
            return None
        return entry.access_token

    async def invalidate(self, slug: str) -> None:
        """Drop the cached token for a provider, racing-safely.

        Takes the per-provider refresh_lock around the cache pop. Without
        this, an in-flight `_ensure_fresh` for the same slug could land its
        (now stale) write into the cache *after* the pop, and a subsequent
        `_ensure_fresh` would find that fresh-looking stale entry and skip
        the refetch — silently swallowing the user's Disconnect/vault-save.
        Serializing through the refresh_lock makes the stale write land
        first, then the pop, then the next refresh starts from an empty
        cache and actually hits DOH.
        """
        await self._invalidate_one(slug=slug)

    async def invalidate_all(self) -> None:
        """Drop every cached entry. Used by the explicit Refresh-all path.

        Per-slug invalidation runs concurrently — each takes its own
        provider's refresh_lock, so they never block each other.
        """
        await asyncio.gather(*(self._invalidate_one(slug=slug) for slug in self._providers))

    async def _invalidate_one(self, slug: str) -> None:
        """Take the provider's refresh_lock, then pop its cache entry."""
        lock = self._refresh_locks.get(slug)
        if lock is None:
            return
        async with lock:
            async with self._cache_lock:
                self._cache.pop(slug, None)

    async def status_items(self) -> list[dict]:
        """Render integration cards from the current cache; never calls DOH.

        Cache writes happen on three paths: boot bootstrap, the proxy hot path
        (`token_for_host` near-expiry refresh), and explicit user invalidate.
        Status reads are a pure projection of whatever those paths produced.
        """
        async with self._cache_lock:
            snapshot = dict(self._cache)
        return [
            _status_item_for_provider(provider=provider, entry=snapshot.get(provider.slug))
            for provider in self._providers.values()
        ]

    async def _ensure_fresh(self, provider: TlsProviderSpec) -> _TokenCacheEntry | None:
        """Single-flight refresh when the cached token is missing or near expiry.

        Returns the cache entry to use for this request, or None when the
        provider is genuinely unavailable. The two cases to keep separate:

        - **Refresh succeeded** (connected/absent): the cache reflects DOH
          truth, so we return whatever's now in the cache.
        - **Refresh transient-failed**: the cache is untouched. If we had a
          prior entry that's still un-expired, hand it back — the proxy
          can use it for the rest of its expires_at window rather than
          surfacing "not connected" to the sandbox because DOH hiccuped.
          Only return None when even the prior token is past expiry.
        """
        async with self._refresh_locks[provider.slug]:
            async with self._cache_lock:
                entry = self._cache.get(provider.slug)
            if entry is not None and entry.is_fresh(now=time.monotonic(), refresh_lead_seconds=self._refresh_lead_seconds):
                return entry
            await self._refresh_provider(provider=provider)
            async with self._cache_lock:
                entry = self._cache.get(provider.slug)
            if entry is None:
                return None
            if not entry.is_usable(now=time.monotonic()):
                return None
            return entry

    async def _refresh_provider(self, provider: TlsProviderSpec) -> None:
        """Refresh one provider and apply the outcome to the cache.

        Side-effect only — callers re-read the cache to learn the result:

        - connected → write the new entry.
        - absent → drop any prior entry (idempotent).
        - transient → leave the cache untouched (don't replace a working
          token with a sentinel; the prior entry, if any, stays available).
        """
        result = await asyncio.to_thread(
            fetch_provider_token,
            refresh_config=self._refresh_config,
            provider=provider,
        )
        if result.outcome == REFRESH_OUTCOME_CONNECTED:
            entry = _cache_entry_from_connected_result(result=result, now=time.monotonic())
            async with self._cache_lock:
                self._cache[provider.slug] = entry
            logger.info("refreshed %s: connected", provider.slug)
            return
        if result.outcome == REFRESH_OUTCOME_ABSENT:
            async with self._cache_lock:
                self._cache.pop(provider.slug, None)
            logger.info("refreshed %s: not_connected", provider.slug)
            return
        logger.info("refreshed %s: transient error (cache untouched)", provider.slug)


class TlsInterceptRuntime:
    """TLS-intercept subsystem: proxy transport, token refresh, and status cards."""

    def __init__(
        self,
        providers: dict[str, TlsProviderSpec],
        refresh_config: DohRefreshConfig,
        refresh_lead_seconds: int,
        ca_dir: Path,
        private_dir: Path,
        on_user_invalidate=None,
    ) -> None:
        self._token_store = _TokenStore(
            providers=providers,
            refresh_config=refresh_config,
            refresh_lead_seconds=refresh_lead_seconds,
        )
        self._cert_minter = _CertMinter(ca_dir=ca_dir, private_dir=private_dir)
        self._cert_minter.bootstrap()
        # `on_user_invalidate(slug | None)` runs after a user-initiated
        # invalidate (vault save/disconnect, per-provider or all-providers
        # cache flush). The proxy hot-path 401 eviction calls the inner
        # token store directly and does NOT trigger this — that path is
        # not a credential change, just a cached-token rotation.
        self._on_user_invalidate = on_user_invalidate

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
        """Drop one provider's cached token entry and fire the user-invalidate hook."""
        await self._token_store.invalidate(slug=slug)
        if self._on_user_invalidate is not None:
            await self._on_user_invalidate(slug)

    async def invalidate_all(self) -> None:
        """Drop every cached token entry and fire the user-invalidate hook."""
        await self._token_store.invalidate_all()
        if self._on_user_invalidate is not None:
            await self._on_user_invalidate(None)

    async def refresh_slug(self, slug: str) -> None:
        """Force a single-provider refetch so the cache reflects current DOH state."""
        provider = self._token_store._providers.get(slug)
        if provider is None:
            return
        await self._token_store._ensure_fresh(provider=provider)

    async def refresh_all(self) -> None:
        """Force a refetch of every provider so the cache reflects current DOH state.

        Refreshes run concurrently — sequential refreshes worst-case at
        N × 30s (the urlopen timeout in fetch_provider_token), which would
        exceed supervisor's wait_for_port budget if the control plane is
        slow. With gather, the floor is one slow call regardless of N.
        """
        await asyncio.gather(
            *(
                self._token_store._ensure_fresh(provider=provider)
                for provider in self._token_store._providers.values()
            )
        )

    async def gateway_env_snapshot(self) -> list[tuple[TlsProviderSpec, dict]]:
        """Pair every connected provider with its cached config for env rendering.

        Returns `(provider, config)` tuples for providers currently in the
        cache (cache presence == connected). Disconnected providers are
        absent from the result. The broker calls `refresh_all()` first when
        it wants the cache aligned with DOH state.
        """
        async with self._token_store._cache_lock:
            snapshot = dict(self._token_store._cache)
        return [
            (self._token_store._providers[slug], entry.config)
            for slug, entry in snapshot.items()
        ]


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
        host, _, port_str = target.partition(":")
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
    provider: TlsProviderSpec,
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
            method = provider.credential_method
            if isinstance(method, VaultUrlRewrite) and method.placeholder not in path_with_query:
                logger.error(
                    "%s request path did not contain expected placeholder: %s",
                    provider.slug,
                    path_with_query.split("?", 1)[0],
                )
                await _send_json_error(
                    writer=tls_writer,
                    status=400,
                    message=f"{provider.slug} request URL must contain the DOH placeholder",
                )
                return
            body = await _read_body(reader=tls_reader, headers=headers)
            token = await token_store.token_for_host(host=host)
            if token is None:
                await _send_provider_not_connected(writer=tls_writer, provider=provider)
                return
            forward_headers, forward_path = _rewrite_request_for_provider(
                headers=headers,
                path_with_query=path_with_query,
                token=token,
                provider=provider,
                upstream_host=host,
            )
            try:
                upstream_status, upstream_headers, upstream_body = await _forward_to_upstream(
                    host=host,
                    port=port,
                    method=request_line.decode("iso-8859-1").split(" ", 1)[0],
                    path_with_query=forward_path,
                    headers=forward_headers,
                    body=body,
                )
            except Exception as exc:
                logger.exception("forward to %s failed", host)
                await _send_json_error(writer=tls_writer, status=502, message=f"broker upstream error: {exc}")
                return
            # Treat upstream 401 as "the cached token is no longer valid":
            # evict it so the next request refetches from DOH. Covers both
            # transient-after-rotation and user-revoked-on-provider-side.
            # We don't retry within this connection — the user's next
            # request through the proxy hits the refreshed token.
            if upstream_status == 401:
                await token_store.invalidate(slug=provider.slug)
                logger.info("evicted %s token cache after upstream 401 from %s", provider.slug, host)
            tls_writer.write(_render_response(status=upstream_status, headers=upstream_headers, body=upstream_body))
            await tls_writer.drain()
            if _header_value(headers=upstream_headers, name=b"connection") == b"close":
                return
    finally:
        with contextlib.suppress(Exception):
            tls_writer.close()
            await tls_writer.wait_closed()


async def _send_provider_not_connected(writer: asyncio.StreamWriter, provider: TlsProviderSpec) -> None:
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
    for n, v in headers:
        if n == name:
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
            await reader.readline()
            return b"".join(chunks)
        chunks.append(await reader.readexactly(size))
        await reader.readline()


def _build_authorization_value(token: str, auth_format: str) -> bytes:
    """Encode the upstream Authorization header for a given provider's auth format."""
    if auth_format == AUTH_FORMAT_BEARER:
        return b"Bearer " + token.encode()
    if auth_format == AUTH_FORMAT_BASIC_X_ACCESS_TOKEN:
        creds = b"x-access-token:" + token.encode()
        return b"Basic " + base64.b64encode(creds)
    raise ValueError(f"unknown auth_format: {auth_format!r}")


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


def _rewrite_request_for_provider(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    token: str,
    provider: TlsProviderSpec,
    upstream_host: str,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Rewrite credentials for the provider-specific upstream API shape."""
    method = provider.credential_method
    if isinstance(method, OAuthHeader):
        return (
            _rewrite_authorization(
                headers=headers,
                token=token,
                auth_format=method.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if isinstance(method, VaultUrlRewrite):
        if method.placeholder not in path_with_query:
            raise ValueError(f"{provider.slug} request URL must contain the DOH placeholder")
        return (
            _strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host),
            path_with_query.replace(method.placeholder, token),
        )
    raise ValueError(f"unknown credential_method: {method!r}")


async def _forward_to_upstream(
    host: str,
    port: int,
    method: str,
    path_with_query: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Open a fresh TLS client to real upstream and replay the request."""
    ctx = ssl.create_default_context()
    upstream_reader, upstream_writer = await asyncio.open_connection(host=host, port=port, ssl=ctx, server_hostname=host)
    try:
        request = method.encode() + b" " + path_with_query.encode() + b" HTTP/1.1\r\n"
        for name, value in headers:
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
        resp_body = await _read_response_body(reader=upstream_reader, headers=response_headers, status=status, method=method)
        return status, response_headers, resp_body
    finally:
        with contextlib.suppress(Exception):
            upstream_writer.close()
            await upstream_writer.wait_closed()


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
