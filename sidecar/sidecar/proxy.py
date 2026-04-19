"""Upstream proxying — forward the authenticated request to the app container."""

import logging

import httpx
from fastapi import Request, Response

from . import jwt_verify

logger = logging.getLogger(__name__)

# Hop-by-hop headers per RFC 7230 section 6.1; we strip these before forwarding.
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

# Inbound headers we never pass through — either we set them ourselves, or they
# would leak control info from the client's request.
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
) -> Response:
    """Forward the authenticated *request* to *upstream_base* and stream the reply back."""
    url = f"{upstream_base}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = _filter_request_headers(request.headers)
    # Trusted identity headers — the app behind the sidecar reads these and
    # MUST trust them only if its network path is locked to the sidecar.
    headers["X-Auth-User"] = identity.username
    headers["X-Auth-Sub"] = identity.oidc_sub
    headers["X-Auth-Email"] = identity.email
    # Preserve the original hostname for the app.
    original_host = request.headers.get("host", "")
    if original_host:
        headers["X-Forwarded-Host"] = original_host

    body = await request.body()

    try:
        upstream_response = await http_client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.error("proxy upstream error url=%s err=%s", url, exc)
        return Response(status_code=502, content=b"upstream unreachable")

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_filter_response_headers(upstream_response.headers),
    )
