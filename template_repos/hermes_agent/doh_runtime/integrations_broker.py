"""Outside-the-sandbox HTTPS broker for per-user third-party integration tokens.

Runs as a supervisor-managed sidecar (same trust level as the sigv4 / haproxy
helpers). Two responsibilities:

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
   - GET  /integrations             — flat unified status (Google + aggregator)
   - POST /integrations/google/kick — synchronous Google refresh
   - …plus whatever routes mcp_aggregator.MCPAggregator.routes() returns,
     mounted under /integrations. The aggregator owns those handlers and
     declares its own URL surface; the broker just provides the mount point
     and the unified status endpoint that fans out to it.
   The aggregator's port 9952 is sandbox-only MCP traffic.

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
import socket
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


DEFAULT_PROXY_PORT = 9950
DEFAULT_CONTROL_PORT = 9951
DEFAULT_MCP_PORT = 9952
DEFAULT_CA_DIR = Path("/run/doh/integrations-broker/ca")
DEFAULT_PRIVATE_DIR = Path("/run/doh/integrations-broker/private")
DEFAULT_MCP_PERSISTENT_DIR = Path("/hermes-persistent-root/mcp-aggregator")

# Refresh ~5 minutes before the typical 3600s expiry. Backoff for transient
# errors. MIN_SLEEP guards against a tight loop if DOH's expires_in is tiny.
REFRESH_LEAD_SECONDS = 300
MIN_SLEEP_SECONDS = 30
NOT_CONNECTED_POLL_SECONDS = 60
BACKOFF_INITIAL_SECONDS = 5
BACKOFF_MAX_SECONDS = 300

# Serializes refreshes per provider so a timer refresh and a synchronous /kick
# cannot race each other.
_provider_refresh_locks: dict[str, asyncio.Lock] = {}

# In-memory state keyed by provider slug. Exposed through /status and /kick.
_provider_state: dict[str, dict] = {}
_provider_state_lock = asyncio.Lock()

# host → bearer token, populated by provider refresh loops. Read on every
# intercepted request. String (not bytes) for debuggability.
_host_token: dict[str, str] = {}
_host_token_lock = asyncio.Lock()


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
    has_cl = False
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


# ── Provider refresh loops ────────────────────────────────────────────

def _fetch_provider_token(control_plane_url: str, bearer: str, owner_username: str, refresh_path: str) -> dict:
    """Call DOH's per-provider refresh endpoint. Returns a classified outcome dict.

    Kinds:
      {"kind": "ok", "access_token": str, "expires_in": int}
      {"kind": "not_connected"}                           — DOH 404
      {"kind": "revoked"}                                 — DOH 410
      {"kind": "transient", "detail": str}                — retryable (network, 5xx)
      {"kind": "fatal", "detail": str}                    — unrecoverable (401 bad bearer, 500 misconfig)
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
    except urllib.error.URLError as exc:
        return {"kind": "transient", "detail": f"network: {exc.reason}"}
    except Exception as exc:
        return {"kind": "transient", "detail": f"unexpected: {exc}"}

    if status == 200:
        return {"kind": "ok", "access_token": payload["access_token"], "expires_in": int(payload.get("expires_in", 0))}
    if status == 404:
        return {"kind": "not_connected"}
    if status == 410:
        return {"kind": "revoked"}
    if status in (401, 500):
        return {"kind": "fatal", "detail": f"http {status}: {payload.get('error', 'unknown')}"}
    return {"kind": "transient", "detail": f"http {status}: {payload.get('error', 'unknown')}"}


async def _refresh_provider_once(
    slug: str,
    cfg: dict,
    control_plane_url: str,
    bearer: str,
    owner_username: str,
    backoff: float,
) -> tuple[float, float]:
    """Refresh one provider and return the next sleep and backoff values."""
    label = cfg["label"]
    refresh_path = cfg["refresh_path"]
    hosts = cfg["hosts"]

    outcome = await asyncio.to_thread(
        _fetch_provider_token,
        control_plane_url=control_plane_url,
        bearer=bearer,
        owner_username=owner_username,
        refresh_path=refresh_path,
    )
    kind = outcome["kind"]
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    if kind == "ok":
        async with _host_token_lock:
            for host in hosts:
                _host_token[host] = outcome["access_token"]
        async with _provider_state_lock:
            _provider_state[slug] = {"label": label, "status": "connected", "last_refreshed_at": now_iso}
        sleep_for = max(MIN_SLEEP_SECONDS, outcome["expires_in"] - REFRESH_LEAD_SECONDS)
        logger.info("refreshed %s token, next refresh in %ds", slug, sleep_for)
        return sleep_for, BACKOFF_INITIAL_SECONDS
    if kind == "not_connected":
        async with _host_token_lock:
            for host in hosts:
                _host_token.pop(host, None)
        async with _provider_state_lock:
            _provider_state[slug] = {"label": label, "status": "not_connected", "last_refreshed_at": None}
        logger.info("%s not connected for user=%s, polling in %ds", slug, owner_username, NOT_CONNECTED_POLL_SECONDS)
        return NOT_CONNECTED_POLL_SECONDS, BACKOFF_INITIAL_SECONDS
    if kind == "revoked":
        async with _host_token_lock:
            for host in hosts:
                _host_token.pop(host, None)
        async with _provider_state_lock:
            _provider_state[slug] = {"label": label, "status": "revoked", "last_refreshed_at": None}
        logger.error("%s refresh token revoked for user=%s, polling in %ds", slug, owner_username, NOT_CONNECTED_POLL_SECONDS)
        return NOT_CONNECTED_POLL_SECONDS, BACKOFF_INITIAL_SECONDS
    if kind == "fatal":
        async with _provider_state_lock:
            _provider_state[slug] = {"label": label, "status": "fatal_error", "last_refreshed_at": None}
        raise RuntimeError(f"{slug} refresh returned {outcome['detail']}")

    async with _provider_state_lock:
        _provider_state[slug] = {"label": label, "status": "transient_error", "last_refreshed_at": None}
    sleep_for = backoff + random.uniform(0, backoff / 2)
    logger.error("%s transient refresh failure (%s), retrying in %.1fs", slug, outcome["detail"], sleep_for)
    return sleep_for, min(backoff * 2, BACKOFF_MAX_SECONDS)


