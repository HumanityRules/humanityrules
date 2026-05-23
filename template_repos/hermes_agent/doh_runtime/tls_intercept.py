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

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa


logger = logging.getLogger("tls_intercept")


REFRESH_LEAD_SECONDS = 300

STATUS_CONNECTED = "connected"
STATUS_NOT_CONNECTED = "not_connected"
STATUS_REVOKED = "revoked"
STATUS_TRANSIENT_ERROR = "transient_error"

# How a provider expects credentials to be injected into the upstream request.
CREDENTIAL_LOCATION_AUTHORIZATION_HEADER = "authorization_header"
CREDENTIAL_LOCATION_TELEGRAM_PATH = "telegram_path"

# How a provider expects the rewritten Authorization header to look.
AUTH_FORMAT_BEARER = "bearer"
AUTH_FORMAT_BASIC_X_ACCESS_TOKEN = "basic_x_access_token"
AUTH_FORMAT_NONE = "none"

TELEGRAM_PLACEHOLDER_TOKEN = "000000:DOH_PLACEHOLDER"

_OUTCOMES = {
    200: STATUS_CONNECTED,
    404: STATUS_NOT_CONNECTED,
    410: STATUS_REVOKED,
    401: STATUS_TRANSIENT_ERROR,
    500: STATUS_TRANSIENT_ERROR,
}


@dataclass(frozen=True)
class TlsProviderSpec:
    """Static config for one provider whose HTTPS traffic is intercepted."""

    slug: str
    label: str
    refresh_path: str
    hosts: tuple[str, ...]
    logo_url: str
    credential_location: str
    # How the rewritten Authorization header should be encoded. Google takes
    # plain Bearer; GitHub git-smart-HTTP needs HTTP Basic with the token as
    # the password under the `x-access-token` username.
    auth_format: str


@dataclass(frozen=True)
class DohRefreshConfig:
    """DOH identity and endpoint config used to refresh provider access tokens."""

    control_plane_url: str
    bearer: str
    owner_username: str
    app_slug: str


@dataclass(frozen=True)
class RefreshResult:
    """Outcome from DOH's per-provider token endpoint."""

    status: str
    access_token: str | None
    expires_in: int | None
    config: dict
    metadata: dict


