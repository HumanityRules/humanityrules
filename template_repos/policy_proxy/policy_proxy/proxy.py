"""Upstream proxying — forward the authenticated request to the app container.

Streams both directions so SSE and chunked responses reach the client byte-by-byte
instead of being buffered until the upstream finishes. WebSocket upgrades use a
parallel pumper (proxy_to_upstream_ws) since httpx is HTTP-only.
"""

import asyncio
import logging

import httpx
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import jwt_verify

logger = logging.getLogger(__name__)


class WebSocketUpstreamUnavailable(Exception):
    """Raised when the upstream app cannot complete a WebSocket handshake."""


# A connection-refused upstream normally means the app container is still
# booting (the deployment goes green as soon as this sidecar is up — see
# healthz in app.py), so browsers get a self-refreshing "starting" page
# instead of a raw 502.
UPSTREAM_STARTING_REFRESH_SECONDS = 3

_AGENT_STARTING_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="__REFRESH_SECONDS__">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Starting your agent</title>
<style>
  :root { color-scheme: light dark; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: system-ui, -apple-system, sans-serif;
    background: #fff; color: #1a1d23;
  }
  @media (prefers-color-scheme: dark) {
    body { background: #0b0d12; color: #e8eaf0; }
  }
  main { text-align: center; padding: 2rem; }
  .spinner {
    width: 28px; height: 28px; margin: 0 auto 1.25rem;
    border: 3px solid color-mix(in srgb, currentColor 20%, transparent);
    border-top-color: currentColor; border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  h1 { font-size: 1.15rem; font-weight: 600; margin: 0 0 0.5rem; }
  p { margin: 0; opacity: 0.65; font-size: 0.95rem; }
</style>
</head>
<body>
<main>
  <div class="spinner"></div>
  <h1>Your agent is starting</h1>
  <p>This usually takes a few seconds. The page refreshes on its own.</p>
</main>
</body>
</html>
""".replace("__REFRESH_SECONDS__", str(UPSTREAM_STARTING_REFRESH_SECONDS))


# Hop-by-hop headers per RFC 7230 section 6.1; stripped in both directions.
# Content-Length is preserved — stripping it forces Starlette to re-frame as
# chunked, which surfaces as ERR_HTTP2_PROTOCOL_ERROR at the browser.
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

_FORBIDDEN_INBOUND_HEADERS = {
    "host",
    "x-auth-user", "x-auth-sub", "x-auth-email", "x-auth-provider",
}

# Headers the websockets library generates itself (or that belong only to the
# inbound HTTP→WS handshake). Stripped before forwarding the upstream
# handshake so we don't end up with two Sec-WebSocket-Key / Host pairs.
# Cookie is dropped because the session JWT is for the policy proxy, not the
# upstream app — apps that need user identity read X-Auth-* instead.
_WS_HANDSHAKE_HEADERS = {
    "host",
    "connection", "upgrade",
    "sec-websocket-key", "sec-websocket-version",
    "sec-websocket-extensions", "sec-websocket-protocol",
    "cookie",
}


def _strip_session_cookie(cookie_header: str) -> str:
    """Remove the humr_session pair from a Cookie header, keeping app-owned cookies."""
    kept: list[str] = []
    for pair in cookie_header.split(";"):
        pair = pair.strip()
        if not pair or pair.split("=", 1)[0].strip() == jwt_verify.SESSION_COOKIE_NAME:
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
        # The session JWT is for the policy proxy, not the upstream app — apps
        # that need user identity read X-Auth-* instead.
        if lower == "cookie":
            if (stripped := _strip_session_cookie(cookie_header=value)):
                cookie_parts.append(stripped)
            continue
        out[name] = value
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


def is_fetch_request(request: Request) -> bool:
    """Return true for fetch/XHR-style requests, false for browser navigations."""
    sec_fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
    if sec_fetch_mode:
        return sec_fetch_mode != "navigate"
    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept or "text/event-stream" in accept


def _upstream_starting_response(request: Request) -> Response:
    headers = {
        "retry-after": str(UPSTREAM_STARTING_REFRESH_SECONDS),
        "cache-control": "no-store",
    }
    if is_fetch_request(request=request):
        return PlainTextResponse(content="agent is starting", status_code=503, headers=headers)
    return HTMLResponse(content=_AGENT_STARTING_PAGE_HTML, status_code=503, headers=headers)


async def proxy_to_upstream(
    request: Request,
    identity: jwt_verify.SessionIdentity | None,
    upstream_base: str,
    http_client: httpx.AsyncClient,
    max_body_bytes: int | None,
):
    """Forward the request to *upstream_base* and stream the reply back.

    identity=None is an anonymous public-webapp request: no X-Auth-* headers
    are added, and _filter_request_headers has already stripped any inbound
    ones — the upstream app detects visitors by their absence.

    max_body_bytes caps the request body (public traffic only). Bodies without
    a Content-Length are rejected outright: uvicorn enforces the declared
    length, so the header check is airtight, while chunked uploads would need
    mid-stream abortion to cap.
    """
    if max_body_bytes is not None:
        if "transfer-encoding" in request.headers:
            return PlainTextResponse(content="content-length required", status_code=411)
        content_length = int(request.headers.get("content-length", "0") or "0")
        if content_length > max_body_bytes:
            return PlainTextResponse(content="request body too large", status_code=413)

    url = f"{upstream_base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = _filter_request_headers(request.headers)
    if identity is not None:
        headers["X-Auth-User"] = identity.username
        headers["X-Auth-Sub"] = identity.sub
        headers["X-Auth-Provider"] = identity.provider
        headers["X-Auth-Email"] = identity.email
    original_host = request.headers.get("host", "")
    if original_host:
        headers["X-Forwarded-Host"] = original_host

    # Only forward a body when the inbound request has one. Passing any
    # iterator makes httpx add Transfer-Encoding: chunked, which HTTP/1.0
    # upstreams (like Hermes) can't parse and will abort mid-response.
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
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        logger.error("proxy upstream not accepting connections url=%s err=%s", url, exc)
        return _upstream_starting_response(request=request)
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
        out.append((name, value))
    return out


def _requested_subprotocols(websocket: WebSocket) -> list[str]:
    """Parse the comma-separated Sec-WebSocket-Protocol header from the browser."""
    raw = websocket.headers.get("sec-websocket-protocol", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


async def _connect_to_upstream_ws(
    uri: str, extra_headers: list[tuple[str, str]], subprotocols: list[str] | None,
) -> ClientConnection:
    """Open the upstream WebSocket or raise a policy-proxy-level failure."""
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
    """Forward frames from the browser to the upstream app until either side closes."""
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
    """Forward frames from the upstream app to the browser until either side closes."""
    async for message in upstream:
        if isinstance(message, str):
            await websocket.send_text(message)
        else:
            await websocket.send_bytes(message)


async def proxy_to_upstream_ws(
    websocket: WebSocket,
    identity: jwt_verify.SessionIdentity | None,
    upstream_host: str,
    upstream_port: int,
) -> None:
    """Proxy a WebSocket between the browser and the local upstream app.

    Opens the upstream connection first, then accepts the inbound handshake
    with the selected subprotocol and pumps frames until either side
    disconnects. identity=None is an anonymous public-webapp connection: no
    X-Auth-* headers are added (inbound ones are already stripped).
    """
    path = websocket.url.path
    query = websocket.url.query
    uri = f"ws://{upstream_host}:{upstream_port}{path}"
    if query:
        uri = f"{uri}?{query}"

    extra_headers = _filter_ws_handshake_headers(websocket.headers)
    if identity is not None:
        extra_headers.append(("X-Auth-User", identity.username))
        extra_headers.append(("X-Auth-Sub", identity.sub))
        extra_headers.append(("X-Auth-Provider", identity.provider))
        extra_headers.append(("X-Auth-Email", identity.email))
    if (original_host := websocket.headers.get("host")):
        extra_headers.append(("X-Forwarded-Host", original_host))

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
        # Surface unexpected pump errors in logs but don't propagate — both
        # WebSocketDisconnect and ConnectionClosed are normal-termination
        # signals from one side or the other.
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