async def _refresh_provider_locked(
    slug: str,
    cfg: dict,
    control_plane_url: str,
    bearer: str,
    owner_username: str,
    backoff: float,
) -> tuple[float, float]:
    """Run a provider refresh with its per-provider lock held."""
    async with _provider_refresh_locks[slug]:
        return await _refresh_provider_once(
            slug=slug,
            cfg=cfg,
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            backoff=backoff,
        )


async def _refresh_all_providers_once(control_plane_url: str, bearer: str, owner_username: str) -> None:
    """Synchronously refresh every provider before returning."""
    for slug, cfg in PROVIDERS.items():
        await _refresh_provider_locked(
            slug=slug,
            cfg=cfg,
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            backoff=BACKOFF_INITIAL_SECONDS,
        )


async def _refresh_loop(slug: str, cfg: dict, control_plane_url: str, bearer: str, owner_username: str) -> None:
    """Refresh one provider on a background timer."""
    backoff = BACKOFF_INITIAL_SECONDS

    while True:
        sleep_for, backoff = await _refresh_provider_locked(
            slug=slug,
            cfg=cfg,
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            backoff=backoff,
        )
        await asyncio.sleep(delay=sleep_for)


async def _current_token_for_host(host: str) -> str | None:
    async with _host_token_lock:
        return _host_token.get(host)


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
    aggregator: "mcp_aggregator.MCPAggregator",
    control_plane_url: str,
    owner_username: str,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers (Google) and MCP-aggregator providers."""
    items: list[dict] = []
    async with _provider_state_lock:
        google_snapshot = {slug: dict(state) for slug, state in _provider_state.items()}
    for slug, cfg in PROVIDERS.items():
        state = google_snapshot.get(slug, {})
        items.append({
            "kind": "tls_intercept",
            "slug": slug,
            "label": state.get("label", cfg["label"]),
            "status": state.get("status", "starting"),
            "last_refreshed_at": state.get("last_refreshed_at"),
        })
    mcp_status = await aggregator.handle_status(request=request)
    mcp_payload = json.loads(mcp_status.body.decode())
    for slug, state in mcp_payload.get("providers", {}).items():
        items.append({
            "kind": "mcp_aggregator",
            "slug": slug,
            "label": state.get("label", slug),
            "status": state.get("status", "unknown"),
        })
    merge_response = await aggregator.handle_merge_connectors(request=request)
    if merge_response.status_code == 200:
        merge_payload = json.loads(merge_response.body.decode())
        for connector in merge_payload.get("connectors", []):
            items.append({
                "kind": "merge_connector",
                "slug": connector.get("slug"),
                "label": connector.get("name"),
                "logo_url": connector.get("logo_url"),
                "status": connector.get("status", "unknown"),
            })
    else:
        logger.error("merge connectors fan-out failed: status=%d", merge_response.status_code)
    return JSONResponse(content={
        "doh_control_plane_url": control_plane_url,
        "env_slug": env_slug,
        "owner_username": owner_username,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


async def _handle_google_kick(
    request: Request,
    control_plane_url: str,
    bearer: str,
    owner_username: str,
    env_slug: str,
    aggregator: "mcp_aggregator.MCPAggregator",
) -> Response:
    """Force-refresh every TLS-intercept provider, then return the unified status."""
    try:
        await _refresh_all_providers_once(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
        )
    except RuntimeError as exc:
        logger.error("synchronous refresh failed: %s", exc)
        response = await _handle_unified_status(
            request=request,
            aggregator=aggregator,
            control_plane_url=control_plane_url,
            owner_username=owner_username,
            env_slug=env_slug,
        )
        payload = json.loads(response.body.decode())
        payload["error"] = str(exc)
        return JSONResponse(content=payload, status_code=500)
    return await _handle_unified_status(
        request=request,
        aggregator=aggregator,
        control_plane_url=control_plane_url,
        owner_username=owner_username,
        env_slug=env_slug,
    )


def _build_control_app(
    aggregator: "mcp_aggregator.MCPAggregator",
    control_plane_url: str,
    bearer: str,
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

    async def kick_route(request: Request) -> Response:
        return await _handle_google_kick(
            request=request,
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            env_slug=env_slug,
            aggregator=aggregator,
        )

    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/google/kick", endpoint=kick_route, methods=["POST"]),
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

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mcp_aggregator
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
        bearer=bearer,
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

    refreshers = [
        asyncio.create_task(_refresh_loop(
            slug=slug,
            cfg=cfg,
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
        ))
        for slug, cfg in PROVIDERS.items()
    ]

    async with proxy_server:
        proxy_task = asyncio.create_task(proxy_server.serve_forever())
        control_task = asyncio.create_task(control_server.serve())
        mcp_task = asyncio.create_task(aggregator.serve())
        done, pending = await asyncio.wait(
            {stop, proxy_task, control_task, mcp_task, *refreshers},
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