@dataclass
class _TokenCacheEntry:
    """Cached token state for one TLS-intercept provider."""

    status: str
    access_token: str | None
    expires_at: float | None
    last_refreshed_at: str | None
    config: dict
    metadata: dict

    def is_fresh(self, now: float, refresh_lead_seconds: int) -> bool:
        """Return true when the cached token is not close to expiry."""
        if self.status != STATUS_CONNECTED:
            return False
        if self.expires_at is None:
            return False
        return self.expires_at - now > refresh_lead_seconds


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
        credential_location=CREDENTIAL_LOCATION_AUTHORIZATION_HEADER,
        auth_format=AUTH_FORMAT_BEARER,
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
        credential_location=CREDENTIAL_LOCATION_AUTHORIZATION_HEADER,
        auth_format=AUTH_FORMAT_BASIC_X_ACCESS_TOKEN,
    ),
    TlsProviderSpec(
        slug="telegram",
        label="Telegram",
        refresh_path="/api/integrations/telegram/token",
        hosts=("api.telegram.org",),
        logo_url="/extensions/telegram.svg",
        credential_location=CREDENTIAL_LOCATION_TELEGRAM_PATH,
        auth_format=AUTH_FORMAT_NONE,
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
    """Call DOH's per-provider refresh endpoint and classify the response."""
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
        with urllib.request.urlopen(req, timeout=30) as response:
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
        return RefreshResult(status=STATUS_TRANSIENT_ERROR, access_token=None, expires_in=None, config={}, metadata={})

    if status == 200:
        return RefreshResult(
            status=STATUS_CONNECTED,
            access_token=payload["access_token"],
            expires_in=int(payload.get("expires_in", 0)),
            config=payload.get("config", {}),
            metadata=payload.get("metadata", {}),
        )
    outcome = _OUTCOMES.get(status, STATUS_TRANSIENT_ERROR)
    if outcome == STATUS_TRANSIENT_ERROR:
        logger.error("refresh got http %d for %s: %s", status, provider.refresh_path, payload.get("error", ""))
    return RefreshResult(status=outcome, access_token=None, expires_in=None, config={}, metadata={})


def _cache_entry_from_refresh_result(result: RefreshResult, now: float) -> _TokenCacheEntry:
    """Convert a refresh result into the internal cache shape."""
    is_connected = result.status == STATUS_CONNECTED
    return _TokenCacheEntry(
        status=result.status,
        access_token=result.access_token,
        expires_at=now + result.expires_in if is_connected and result.expires_in is not None else None,
        last_refreshed_at=dt.datetime.now(dt.timezone.utc).isoformat() if is_connected else None,
        config=result.config if is_connected else {},
        metadata=result.metadata if is_connected else {},
    )


def _status_item_for_provider(provider: TlsProviderSpec, entry: _TokenCacheEntry | None) -> dict:
    """Serialize one TLS-intercept provider for the unified integrations payload."""
    return {
        "kind": "tls_intercept",
        "slug": provider.slug,
        "label": provider.label,
        "logo_url": provider.logo_url,
        "status": entry.status if entry is not None else STATUS_TRANSIENT_ERROR,
        "last_refreshed_at": entry.last_refreshed_at if entry is not None else None,
        "config": entry.config if entry is not None else {},
        "metadata": entry.metadata if entry is not None else {},
        "connect_mode": "vault" if provider.credential_location == CREDENTIAL_LOCATION_TELEGRAM_PATH else "oauth",
        "restart_required_after_save": provider.credential_location == CREDENTIAL_LOCATION_TELEGRAM_PATH,
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
        return entry.access_token

    async def invalidate(self, slug: str) -> None:
        """Drop the cached token for a provider (e.g. after upstream 401).

        Forces the next `token_for_host` call to refetch from DOH. If DOH
        has since deleted the grant (user revoked), the next refresh
        returns 404 → status flips to not_connected → integrations pane
        updates without ceremony.
        """
        async with self._cache_lock:
            self._cache.pop(slug, None)

    async def invalidate_all(self) -> None:
        """Drop every cached entry. Used by explicit "I just disconnected" signals.

        The next status read will lazily refetch. Without this, the cache
        could keep reporting "connected" for up to one full token lifetime
        after the user disconnects on DOH's side from the same session.
        """
        async with self._cache_lock:
            self._cache.clear()

    async def status_items(self) -> list[dict]:
        """Return TLS-intercept integration cards for the unified status payload.

        Uses `_ensure_fresh` per provider (single-flight, refetches only when
        the cached entry is missing or near expiry). Avoids the previous
        "force-refresh on every page open" pattern, which on GitHub would
        rotate the refresh_token and invalidate the in-flight access token —
        racing any concurrent git/gh request through the proxy.
        """
        for provider in self._providers.values():
            await self._ensure_fresh(provider=provider)
        async with self._cache_lock:
            snapshot = dict(self._cache)
        return [
            _status_item_for_provider(provider=provider, entry=snapshot.get(provider.slug))
            for provider in self._providers.values()
        ]

    async def _ensure_fresh(self, provider: TlsProviderSpec) -> _TokenCacheEntry:
        """Single-flight refresh if the cached token is missing or near expiry."""
        async with self._refresh_locks[provider.slug]:
            async with self._cache_lock:
                entry = self._cache.get(provider.slug)
            if entry is not None and entry.is_fresh(now=time.monotonic(), refresh_lead_seconds=self._refresh_lead_seconds):
                return entry
            return await self._refresh_provider(provider=provider)

    async def _refresh_provider(self, provider: TlsProviderSpec) -> _TokenCacheEntry:
        """Refresh one provider and update the cache."""
        result = await asyncio.to_thread(
            fetch_provider_token,
            refresh_config=self._refresh_config,
            provider=provider,
        )
        entry = _cache_entry_from_refresh_result(result=result, now=time.monotonic())
        async with self._cache_lock:
            self._cache[provider.slug] = entry
        logger.info("refreshed %s: %s", provider.slug, entry.status)
        return entry


class TlsInterceptRuntime:
    """TLS-intercept subsystem: proxy transport, token refresh, and status cards."""

    def __init__(
        self,
        providers: dict[str, TlsProviderSpec],
        refresh_config: DohRefreshConfig,
        refresh_lead_seconds: int,
        ca_dir: Path,
        private_dir: Path,
    ) -> None:
        self._token_store = _TokenStore(
            providers=providers,
            refresh_config=refresh_config,
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

    async def invalidate_all(self) -> None:
        """Drop every cached token entry; next status read refetches lazily."""
        await self._token_store.invalidate_all()


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
            body = await _read_body(reader=tls_reader, headers=headers)
            token = await token_store.token_for_host(host=host)
            if token is None:
                await _send_provider_not_connected(writer=tls_writer, provider=provider)
                return
            forward_headers, forward_path = _rewrite_request_for_provider(
                headers=headers,
                path_with_query=request_line.decode("iso-8859-1").split(" ", 2)[1],
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


def _rewrite_telegram_path(path_with_query: str, token: str) -> str:
    """Replace the sandbox placeholder token in Telegram Bot API paths."""
    bot_prefix = f"/bot{TELEGRAM_PLACEHOLDER_TOKEN}/"
    file_prefix = f"/file/bot{TELEGRAM_PLACEHOLDER_TOKEN}/"
    if path_with_query.startswith(bot_prefix):
        return "/bot" + token + "/" + path_with_query[len(bot_prefix):]
    if path_with_query.startswith(file_prefix):
        return "/file/bot" + token + "/" + path_with_query[len(file_prefix):]
    logger.error("telegram request path did not contain expected placeholder token: %s", path_with_query.split("?", 1)[0])
    return path_with_query


def _rewrite_request_for_provider(
    headers: list[tuple[bytes, bytes]],
    path_with_query: str,
    token: str,
    provider: TlsProviderSpec,
    upstream_host: str,
) -> tuple[list[tuple[bytes, bytes]], str]:
    """Rewrite credentials for the provider-specific upstream API shape."""
    if provider.credential_location == CREDENTIAL_LOCATION_AUTHORIZATION_HEADER:
        return (
            _rewrite_authorization(
                headers=headers,
                token=token,
                auth_format=provider.auth_format,
                upstream_host=upstream_host,
            ),
            path_with_query,
        )
    if provider.credential_location == CREDENTIAL_LOCATION_TELEGRAM_PATH:
        return (
            _strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host),
            _rewrite_telegram_path(path_with_query=path_with_query, token=token),
        )
    raise ValueError(f"unknown credential_location: {provider.credential_location!r}")


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
