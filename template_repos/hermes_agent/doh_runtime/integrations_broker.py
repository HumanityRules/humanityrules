"""Outside-the-sandbox HTTPS broker for per-user third-party integration tokens.

Runs as a supervisor-managed sidecar process. Two responsibilities:

1. **HTTPS forward proxy on 127.0.0.1:9950.** The nono sandbox gets
   HTTPS_PROXY=http://127.0.0.1:9950 and SSL_CERT_FILE pointed at our
   boot-generated CA bundle. Clients (e.g. gws) send CONNECT for
   gmail.googleapis.com:443 etc.; for hosts in our injection config, we
   terminate TLS with an on-demand leaf cert, swap the Authorization header
   for the real short-lived access token, and forward to real upstream over
   a fresh TLS client. For hosts *not* in the config (e.g. api.tavily.com),
   we opaque-tunnel — the sandbox sees normal end-to-end TLS and we inject
   nothing.

2. **Integrations control API on 127.0.0.1:9951** (Starlette/uvicorn, reached
   same-origin by the WebUI extension via the /__doh_broker/* reverse-proxy
   patch). One unified URL space for all browser-facing integration
   management:
   - GET  /healthz                  — liveness
   - GET  /integrations             — flat unified status (Google + aggregator);
                                      force-refreshes every TLS-intercept
                                      provider before responding.
   - …plus whatever routes mcp_aggregator.MCPAggregator.routes() returns,
     mounted under /integrations. The aggregator owns those handlers and
     declares its own URL surface; the broker just provides the mount point
     and the unified status endpoint that fans out to it.
   The aggregator's port 9952 is sandbox-only MCP traffic.

Token refresh for TLS-intercept providers is lazy: tokens are fetched on
the first request that needs them and re-fetched only when the cached
token is within REFRESH_LEAD_SECONDS of expiry. /integrations forces a
refresh so the UI always shows current state.

Refresh tokens, DOH's OAuth client secrets, and the env bearer NEVER enter
the sandbox. Only swapped-in short-lived access tokens reach Google — and
only inside a TLS body destined for Google, never as a file or env var the
sandbox can read.

Environment contract (set by deploy_app.py's env-bearer overlay):
- DOH_ENV_BEARER       — bearer for DOH's per-env integration endpoints.
- DOH_OWNER_USERNAME   — whose grants this container is for.
- DOH_CONTROL_PLANE_URL — base URL for DOH (e.g. https://devopshero.ai).
- DOH_ENV_SLUG         — env slug, for logging only.

Required file system:
- BROKER_CA_DIR (default /run/doh/integrations-broker/ca) must be writable by the broker user.
  The CA bundle is written here on startup for SSL_CERT_FILE to pick up.
- BROKER_PRIVATE_DIR (default /run/doh/integrations-broker/private) must be writable by the
  broker user and unreadable by the sandbox.
"""

import argparse
import asyncio
import contextlib
import datetime as dt
import ipaddress
import json
import logging
import os
import random
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import mcp_aggregator


DEFAULT_PROXY_PORT = 9950
DEFAULT_CONTROL_PORT = 9951
DEFAULT_MCP_PORT = 9952
DEFAULT_CA_DIR = Path("/run/doh/integrations-broker/ca")
DEFAULT_PRIVATE_DIR = Path("/run/doh/integrations-broker/private")
DEFAULT_MCP_PERSISTENT_DIR = Path("/hermes-persistent-root/mcp-aggregator")

# Refresh when the cached token is within this window of expiry. Wider than
# any reasonable refresh round-trip so the request waiting on us never sees a
# token that expires mid-flight upstream.
REFRESH_LEAD_SECONDS = 300

# Per-provider lock: many concurrent agent requests will race on the same
# host's first call after expiry; only one of them should hit DOH.
_provider_refresh_locks: dict[str, asyncio.Lock] = {}

# Cache of the last refresh outcome per provider. Read by the proxy hot path
# (to get a token) and by /integrations (to render status).
#
# Shape: {
#   "status": "connected" | "not_connected" | "revoked" | "transient_error",
#   "access_token": str | None,
#   "expires_at": float | None,            # monotonic seconds
#   "last_refreshed_at": str | None,       # ISO 8601, only set on "connected"
# }
_provider_cache: dict[str, dict] = {}
_provider_cache_lock = asyncio.Lock()

