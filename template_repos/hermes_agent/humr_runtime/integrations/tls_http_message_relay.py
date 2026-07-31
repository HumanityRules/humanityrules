"""Provider-agnostic HTTP/1.1: read a request from the sandbox, send it to a
destination host, and stream the response back.

This module deliberately knows nothing about providers, credentials, tokens,
or the catalog. By the time code here runs, the TLS-intercept layer has
already decided *which* host to talk to and *what* the outbound request
bytes should be. What remains is mechanical: parse HTTP, frame bodies
correctly, open a TLS connection to the destination, replay the request,
and copy the response home without distorting it.

That ignorance is load-bearing. Keeping credentials and provider policy out
of this file is what keeps the HTTP machinery tractable — and what lets
billing (`tls_usage_metering`) watch a response through a narrow observer
seam without the relay knowing why anyone is watching.

The main shapes:

- `SandboxRequest` / `ProviderRequest` — the same HTTP message on either
  side of the credential rewrite. The intercept layer reads one, possibly
  rewrites headers and path, and hands the other to `forward_to_provider`.
- `forward_to_provider` — connect to the real upstream, write the request,
  stream the response to the sandbox writer, and return a small
  `ForwardResult` (status code, whether the sandbox connection can keep
  serving more requests).
- `pump_both_ways` — the opaque-tunnel path for hosts we do not intercept:
  copy bytes in both directions and stop when either side closes.
- Synthetic replies (`send_raw`, `send_json_error`) — used when the proxy
  itself must answer the sandbox (bad request, provider not connected,
  upstream connect failed) without ever talking to a real provider.

Two framing policies sit next to each other on purpose, because they are
asymmetric and easy to conflate:

- **Request bodies** are buffered in full. The broker validates framing,
  then re-sends to the provider with `Content-Length`. Chunked-alone or
  Content-Length are accepted; anything else is a 400.
- **Response bodies** are never buffered whole. Bytes are flushed per
  chunk in whatever framing the upstream used. Buffering whole bodies made
  broker memory track the largest response ever proxied (git clone packs
  through GitHub reached multi-GB peaks). Per-chunk flushing is also what
  keeps server-sent-event deltas live for the sandbox.

A caller may pass a `ResponseBodyObserver` to watch one response as decoded
bytes (transfer framing already removed). Observation is strictly one-way:
every callback is guarded, an observer that raises is dropped for the rest
of that response, and the bytes the sandbox receives are identical whether
or not anyone is watching.
"""

import asyncio
import contextlib
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Literal, Protocol


logger = logging.getLogger("tls_http_message_relay")


_CONTENT_LENGTH_RE = re.compile(rb"^\d+$")
_CHUNK_SIZE_RE = re.compile(rb"^[0-9A-Fa-f]+$")


@dataclass(frozen=True)
class SandboxRequest:
    """An HTTP request read from the sandbox connection."""

    method: str
    path_with_query: str
    headers: list[tuple[bytes, bytes]]
    body: bytes


@dataclass(frozen=True)
class ProviderRequest:
    """The HTTP request the broker will send to the provider."""

    method: str
    path_with_query: str
    headers: list[tuple[bytes, bytes]]
    body: bytes


@dataclass(frozen=True)
class ForwardResult:
    """Facts the intercept loop needs after forwarding one request."""

    status_code: int
    sandbox_connection_can_continue: bool


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


def _header_values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[bytes]:
    """Return every value for one header name, preserving field order."""
    normalized_name = name.lower()
    return [value.lower() for field_name, value in headers if field_name.lower() == normalized_name]


async def _read_request_body(sandbox_reader: asyncio.StreamReader, headers: list[tuple[bytes, bytes]]) -> bytes:
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
        # stripping Transfer-Encoding before sending would silently change semantics.
        if encodings != [b"chunked"]:
            raise ValueError(f"unsupported request Transfer-Encoding: {b', '.join(encodings)!r}")
        return await _read_chunked_request_body(sandbox_reader=sandbox_reader)

    content_length_values = [
        token.strip()
        for value in _header_values(headers=headers, name=b"content-length")
        for token in value.split(b",")
    ]
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
        return await sandbox_reader.readexactly(remaining)
    except asyncio.IncompleteReadError as exc:
        raise ValueError(f"request body ended after {len(exc.partial)} of {remaining} bytes") from exc


