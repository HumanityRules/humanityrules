"""MITM proxy transport: CONNECT handling, credential rewrite, and upstream relay."""

import asyncio
import base64
import contextlib
import json
import logging
import ssl

import tls_cert_minter
import tls_providers
import tls_token_store


logger = logging.getLogger("tls_intercept")


async def _handle_proxy_conn(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    minter: tls_cert_minter._CertMinter,
    token_store: tls_token_store._TokenStore,
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
    minter: tls_cert_minter._CertMinter,
    token_store: tls_token_store._TokenStore,
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
                addresses_humr_credential = _request_addresses_humr_credential(
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
            if addresses_humr_credential:
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
            if upstream_status == 401 and addresses_humr_credential:
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
        message=f"{provider.slug} integration not connected in HUMR — connect it from the Integrations pane.",
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
    """A request didn't carry a recognizable placeholder, or a required secret is missing from the cache."""


def _strip_bearer_prefix(value: bytes) -> str:
    """Return the token from a `Bearer <token>` header value (case-insensitive prefix)."""
    text = value.decode("iso-8859-1").strip()
    if text[:7].lower() == "bearer ":
        return text[7:].strip()
    return text


def _request_addresses_humr_credential(headers: list[tuple[bytes, bytes]], path_with_query: str, provider: tls_providers.TlsProviderSpec) -> bool:
    """Decide whether a request asks for HUMR's credential or is anonymous public traffic."""
    method = provider.credential_method
    if isinstance(method, (tls_providers.OAuthHeader, tls_providers.OAuthHeaderMultiInject)):
        return True
    if isinstance(method, tls_providers.VaultUrlRewrite):
        if method.placeholder not in path_with_query:
            raise _SecretSelectionError("request URL must contain the HUMR placeholder")
        return True
    if isinstance(method, tls_providers.VaultHeaderInject):
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        if incoming is None:
            return False
        if method.secret_for_placeholder(_strip_bearer_prefix(incoming)) is None:
            raise _SecretSelectionError("request Authorization did not carry a known HUMR placeholder")
        return True
    if isinstance(method, tls_providers.VaultApiKeyHeader):
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        if incoming is not None:
            if incoming.decode("iso-8859-1").strip() != method.placeholder:
                raise _SecretSelectionError(f"request {method.header_name} did not carry the HUMR placeholder")
            return True
        if any(n.lower() == b"authorization" for n, _v in headers):
            raise _SecretSelectionError(f"request carried Authorization instead of the {method.header_name} HUMR placeholder")
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
    """Force `extra` header values, replacing any the client sent (case-insensitive)."""
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
    """Rewrite credentials for the provider-specific upstream API shape."""
    method = provider.credential_method
    if isinstance(method, tls_providers.OAuthHeader):
        return (
            _rewrite_authorization(
                headers=headers,
                token=tls_token_store._primary_secret(secrets),
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
        token = tls_token_store._primary_secret(secrets)
        if method.placeholder not in path_with_query:
            raise _SecretSelectionError("request URL must contain the HUMR placeholder")
        return (
            _strip_proxy_headers_and_set_host(headers=headers, upstream_host=upstream_host),
            path_with_query.replace(method.placeholder, token),
        )
    if isinstance(method, tls_providers.VaultHeaderInject):
        incoming = next((v for n, v in headers if n.lower() == b"authorization"), None)
        bearer = _strip_bearer_prefix(incoming) if incoming is not None else None
        secret_name = method.secret_for_placeholder(bearer) if bearer is not None else None
        if secret_name is None:
            raise _SecretSelectionError("request Authorization did not carry a known HUMR placeholder")
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
        header_lower = method.header_name.lower().encode()
        incoming = next((v for n, v in headers if n.lower() == header_lower), None)
        incoming_value = incoming.decode("iso-8859-1").strip() if incoming is not None else None
        if incoming_value != method.placeholder:
            raise _SecretSelectionError(f"request {method.header_name} did not carry the HUMR placeholder")
        token = tls_token_store._primary_secret(secrets)
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
    """Replay the request to the real upstream and relay the response to the client."""
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
    """Relay an SSE response head + body to the client, flushing per frame."""
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
                return False
            remaining -= len(chunk)
            client_writer.write(chunk)
            await client_writer.drain()
        return True
    while True:
        chunk = await upstream_reader.read(65536)
        if not chunk:
            break
        client_writer.write(chunk)
        await client_writer.drain()
    return False


async def _relay_chunked_stream(upstream_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> bool:
    """Relay a chunked upstream body to the client one chunk at a time, flushing each."""
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
