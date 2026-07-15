"""Upstream proxying for local compose — no auth, sets X-Forwarded-Host like policy_proxy."""

import asyncio
import logging
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

logger = logging.getLogger(__name__)


class WebSocketUpstreamUnavailable(Exception):
    """Raised when the upstream app cannot complete a WebSocket handshake."""


_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

_FORBIDDEN_INBOUND_HEADERS = {"host"}
_ORIGIN_LIKE_HEADERS = frozenset({"origin", "referer"})

# Mirrors policy_proxy: on localhost, cookies are shared across ports, so a
# humr_session JWT set by another local service must not reach the upstream app.
SESSION_COOKIE_NAME = "humr_session"


def _host_for_caddy(host: str) -> str:
    """Strip the port from Host so Caddy matches HUMR_PUBLIC_HOSTNAME (e.g. localhost)."""
    if not host:
        return host
    if host.startswith("["):
        bracket_end = host.find("]")
        if bracket_end != -1 and len(host) > bracket_end + 1 and host[bracket_end + 1] == ":":
            return host[: bracket_end + 1]
        return host
    if ":" in host:
        return host.rsplit(":", 1)[0]
    return host


def _strip_port_from_url(value: str) -> str:
    """Drop explicit :port from http(s) URLs so WebUI CSRF matches HUMR_PUBLIC_HOSTNAME."""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port is None:
        return value
    host = parsed.hostname
    netloc = f"[{host}]" if ":" in host else host
    return urlunparse((
        parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment,
    ))


def _normalize_origin_like_header(name: str, value: str) -> str:
    if name.lower() not in _ORIGIN_LIKE_HEADERS:
        return value
    return _strip_port_from_url(value=value)


_WS_HANDSHAKE_HEADERS = {
    "host",
    "connection", "upgrade",
    "sec-websocket-key", "sec-websocket-version",
    "sec-websocket-extensions", "sec-websocket-protocol",
}


def _strip_session_cookie(cookie_header: str) -> str:
    """Remove the humr_session pair from a Cookie header, keeping app-owned cookies."""
    kept: list[str] = []
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if not pair or pair.split("=", 1)[0].strip() == SESSION_COOKIE_NAME:
            continue
        kept.append(pair)
    return "; ".join(kept)


def _filter_request_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    # HTTP/2 clients may split cookies across multiple Cookie fields (RFC 7540
    # section 8.1.2.5); collect them all so the dict doesn't keep only the last.
    cookie_parts: list[str] = []
    for name, value in headers.items():
        lower = name.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in _FORBIDDEN_INBOUND_HEADERS:
            continue
        if lower == "cookie":
            if (stripped := _strip_session_cookie(cookie_header=value)):
                cookie_parts.append(stripped)
            continue
        out[name] = _normalize_origin_like_header(name=name, value=value)
    if cookie_parts:
        out["Cookie"] = "; ".join(cookie_parts)
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in _HOP_BY_HOP_HEADERS:
            continue
        out[name] = value
    return out


def _forwarded_host(request: Request) -> str | None:
    original_host = request.headers.get("host", "")
    if original_host:
        return _host_for_caddy(host=original_host)
    return None


async def proxy_to_upstream(
    request: Request,
    upstream_base: str,
    http_client: httpx.AsyncClient,
):
    """Forward the request to *upstream_base* and stream the reply back."""
    url = f"{upstream_base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = _filter_request_headers(request.headers)
    forwarded_host = _forwarded_host(request=request)
    if forwarded_host is not None:
        headers["X-Forwarded-Host"] = forwarded_host

    has_request_body = (
        "content-length" in request.headers or "transfer-encoding" in request.headers
    )
    build_kwargs: dict[str, object] = {
        "method": request.method,
        "url": url,
        "headers": headers,
    }
    if has_request_body:
        build_kwargs["content"] = request.stream()
    upstream_request = http_client.build_request(**build_kwargs)

    try:
        upstream_response = await http_client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        logger.error("proxy upstream error url=%s err=%s", url, exc)
        return PlainTextResponse(content="upstream unreachable", status_code=502)

    return StreamingResponse(
        content=upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=_filter_response_headers(upstream_response.headers),
        background=BackgroundTask(upstream_response.aclose),
    )


def _filter_ws_handshake_headers(headers) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, value in headers.items():
        lower = name.lower()
        if lower in _WS_HANDSHAKE_HEADERS or lower in _FORBIDDEN_INBOUND_HEADERS:
            continue
        if lower == "cookie":
            if (stripped := _strip_session_cookie(cookie_header=value)):
                out.append((name, stripped))
            continue
        out.append((name, _normalize_origin_like_header(name=name, value=value)))
    return out


def _requested_subprotocols(websocket: WebSocket) -> list[str]:
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


async def _connect_to_upstream_ws(
    uri: str, extra_headers: list[tuple[str, str]], subprotocols: list[str] | None,
) -> ClientConnection:
    try:
        return await ws_connect(
            uri,
            additional_headers=extra_headers,
            subprotocols=subprotocols,
            ping_interval=None,
            open_timeout=10,
            max_size=4 * 1024 * 1024,
        )
    except InvalidStatus as exc:
        logger.error("ws upstream rejected uri=%s status=%s", uri, exc.response.status_code)
        raise WebSocketUpstreamUnavailable from exc
    except (OSError, asyncio.TimeoutError) as exc:
        logger.error("ws upstream unreachable uri=%s err=%s", uri, exc)
        raise WebSocketUpstreamUnavailable from exc


async def _pump_client_to_upstream(
    websocket: WebSocket, upstream: ClientConnection,
) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        if (data := message.get("text")) is not None:
            await upstream.send(data)
        elif (data := message.get("bytes")) is not None:
            await upstream.send(data)


async def _pump_upstream_to_client(
    upstream: ClientConnection, websocket: WebSocket,
) -> None:
    async for message in upstream:
        if isinstance(message, str):
            await websocket.send_text(message)
        else:
            await websocket.send_bytes(message)


async def proxy_to_upstream_ws(
    websocket: WebSocket,
    upstream_host: str,
    upstream_port: int,
) -> None:
    path = websocket.url.path
    query = websocket.url.query
    uri = f"ws://{upstream_host}:{upstream_port}{path}"
    if query:
        uri = f"{uri}?{query}"

    extra_headers = _filter_ws_handshake_headers(websocket.headers)
    if (original_host := websocket.headers.get("host")):
        extra_headers.append(("X-Forwarded-Host", _host_for_caddy(host=original_host)))

    subprotocols = _requested_subprotocols(websocket) or None

    upstream = await _connect_to_upstream_ws(
        uri=uri, extra_headers=extra_headers, subprotocols=subprotocols,
    )

    try:
        await websocket.accept(subprotocol=upstream.subprotocol)

        pump_in = asyncio.create_task(_pump_client_to_upstream(websocket, upstream))
        pump_out = asyncio.create_task(_pump_upstream_to_client(upstream, websocket))
        done, pending = await asyncio.wait(
            {pump_in, pump_out}, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except (WebSocketDisconnect, ConnectionClosed, asyncio.CancelledError):
                pass
            except Exception:
                logger.exception("ws pump error uri=%s", uri)
        for task in pending:
            try:
                await task
            except (WebSocketDisconnect, ConnectionClosed, asyncio.CancelledError):
                pass
            except Exception:
                logger.exception("ws pump cancel error uri=%s", uri)
    finally:
        await upstream.close()
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