async def _read_chunked_request_body(sandbox_reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    while True:
        size_line = await sandbox_reader.readline()
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
                trailer_line = await sandbox_reader.readline()
                if not trailer_line:
                    raise ValueError("request chunked body ended inside trailers")
                if trailer_line == b"\r\n":
                    return b"".join(chunks)
                if not trailer_line.endswith(b"\r\n"):
                    raise ValueError("request trailer line did not end with CRLF")
        try:
            chunks.append(await sandbox_reader.readexactly(size))
            delimiter = await sandbox_reader.readexactly(2)
        except asyncio.IncompleteReadError as exc:
            raise ValueError("request chunk payload ended before its declared boundary") from exc
        if delimiter != b"\r\n":
            raise ValueError("request chunk payload was not followed by CRLF")


async def read_sandbox_request(sandbox_reader: asyncio.StreamReader) -> SandboxRequest | None:
    """Read one complete request from the sandbox, or None after a clean EOF."""
    request_line = await sandbox_reader.readline()
    if not request_line:
        return None
    try:
        method, path_with_query, _http_version = request_line.decode("iso-8859-1").strip().split(" ", 2)
    except ValueError as exc:
        raise ValueError("bad request line") from exc

    header_lines: list[bytes] = []
    while True:
        line = await sandbox_reader.readline()
        header_lines.append(line)
        if line in (b"\r\n", b"\n", b""):
            break
    headers = _parse_headers(lines=header_lines)
    body = await _read_request_body(sandbox_reader=sandbox_reader, headers=headers)
    return SandboxRequest(method=method, path_with_query=path_with_query, headers=headers, body=body)


def _normalize_provider_headers(headers: list[tuple[bytes, bytes]], body_length: int, provider_host: str) -> list[tuple[bytes, bytes]]:
    """Set the provider Host and strip proxy, hop-by-hop, and stale framing headers."""
    headers_to_strip = frozenset({
        b"connection",
        b"content-length",
        b"host",
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
    normalized.append((b"Host", provider_host.encode()))
    if body_length > 0 or body_was_framed:
        normalized.append((b"Content-Length", str(body_length).encode()))
    return normalized


_provider_ssl_context: ssl.SSLContext | None = None


def _get_provider_ssl_context() -> ssl.SSLContext:
    """Return the shared provider SSLContext, creating it on first use.

    A context is safe to share across connections, and creating one per
    request re-parses the entire system CA store — measurable allocator churn
    under concurrent proxy traffic.
    """
    global _provider_ssl_context
    if _provider_ssl_context is None:
        _provider_ssl_context = ssl.create_default_context()
    return _provider_ssl_context


class ResponseBodyObserver(Protocol):
    """Read-only sink for one provider response, fed decoded body bytes.

    `on_head` receives the final (non-interim) status and the provider's own
    header list; `on_body` receives payload bytes with transfer framing
    (chunk sizes, delimiters, trailers) already removed; `on_end` fires once
    the exchange is over, cleanly or not. Content codings are NOT undone —
    an observer that needs a readable body must arrange for an uncompressed
    response itself (e.g. by stripping the request's Accept-Encoding).
    """

    def on_head(self, status: int, headers: list[tuple[bytes, bytes]]) -> None: ...

    def on_body(self, data: bytes) -> None: ...

    def on_end(self) -> None: ...


class _GuardedObserver:
    """Wraps a ResponseBodyObserver so no exception can reach the relay.

    The first raise disables the observer for the rest of the response —
    observation is best-effort; the byte relay never is.
    """

    def __init__(self, observer: ResponseBodyObserver) -> None:
        self._observer: ResponseBodyObserver | None = observer

    def _call(self, method_name: str, *args: object) -> None:
        if self._observer is None:
            return
        try:
            getattr(self._observer, method_name)(*args)
        except Exception:
            logger.exception("response body observer raised in %s; disabled for this response", method_name)
            self._observer = None

    def on_head(self, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        self._call("on_head", status, headers)

    def on_body(self, data: bytes) -> None:
        self._call("on_body", data)

    def on_end(self) -> None:
        self._call("on_end")


async def forward_to_provider(
    provider_host: str,
    provider_port: int,
    request: ProviderRequest,
    sandbox_writer: asyncio.StreamWriter,
    response_body_observer: ResponseBodyObserver | None,
) -> ForwardResult:
    """Send one request to the provider and stream its response to the sandbox.

    The response head is forwarded verbatim and the body is relayed chunk by
    chunk in the provider's framing — never buffered whole. Streaming keeps
    SSE deltas live for the sandbox AND caps broker memory at one response
    chunk per in-flight response; buffering entire bodies made broker RSS
    track the largest response ever proxied (git clone packs through
    github.com reached multi-GB peaks).
    """
    observer = _GuardedObserver(observer=response_body_observer) if response_body_observer is not None else None
    ssl_context = _get_provider_ssl_context()
    provider_reader, provider_writer = await asyncio.open_connection(
        host=provider_host,
        port=provider_port,
        ssl=ssl_context,
        server_hostname=provider_host,
    )
    try:
        provider_headers = _normalize_provider_headers(
            headers=request.headers,
            body_length=len(request.body),
            provider_host=provider_host,
        )
        request_head = request.method.encode() + b" " + request.path_with_query.encode() + b" HTTP/1.1\r\n"
        for name, value in provider_headers:
            request_head += name + b": " + value + b"\r\n"
        request_head += b"\r\n"
        provider_writer.write(request_head)
        if request.body:
            provider_writer.write(request.body)
        await provider_writer.drain()
        # Interim (1xx) responses precede the final one on the same
        # connection: relay each interim head verbatim and keep reading, so
        # an interim 100/103 doesn't desync the stream and the final status
        # (which drives keep-alive and the 401 evict) is the one acted on.
        # (The proxy reads the full request body before forwarding, so a
        # sandbox waiting on 100-continue waits out its expect timeout first —
        # pre-existing behavior.) 101 is the exception: it has no following
        # response — the connection switches protocols. Upgrades aren't
        # supported (the request normalizer strips `Upgrade`/`Connection`),
        # so a stray 101 is final and force-closes.
        while True:
            status, response_headers = await _read_provider_response_head(provider_reader=provider_reader)
            if not (100 <= status < 200) or status == 101:
                break
            sandbox_writer.write(_render_response_head(status=status, headers=response_headers))
            await sandbox_writer.drain()
        sandbox_connection_can_continue = status != 101 and not connection_close_requested(headers=response_headers)
        if observer is not None:
            observer.on_head(status, response_headers)

        # Statuses that cannot carry a body (HEAD, 1xx, 204, 304) stop at
        # the head even when framing headers are present (a 304 echoes
        # the would-be body's Content-Length) — the relay would otherwise
        # wait on body bytes that never come and hang a valid response.
        response_can_have_body = not (request.method.upper() == "HEAD" or 100 <= status < 200 or status in (204, 304))
        if response_can_have_body:
            # Parsing (and validating) the framing BEFORE the head is written
            # keeps invalid-framing failures on the clean-502 path below.
            framing, sandbox_headers = _parse_response_framing(headers=response_headers)
        else:
            framing, sandbox_headers = None, response_headers

        sandbox_writer.write(_render_response_head(status=status, headers=sandbox_headers))
        await sandbox_writer.drain()

        # Past this point the head is committed to the sandbox: an error can
        # no longer be reported as an HTTP response without corrupting the
        # byte stream (the sandbox would read it as body data). On failure,
        # tear the connection down instead — truncation is detectable from
        # the framing; an injected 502 mid-body is silent corruption.
        try:
            if not response_can_have_body:
                return ForwardResult(status_code=status, sandbox_connection_can_continue=sandbox_connection_can_continue)
            body_has_clean_boundary = await _relay_response_body(
                provider_reader=provider_reader,
                sandbox_writer=sandbox_writer,
                framing=framing,
                observer=observer,
            )
            return ForwardResult(
                status_code=status,
                sandbox_connection_can_continue=sandbox_connection_can_continue and body_has_clean_boundary,
            )
        except Exception:
            logger.exception("forward from %s failed after response head was sent", provider_host)
            return ForwardResult(status_code=status, sandbox_connection_can_continue=False)
    finally:
        if observer is not None:
            observer.on_end()
        with contextlib.suppress(Exception):
            provider_writer.close()
            await provider_writer.wait_closed()


async def _read_provider_response_head(provider_reader: asyncio.StreamReader) -> tuple[int, list[tuple[bytes, bytes]]]:
    """Read one response status line and header block from the provider."""
    status_line = await provider_reader.readline()
    try:
        status = int(status_line.split(b" ", 2)[1])
    except (IndexError, ValueError):
        raise RuntimeError(f"bad provider status line: {status_line!r}")
    headers: list[tuple[bytes, bytes]] = []
    while True:
        line = await provider_reader.readline()
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
    """How one provider response body is delimited on the wire."""

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
    Raises RuntimeError on a malformed Content-Length (a response the sandbox
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
        sandbox_headers = [(name, value) for name, value in headers if name.lower() != b"content-length"]
        encodings = [token.strip() for token in transfer_encoding.split(b",")]
        kind = "chunked" if encodings and encodings[-1] == b"chunked" else "eof"
        return _BodyFraming(kind=kind, content_length=None), sandbox_headers
    if content_length_value is not None:
        if not _CONTENT_LENGTH_RE.fullmatch(content_length_value):
            raise RuntimeError(f"invalid provider Content-Length: {content_length_value!r}")
        return _BodyFraming(kind="content_length", content_length=int(content_length_value)), headers
    return _BodyFraming(kind="eof", content_length=None), headers


def _render_response_head(status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
    """Render the response status line and headers for the sandbox."""
    head = b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
    for name, value in headers:
        head += name + b": " + value + b"\r\n"
    return head + b"\r\n"


async def _relay_response_body(
    provider_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    framing: _BodyFraming,
    observer: _GuardedObserver | None,
) -> bool:
    """Stream a provider response body to the sandbox, flushing per chunk.

    The head already told the sandbox how the body is delimited (framing was
    parsed and validated by `_parse_response_framing` before the head was
    committed). Per-chunk flushing keeps SSE deltas live and holds at most
    one relay chunk in memory regardless of body size. Returns whether the
    sandbox connection may be reused: False whenever the body didn't terminate
    cleanly (connection-close framing, a short ``Content-Length``, or a
    chunked body without its terminating 0-chunk), since the sandbox's parser
    can't find a clean boundary and would misframe or hang on the next
    response sent over the same socket.
    """
    if framing.kind == "chunked":
        return await _relay_chunked_stream(provider_reader=provider_reader, sandbox_writer=sandbox_writer, observer=observer)
    if framing.kind == "content_length":
        remaining = framing.content_length
        while remaining > 0:
            chunk = await provider_reader.read(min(65536, remaining))
            if not chunk:
                # Provider EOF before the advertised length — the sandbox is
                # still waiting on the unfulfilled Content-Length, so the
                # socket can't carry another response.
                return False
            remaining -= len(chunk)
            if observer is not None:
                observer.on_body(chunk)
            sandbox_writer.write(chunk)
            await sandbox_writer.drain()
        return True
    # No explicit framing: stream until provider EOF — the sandbox learns the
    # body ended only when we close the connection, so it can't be reused.
    while True:
        chunk = await provider_reader.read(65536)
        if not chunk:
            break
        if observer is not None:
            observer.on_body(chunk)
        sandbox_writer.write(chunk)
        await sandbox_writer.drain()
    return False


async def _relay_chunked_stream(
    provider_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    observer: _GuardedObserver | None,
) -> bool:
    """Stream a chunked provider body to the sandbox, flushing each chunk.

    The chunk framing is forwarded verbatim (size line, payload, trailing CRLF,
    final 0-chunk + trailers) so the sandbox's chunked decoder sees each SSE
    frame the instant it arrives. The observer, by contrast, sees only payload
    bytes — this loop already separates framing from payload, which is what
    lets observers read a chunked stream without their own dechunker. Returns
    True only when the stream closed cleanly with its terminating 0-chunk; a
    premature EOF, malformed size line, or truncated payload returns False so
    the caller tears the sandbox connection down rather than reuse a socket
    the sandbox can't reframe.
    """
    while True:
        size_line = await provider_reader.readline()
        if not size_line:
            return False
        # Validate the size token as strict hex (RFC 9112 §7.1) before
        # converting: int(x, 16) also accepts forms like `-1`/`+1` that a
        # sandbox parser would reject or, worse, interpret differently.
        size_token = size_line.strip().split(b";")[0].strip()
        if not _CHUNK_SIZE_RE.fullmatch(size_token):
            return False
        size = int(size_token, 16)
        sandbox_writer.write(size_line)
        if size == 0:
            # Forward the trailer section up to its terminating blank line,
            # flushing per line — trailer size is sender-controlled, and an
            # undrained loop would buffer it without backpressure. A bare EOF
            # (b"") before the blank line means the chunked terminator
            # (0-chunk + trailers + CRLF) never completed, so the sandbox
            # can't reframe — relay the partial bytes but report non-reuse.
            while True:
                trailer_line = await provider_reader.readline()
                if trailer_line == b"":
                    await sandbox_writer.drain()
                    return False
                sandbox_writer.write(trailer_line)
                await sandbox_writer.drain()
                if trailer_line in (b"\r\n", b"\n"):
                    break
            return True
        # Relay the payload in bounded sub-reads: the chunk size is
        # sender-controlled, and reading a whole chunk at once would let one
        # huge chunk re-create the buffered-body memory blowup. A short read
        # (EOF mid-payload) has already forwarded the partial bytes, but the
        # chunk is short of its declared size — the sandbox's decoder can't
        # trust the framing from here on.
        remaining = size
        while remaining > 0:
            payload = await provider_reader.read(min(65536, remaining))
            if not payload:
                return False
            remaining -= len(payload)
            if observer is not None:
                observer.on_body(payload)
            sandbox_writer.write(payload)
            await sandbox_writer.drain()
        crlf = await provider_reader.readline()
        sandbox_writer.write(crlf)
        await sandbox_writer.drain()
        if crlf != b"\r\n":
            # The chunk delimiter is missing or malformed (strict CRLF per
            # RFC 9112): whatever follows can't be framed as a size line, so
            # stop relaying and report the connection unusable rather than
            # emit garbage framing a stricter sandbox parser would reject.
            return False


def _http_reason(status: int) -> str:
    return {
        100: "Continue", 101: "Switching Protocols", 103: "Early Hints",
        200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently",
        302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized",
        402: "Payment Required", 403: "Forbidden", 404: "Not Found", 409: "Conflict", 410: "Gone",
        429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway",
        503: "Service Unavailable",
    }.get(status, "OK")


async def send_json_response(writer: asyncio.StreamWriter, status: int, payload: dict) -> None:
    """Write a small JSON response of the caller's own shape and drain it."""
    body = json.dumps(payload).encode()
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    await writer.drain()


async def send_json_error(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    """Write a small JSON error response and drain it."""
    await send_json_response(writer=writer, status=status, payload={"error": {"code": status, "message": message}})


async def send_raw(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    writer.write(
        b"HTTP/1.1 " + str(status).encode() + b" " + _http_reason(status=status).encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()


async def pump_both_ways(
    sandbox_reader: asyncio.StreamReader,
    sandbox_writer: asyncio.StreamWriter,
    destination_reader: asyncio.StreamReader,
    destination_writer: asyncio.StreamWriter,
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
    await asyncio.gather(
        _copy(src=sandbox_reader, dst=destination_writer),
        _copy(src=destination_reader, dst=sandbox_writer),
    )