# Credentials for calling DOH's per-provider refresh endpoint. Set once in
# _run() from env vars; read by _refresh_provider so callers don't have to
# thread these three args through every helper.
_doh_refresh_config: dict[str, str] = {}


logger = logging.getLogger("integrations_broker")


# ── PROVIDERS registry ────────────────────────────────────────────────
#
# One entry per integration. Adding Slack/Notion/Linear means adding a dict
# here — everything else (refresh scheduling, header swap, status) is generic.
#
# Fields:
#   label           — human-readable name for status/UI.
#   refresh_path    — POST target on DOH control plane. Request body is
#                     {"owner_username": ...} with Authorization bearer env.
#   hosts           — upstream hostnames this provider's token authenticates.
#                     CONNECT to any of these will be intercepted + swapped.
PROVIDERS: dict[str, dict] = {
    "google": {
        "label": "Google Workspace",
        "refresh_path": "/api/integrations/google/token",
        "hosts": [
            "gmail.googleapis.com",
            "calendar-json.googleapis.com",
            "drive.googleapis.com",
            "docs.googleapis.com",
            "sheets.googleapis.com",
            "people.googleapis.com",
            "www.googleapis.com",
            "oauth2.googleapis.com",
        ],
    },
}


def _host_to_provider_slug(host: str) -> str | None:
    """Map an upstream hostname to the provider slug whose token authenticates it."""
    for slug, cfg in PROVIDERS.items():
        if host in cfg["hosts"]:
            return slug
    return None


# ── CA + leaf certs ───────────────────────────────────────────────────

