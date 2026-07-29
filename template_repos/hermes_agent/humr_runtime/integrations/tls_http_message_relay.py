"""HTTP/1.1 wire mechanics for the TLS-intercept proxy: parse, frame, replay, relay.

Provider-agnostic, and that is the discipline that keeps this file
comprehensible: nothing here knows that providers, credentials, or tokens
exist. It reads a request off a TLS-terminated socket, replays it to the real
upstream, and streams the response back in the upstream's own framing.

Bodies are never buffered whole, in either direction. Broker RSS would
otherwise track the largest response ever proxied (git clone packs through
github.com reached multi-GB peaks), and per-chunk flushing is also what keeps
SSE deltas live for the sandbox client.

Request-side and response-side framing rules are deliberately different, and
they sit next to each other here so the asymmetry is visible: a request body
must be chunked-alone or Content-Length, because the broker re-frames what it
forwards, while a response body is relayed in whatever framing arrived.
"""

import asyncio
import contextlib
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Literal


logger = logging.getLogger("tls_http_message_relay")


_CONTENT_LENGTH_RE = re.compile(rb"^\d+$")
_CHUNK_SIZE_RE = re.compile(rb"^[0-9A-Fa-f]+$")


def parse_headers(lines: list[bytes]) -> list[tuple[bytes, bytes]]:
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


def _header_values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    """Return every value for one header name, preserving field order."""
    normalized_name = name.lower()
    return [value.lower() for field_name, value in headers if field_name.lower() == normalized_name]


async def read_body(reader: asyncio.StreamReader, headers: list[tuple[bytes, bytes]]) -> bytes:
    """Read a request body per Content-Length / Transfer-Encoding.

    Transfer-Encoding takes precedence over Content-Length, mirroring the
    response-side rule — reading by CL when TE is present would leave chunk
    framing bytes on the socket to be parsed as the next request. Chunked is
    the only transfer coding the broker can decode; other or repeated codings
    and malformed CL values are rejected as ValueError, which the request loop
    answers with a 400.
    """
    transfer_encoding_values = _header_values(headers=headers, name=b"transfer-encoding")
    if transfer_encoding_values:
        encodings = [token.strip() for value in transfer_encoding_values for token in value.split(b",")]
        # Dechunking is the only request transfer coding the broker implements.
        # Accepting a preceding coding (for example gzip, chunked) and then
        # stripping Transfer-Encoding upstream would silently change semantics.
        if encodings != [b"chunked"]:
            raise ValueError(f"unsupported request Transfer-Encoding: {b', '.join(encodings)!r}")
        return await _read_chunked(reader=reader)

    content_length_values = [token.strip() for value in _header_values(headers=headers, name=b"content-length") for token in value.split(b",")]
    if not content_length_values:
        return b""
    if any(not _CONTENT_LENGTH_RE.fullmatch(value) for value in content_length_values):
        raise ValueError(f"invalid request Content-Length: {b', '.join(content_length_values)!r}")
    if len(set(content_length_values)) != 1:
        raise ValueError(f"conflicting request Content-Length values: {content_length_values!r}")
    remaining = int(content_length_values[0])
    if remaining == 0:
        return b""
    try:
        return await reader.readexactly(remaining)
    except asyncio.IncompleteReadError as exc:
        raise ValueError(f"request body ended after {len(exc.partial)} of {remaining} bytes") from exc


