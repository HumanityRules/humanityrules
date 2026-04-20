"""Upstream proxying — forward the authenticated request to the app container.

Streams both directions so SSE and chunked responses reach the client byte-by-byte
instead of being buffered until the upstream finishes.
"""

import logging

import httpx
from fastapi import Request
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse, StreamingResponse

from . import jwt_verify

logger = logging.getLogger(__name__)

# Hop-by-hop headers per RFC 7230 section 6.1; stripped in both directions.
# Content-Length is preserved — stripping it forces Starlette to re-frame as
# chunked, which surfaces as ERR_HTTP2_PROTOCOL_ERROR at the browser.
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

_FORBIDDEN_INBOUND_HEADERS = {
    "host",
    "x-auth-user", "x-auth-sub", "x-auth-email",
}


def _filter_request_headers(headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in _HOP_BY_HOP_HEADERS or lower in _FORBIDDEN_INBOUND_HEADERS:
            continue
        out[name] = value
    return out


def _filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in _HOP_BY_HOP_HEADERS:
            continue
        out[name] = value
    return out


async def proxy_to_upstream(
    request: Request,
    identity: jwt_verify.SessionIdentity,
    upstream_base: str,
    http_client: httpx.AsyncClient,
):
    """Forward the request to *upstream_base* and stream the reply back."""
    url = f"{upstream_base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = _filter_request_headers(request.headers)
    headers["X-Auth-User"] = identity.username
    headers["X-Auth-Sub"] = identity.oidc_sub
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
    except httpx.HTTPError as exc:
        logger.error("proxy upstream error url=%s err=%s", url, exc)
        return PlainTextResponse(content="upstream unreachable", status_code=502)

    return StreamingResponse(
        content=upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=_filter_response_headers(upstream_response.headers),
        background=BackgroundTask(upstream_response.aclose),
    )