class _CertMinter:
    """Boot-generated CA that mints leaf certs on demand, one per SNI hostname.

    CA private key is in-memory only — never written to disk. The public CA
    cert is written to `ca_dir/bundle.pem` so SSL_CERT_FILE can point at it.
    Leaf certs are loaded through broker-private temp files and cached
    indefinitely (broker process lifetime == container lifetime).
    """

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
                    key_encipherment=False, content_commitment=False,
                    data_encipherment=False, key_agreement=False,
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
        """Return an SSLContext presenting a leaf cert valid for hostname. Cached per host."""
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
        cert_path = self._pem_bytes_to_private_tmp(leaf_cert.public_bytes(serialization.Encoding.PEM))
        key_path = self._pem_bytes_to_private_tmp(
            leaf_key.private_bytes(
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


# ── HTTPS forward proxy ───────────────────────────────────────────────

async def _handle_proxy_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, minter: _CertMinter) -> None:
    """Accept a CONNECT, then either intercept (for known hosts) or tunnel."""
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
        provider_slug = _host_to_provider_slug(host=host)
        if provider_slug is None:
            await _tunnel_opaque(client_reader=reader, client_writer=writer, host=host, port=port)
            return
        await _intercept_and_forward(
            client_reader=reader,
            client_writer=writer,
            host=host,
            port=port,
            provider_slug=provider_slug,
            minter=minter,
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
    """Straight CONNECT tunnel for hosts we don't intercept (e.g. Tavily)."""
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
    provider_slug: str,
    minter: _CertMinter,
) -> None:
    """TLS-terminate with a minted leaf, swap Authorization, forward over our own TLS client."""
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
            token = await _current_token_for_host(host=host)
            if token is None:
                status_line = b"HTTP/1.1 503 Service Unavailable\r\n"
                msg = json.dumps({
                    "error": {
                        "code": 503,
                        "message": f"{provider_slug} integration not connected in DOH — connect it from the Integrations pane.",
                    },
                }).encode()
                tls_writer.write(status_line + b"Content-Type: application/json\r\nContent-Length: " + str(len(msg)).encode() + b"\r\nConnection: close\r\n\r\n" + msg)
                await tls_writer.drain()
                return
            forward_headers = _rewrite_authorization(headers=headers, token=token, upstream_host=host)
            try:
                upstream_status, upstream_headers, upstream_body = await _forward_to_upstream(
                    host=host,
                    port=port,
                    method=request_line.decode("iso-8859-1").split(" ", 1)[0],
                    path_with_query=request_line.decode("iso-8859-1").split(" ", 2)[1],
                    headers=forward_headers,
                    body=body,
                )
            except Exception as exc:
                logger.exception("forward to %s failed", host)
                msg = json.dumps({"error": {"code": 502, "message": f"broker upstream error: {exc}"}}).encode()
                tls_writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\nContent-Length: " + str(len(msg)).encode() + b"\r\nConnection: close\r\n\r\n" + msg)
                await tls_writer.drain()
                return
            tls_writer.write(_render_response(status=upstream_status, headers=upstream_headers, body=upstream_body))
            await tls_writer.drain()
            # Not advertising keep-alive downstream — clean close per request is simpler.
            if _header_value(headers=upstream_headers, name=b"connection") == b"close":
                return
    finally:
        with contextlib.suppress(Exception):
            tls_writer.close()
            await tls_writer.wait_closed()


async def _pump_both_ways(a_reader: asyncio.StreamReader, a_writer: asyncio.StreamWriter, b_reader: asyncio.StreamReader, b_writer: asyncio.StreamWriter) -> None:
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
    """Read a request body per Content-Length / Transfer-Encoding. No streaming; uploads buffered."""
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


def _rewrite_authorization(headers: list[tuple[bytes, bytes]], token: str, upstream_host: str) -> list[tuple[bytes, bytes]]:
    bearer = b"Bearer " + token.encode()
    host_override = upstream_host.encode()
    rewritten: list[tuple[bytes, bytes]] = []
    seen_auth = False
    for name, value in headers:
        if name == b"authorization":
            rewritten.append((b"Authorization", bearer))
            seen_auth = True
            continue
        if name == b"host":
            rewritten.append((b"Host", host_override))
            continue
        if name in (b"proxy-connection", b"proxy-authorization"):
            continue
        rewritten.append((name, value))
    if not seen_auth:
        rewritten.append((b"Authorization", bearer))
    return rewritten


async def _forward_to_upstream(host: str, port: int, method: str, path_with_query: str, headers: list[tuple[bytes, bytes]], body: bytes) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    """Open a fresh TLS client to real upstream, replay the request, return the response."""
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
    te_name = b"transfer-encoding"
    te_value = None
    for n, v in headers:
        if n.lower() == te_name:
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
    # Read to EOF if neither header is set.
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


# ── Provider refresh ──────────────────────────────────────────────────
#
# Refresh is lazy: we fetch a token only when one is needed and the cache is
# missing or about to expire. Per-provider lock guarantees single-flight, so
# concurrent first-callers all wait on the same in-flight refresh and then
# read the fresh cache entry.

# Refresh outcomes from DOH's per-provider endpoint. The proxy treats anything
# other than "connected" the same way (no token → 503), so the value matters
# only for /integrations rendering and logging.
_OUTCOMES = {
    200: "connected",          # token in payload
    404: "not_connected",      # user hasn't connected this provider
    410: "revoked",            # refresh token gone
    401: "transient_error",    # bad env bearer; treat as transient so we keep trying
    500: "transient_error",    # DOH misconfig
}


def _fetch_provider_token(control_plane_url: str, bearer: str, owner_username: str, refresh_path: str) -> dict:
    """Call DOH's per-provider refresh endpoint. Returns a status dict.

    Shape: {"status": <see _OUTCOMES>, "access_token": str|None, "expires_in": int|None}
    Network errors and unexpected statuses come back as transient_error.
    """
    url = f"{control_plane_url.rstrip('/')}{refresh_path}"
    body = json.dumps({"owner_username": owner_username}).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
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
        logger.error("refresh network error for %s: %s", refresh_path, exc)
        return {"status": "transient_error", "access_token": None, "expires_in": None}

    if status == 200:
        return {
            "status": "connected",
            "access_token": payload["access_token"],
            "expires_in": int(payload.get("expires_in", 0)),
        }
    outcome = _OUTCOMES.get(status, "transient_error")
    if outcome == "transient_error":
        logger.error("refresh got http %d for %s: %s", status, refresh_path, payload.get("error", ""))
    return {"status": outcome, "access_token": None, "expires_in": None}


async def _refresh_provider(slug: str) -> dict:
    """Force a refresh for one provider and update the cache. Returns the new entry."""
    cfg = PROVIDERS[slug]
    outcome = await asyncio.to_thread(
        _fetch_provider_token,
        control_plane_url=_doh_refresh_config["control_plane_url"],
        bearer=_doh_refresh_config["bearer"],
        owner_username=_doh_refresh_config["owner_username"],
        refresh_path=cfg["refresh_path"],
    )
    entry = {
        "status": outcome["status"],
        "access_token": outcome["access_token"],
        "expires_at": (
            time.monotonic() + outcome["expires_in"] if outcome["status"] == "connected" else None
        ),
        "last_refreshed_at": (
            dt.datetime.now(dt.timezone.utc).isoformat() if outcome["status"] == "connected" else None
        ),
    }
    async with _provider_cache_lock:
        _provider_cache[slug] = entry
    logger.info("refreshed %s: %s", slug, entry["status"])
    return entry


async def _ensure_fresh(slug: str) -> dict:
    """Return a cache entry that's either non-connected or has a token >LEAD seconds from expiry.

    Single-flights via the per-provider lock: concurrent callers see the same
    refreshed entry without firing duplicate requests at DOH.
    """
    async with _provider_refresh_locks[slug]:
        async with _provider_cache_lock:
            entry = _provider_cache.get(slug)
        if entry and entry["status"] == "connected" and entry["expires_at"] - time.monotonic() > REFRESH_LEAD_SECONDS:
            return entry
        return await _refresh_provider(slug=slug)


async def _refresh_all_providers() -> None:
    """Force-refresh every provider. Used by /integrations so the UI sees current state."""
    for slug in PROVIDERS:
        async with _provider_refresh_locks[slug]:
            await _refresh_provider(slug=slug)


async def _current_token_for_host(host: str) -> str | None:
    """Token for an upstream host, refreshing lazily. None if the user isn't connected."""
    slug = _host_to_provider_slug(host=host)
    if slug is None:
        return None
    entry = await _ensure_fresh(slug=slug)
    return entry["access_token"]


# ── Integrations control API (Starlette on 127.0.0.1:9951) ───────────
#
# The unified /integrations endpoint returns a flat list of cards: TLS-intercept
# providers (Google) and MCP-aggregator providers (Notion) coexist. The WebUI
# renders one card per entry, branching click handlers on `kind`.
#
# MCP-aggregator routes (oauth start/callback, disconnect) are mounted here so
# that browser traffic for both kinds of integrations shares one URL space and
# the aggregator's port 9952 is sandbox-only MCP transport.


async def _handle_unified_status(
    request: Request,
    aggregator: mcp_aggregator.MCPAggregator,
    control_plane_url: str,
    owner_username: str,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers (Google) and MCP-aggregator items.

    Force-refreshes every TLS-intercept provider so the rendered status reflects
    DOH's current view of the user's grants, not whatever the proxy last saw.
    """
    await _refresh_all_providers()
    items: list[dict] = []
    async with _provider_cache_lock:
        snapshot = {slug: dict(entry) for slug, entry in _provider_cache.items()}
    for slug, cfg in PROVIDERS.items():
        entry = snapshot.get(slug, {})
        items.append({
            "kind": "tls_intercept",
            "slug": slug,
            "label": cfg["label"],
            "status": entry.get("status", "transient_error"),
            "last_refreshed_at": entry.get("last_refreshed_at"),
        })
    items.extend(await aggregator.status_items(request=request))
    return JSONResponse(content={
        "doh_control_plane_url": control_plane_url,
        "env_slug": env_slug,
        "owner_username": owner_username,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def _build_control_app(
    aggregator: mcp_aggregator.MCPAggregator,
    control_plane_url: str,
    owner_username: str,
    env_slug: str,
) -> Starlette:
    """Wire the unified /__doh_broker/* router for browser-facing integration management."""
    async def status_route(request: Request) -> Response:
        return await _handle_unified_status(
            request=request,
            aggregator=aggregator,
            control_plane_url=control_plane_url,
            owner_username=owner_username,
            env_slug=env_slug,
        )

    async def refresh_catalog_route(request: Request) -> Response:
        ok, payload = await aggregator.refresh_catalog()
        return JSONResponse(content=payload, status_code=200 if ok else 429)

    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/refresh_catalog", endpoint=refresh_catalog_route, methods=["POST"]),
        *aggregator.routes(prefix="/integrations"),
    ]
    return Starlette(routes=routes)


# ── Wiring ────────────────────────────────────────────────────────────

def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


async def _run(proxy_port: int, control_port: int, mcp_port: int, ca_dir: Path, private_dir: Path, mcp_persistent_dir: Path) -> None:
    control_plane_url = _require_env(name="DOH_CONTROL_PLANE_URL")
    bearer = _require_env(name="DOH_ENV_BEARER")
    owner_username = _require_env(name="DOH_OWNER_USERNAME")
    app_slug = _require_env(name="DOH_APP_SLUG")
    env_slug = os.environ.get("DOH_ENV_SLUG", "")
    logger.info(
        "starting integrations_broker for owner=%s env=%s against %s (proxy=%d, control=%d, mcp=%d)",
        owner_username, env_slug, control_plane_url, proxy_port, control_port, mcp_port,
    )
    minter = _CertMinter(ca_dir=ca_dir, private_dir=private_dir)
    minter.bootstrap()

    _doh_refresh_config["control_plane_url"] = control_plane_url
    _doh_refresh_config["bearer"] = bearer
    _doh_refresh_config["owner_username"] = owner_username
    for slug in PROVIDERS:
        _provider_refresh_locks[slug] = asyncio.Lock()

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, lambda s=sig: (logger.info("signal %d; shutting down", s), stop.done() or stop.set_result(None)))

    async def _proxy_cb(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await _handle_proxy_conn(reader=r, writer=w, minter=minter)

    proxy_server = await asyncio.start_server(client_connected_cb=_proxy_cb, host="127.0.0.1", port=proxy_port)
    logger.info("proxy listening on 127.0.0.1:%d", proxy_port)

    public_base_url = os.environ.get("DOH_APP_PUBLIC_URL")
    mcp_persistent_dir.mkdir(parents=True, exist_ok=True)
    aggregator = mcp_aggregator.MCPAggregator(
        port=mcp_port,
        persistent_dir=mcp_persistent_dir,
        public_base_url=public_base_url,
        doh_control_plane_url=control_plane_url,
        doh_env_bearer=bearer,
        doh_app_slug=app_slug,
        doh_owner_username=owner_username,
    )

    control_app = _build_control_app(
        aggregator=aggregator,
        control_plane_url=control_plane_url,
        owner_username=owner_username,
        env_slug=env_slug,
    )
    control_uvicorn_config = uvicorn.Config(
        app=control_app, host="127.0.0.1", port=control_port, log_level="warning", access_log=False,
    )
    control_server = uvicorn.Server(config=control_uvicorn_config)
    # The broker process owns signal handling; uvicorn must not install its own.
    control_server.install_signal_handlers = lambda: None
    logger.info("control API listening on 127.0.0.1:%d", control_port)

    async with proxy_server:
        proxy_task = asyncio.create_task(proxy_server.serve_forever())
        control_task = asyncio.create_task(control_server.serve())
        mcp_task = asyncio.create_task(aggregator.serve())
        done, pending = await asyncio.wait(
            {stop, proxy_task, control_task, mcp_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            exc = task.exception() if task.done() and not task.cancelled() else None
            if exc:
                logger.error("task exited: %r", exc)
                raise exc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [integrations_broker] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT)
    parser.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    parser.add_argument("--private-dir", type=Path, default=DEFAULT_PRIVATE_DIR)
    parser.add_argument("--mcp-persistent-dir", type=Path, default=DEFAULT_MCP_PERSISTENT_DIR)
    args = parser.parse_args()
    try:
        asyncio.run(
            _run(
                proxy_port=args.proxy_port,
                control_port=args.control_port,
                mcp_port=args.mcp_port,
                ca_dir=args.ca_dir,
                private_dir=args.private_dir,
                mcp_persistent_dir=args.mcp_persistent_dir,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