async def _read_chunked(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    while True:
        size_line = await reader.readline()
        if not size_line:
            raise ValueError("request chunked body ended before a chunk size")
        if not size_line.endswith(b"\r\n"):
            raise ValueError("request chunk size line did not end with CRLF")
        size_token = size_line[:-2].split(b";", 1)[0].strip()
        if not _CHUNK_SIZE_RE.fullmatch(size_token):
            raise ValueError(f"invalid request chunk size: {size_token!r}")
        size = int(size_token, 16)
        if size == 0:
            while True:
                trailer_line = await reader.readline()
                if not trailer_line:
                    raise ValueError("request chunked body ended inside trailers")
                if trailer_line == b"\r\n":
                    return b"".join(chunks)
                if not trailer_line.endswith(b"\r\n"):
                    raise ValueError("request trailer line did not end with CRLF")
        try:
            chunks.append(await reader.readexactly(size))
            delimiter = await reader.readexactly(2)
        except asyncio.IncompleteReadError as exc:
            raise ValueError("request chunk payload ended before its declared boundary") from exc
        if delimiter != b"\r\n":
            raise ValueError("request chunk payload was not followed by CRLF")


def strip_proxy_headers_and_set_host(headers: list[tuple[bytes, bytes]], upstream_host: str) -> list[tuple[bytes, bytes]]:
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


_upstream_ssl_context: ssl.SSLContext | None = None


def _get_upstream_ssl_context() -> ssl.SSLContext:
    """Return the shared upstream client SSLContext, creating it on first use.

    A context is safe to share across connections, and creating one per
    request re-parses the entire system CA store — measurable allocator churn
    under concurrent proxy traffic.
    """
    global _upstream_ssl_context
    if _upstream_ssl_context is None:
        _upstream_ssl_context = ssl.create_default_context()
    return _upstream_ssl_context


async def forward_to_upstream(
    host: str,
    port: int,
    method: str,
    path_with_query: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    client_writer: asyncio.StreamWriter,
) -> tuple[int, bool]:
    """Replay the request to the real upstream and relay the response to the client.

    The response head is forwarded verbatim and the body is relayed chunk by
    chunk in its upstream framing — never buffered whole. Streaming keeps SSE
    deltas live for the sandbox client AND caps broker memory at one relay
    chunk per in-flight response; buffering entire bodies made broker RSS
    track the largest response ever proxied (git clone packs through
    github.com reached multi-GB peaks). Returns ``(status, keep_alive)``;
    ``keep_alive`` is False when the client connection must be torn down
    after this exchange.
    """
    ctx = _get_upstream_ssl_context()
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
        # Interim (1xx) responses precede the final one on the same
        # connection: relay each interim head verbatim and keep reading, so
        # an interim 100/103 doesn't desync the stream and the final status
        # (which drives keep-alive and the 401 evict) is the one acted on.
        # (The proxy reads the full request body before forwarding, so a
        # client waiting on 100-continue waits out its expect timeout first —
        # pre-existing behavior.) 101 is the exception: it has no following
        # response — the connection switches protocols. Upgrades aren't
        # supported (the request normalizer strips `Upgrade`/`Connection`),
        # so a stray 101 is final and force-closes.
        while True:
            status, response_headers = await _read_response_head(reader=upstream_reader)
            if not (100 <= status < 200) or status == 101:
                break
            client_writer.write(_render_response_head(status=status, headers=response_headers))
            await client_writer.drain()
        connection_keep_alive = status != 101 and not connection_close_requested(headers=response_headers)

        # Statuses that cannot carry a body (HEAD, 1xx, 204, 304) stop at
        # the head even when framing headers are present (a 304 echoes
        # the would-be body's Content-Length) — the relay would otherwise
        # wait on body bytes that never come and hang a valid response.
        response_can_have_body = not (method.upper() == "HEAD" or 100 <= status < 200 or status in (204, 304))
        if response_can_have_body:
            # Parsing (and validating) the framing BEFORE the head is written
            # keeps invalid-framing failures on the clean-502 path below.
            framing, forward_headers = _parse_response_framing(headers=response_headers)
        else:
            framing, forward_headers = None, response_headers

        client_writer.write(_render_response_head(status=status, headers=forward_headers))
        await client_writer.drain()

        # Past this point the head is committed to the client: an error can
        # no longer be reported as an HTTP response without corrupting the
        # byte stream (the client would read it as body data). On failure,
        # tear the connection down instead — truncation is detectable from
        # the framing; an injected 502 mid-body is silent corruption.
        try:
            if not response_can_have_body:
                return status, connection_keep_alive
            framed = await _relay_response_body(
                upstream_reader=upstream_reader,
                client_writer=client_writer,
                framing=framing,
            )
            return status, connection_keep_alive and framed
        except Exception:
            logger.exception("relay from %s failed after response head was sent", host)
            return status, False
    finally:
        with contextlib.suppress(Exception):
            upstream_writer.close()
            await upstream_writer.wait_closed()


async def _read_response_head(reader: asyncio.StreamReader) -> tuple[int, list[tuple[bytes, bytes]]]:
    """Read one response status line + header block from the upstream."""
    status_line = await reader.readline()
    try:
        status = int(status_line.split(b" ", 2)[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"bad upstream status line: {status_line!r}")
    headers: list[tuple[bytes, bytes]] = []
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        if b":" not in line:
            continue
        name, _, value = line.partition(b":")
        headers.append((name.strip(), value.strip().rstrip(b"\r\n")))
    return status, headers


def connection_close_requested(headers: list[tuple[bytes, bytes]]) -> bool:
    """True when any Connection field carries a `close` token.

    Connection is a comma-separated token list AND may legally appear as
    multiple field lines — every occurrence is scanned, not just the first.
    """
    for name, value in headers:
        if name.lower() != b"connection":
            continue
        if b"close" in {token.strip() for token in value.lower().split(b",")}:
            return True
    return False


@dataclass(frozen=True)
class _BodyFraming:
    """How one upstream response body is delimited on the wire."""

    kind: Literal["chunked", "content_length", "eof"]
    content_length: int | None


def _parse_response_framing(headers: list[tuple[bytes, bytes]]) -> tuple[_BodyFraming, list[tuple[bytes, bytes]]]:
    """Decide the response body framing and the headers to forward with it.

    Transfer-Encoding takes precedence over Content-Length (RFC 9112 §6.3):
    following CL when TE is present would let the next response's bytes be
    read as body data, and an intermediary must not forward both — CL is
    stripped from the forwarded head so the downstream parser can't pick the
    other one. TE is a comma-separated coding list; the body is chunked-framed
    only when chunked is the FINAL coding, otherwise it is close-delimited.
    Raises RuntimeError on a malformed Content-Length (a response the client
    must never see as-is — callers turn it into a clean 502).
    """
    transfer_encoding = None
    content_length_value = None
    for name, value in headers:
        lowered = name.lower()
        if lowered == b"transfer-encoding":
            transfer_encoding = value.lower()
        elif lowered == b"content-length":
            content_length_value = value
    if transfer_encoding is not None:
        forward_headers = [(name, value) for name, value in headers if name.lower() != b"content-length"]
        encodings = [token.strip() for token in transfer_encoding.split(b",")]
        kind = "chunked" if encodings and encodings[-1] == b"chunked" else "eof"
        return _BodyFraming(kind=kind, content_length=None), forward_headers
    if content_length_value is not None:
        if not _CONTENT_LENGTH_RE.fullmatch(content_length_value):
            raise RuntimeError(f"invalid upstream Content-Length: {content_length_value!r}")
        return _BodyFraming(kind="content_length", content_length=int(content_length_value)), headers
    return _BodyFraming(kind="eof", content_length=None), headers


def _render_response_head(status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
    """Render the response status line + headers verbatim for the client."""
    head = b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
    for name, value in headers:
        head += name + b": " + value + b"\r\n"
    return head + b"\r\n"


async def _relay_response_body(
    upstream_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    framing: _BodyFraming,
) -> bool:
    """Relay a response body to the client chunk by chunk, flushing per chunk.

    The head already told the client how the body is delimited (framing was
    parsed and validated by `_parse_response_framing` before the head was
    committed). Per-chunk flushing keeps SSE deltas live and holds at most
    one relay chunk in memory regardless of body size. Returns whether the
    client connection may be reused: False whenever the body didn't terminate
    cleanly (connection-close framing, a short ``Content-Length``, or a
    chunked body without its terminating 0-chunk), since the client's parser
    can't find a clean boundary and would misframe or hang on the next
    response sent over the same socket.
    """
    if framing.kind == "chunked":
        return await _relay_chunked_stream(upstream_reader=upstream_reader, client_writer=client_writer)
    if framing.kind == "content_length":
        remaining = framing.content_length
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
        # Validate the size token as strict hex (RFC 9112 §7.1) before
        # converting: int(x, 16) also accepts forms like `-1`/`+1` that a
        # client parser would reject or, worse, interpret differently.
        size_token = size_line.strip().split(b";")[0].strip()
        if not _CHUNK_SIZE_RE.fullmatch(size_token):
            return False
        size = int(size_token, 16)
        client_writer.write(size_line)
        if size == 0:
            # Forward the trailer section up to its terminating blank line,
            # flushing per line — trailer size is sender-controlled, and an
            # undrained loop would buffer it without backpressure. A bare EOF
            # (b"") before the blank line means the chunked terminator
            # (0-chunk + trailers + CRLF) never completed, so the client
            # can't reframe — relay the partial bytes but report non-reuse.
            while True:
                trailer_line = await upstream_reader.readline()
                if trailer_line == b"":
                    await client_writer.drain()
                    return False
                client_writer.write(trailer_line)
                await client_writer.drain()
                if trailer_line in (b"\r\n", b"\n"):
                    break
            return True
        # Relay the payload in bounded sub-reads: the chunk size is
        # sender-controlled, and reading a whole chunk at once would let one
        # huge chunk re-create the buffered-body memory blowup. A short read
        # (EOF mid-payload) has already forwarded the partial bytes, but the
        # chunk is short of its declared size — the client's decoder can't
        # trust the framing from here on.
        remaining = size
        while remaining > 0:
            payload = await upstream_reader.read(min(65536, remaining))
            if not payload:
                return False
            remaining -= len(payload)
            client_writer.write(payload)
            await client_writer.drain()
        crlf = await upstream_reader.readline()
        client_writer.write(crlf)
        await client_writer.drain()
        if crlf != b"\r\n":
            # The chunk delimiter is missing or malformed (strict CRLF per
            # RFC 9112): whatever follows can't be framed as a size line, so
            # stop relaying and report the connection unusable rather than
            # emit garbage framing a stricter client parser would reject.
            return False


def _http_reason(status: int) -> str:
    return {
        100: "Continue", 101: "Switching Protocols", 103: "Early Hints",
        200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently",
        302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized",
        403: "Forbidden", 404: "Not Found", 409: "Conflict", 410: "Gone",
        429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status, "OK")


async def send_json_error(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    """Write a small JSON error response and drain it."""
    body = json.dumps({"error": {"code": status, "message": message}}).encode()
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    await writer.drain()


async def send_raw(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()


async def pump_both_ways(
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
