"""FastAPI application for the policy proxy.

Two roles share this binary:

- proxy (default): the per-app sidecar. `create_app(cfg)` builds the sidecar
  FastAPI app with the PDP + upstream proxy catch-all.
- auth: the singleton auth service. `create_auth_app(cfg, secrets_client)`
  builds a FastAPI app with only the OAuth/JWKS routes.

`main.py` dispatches on DOH_ROLE at startup. Keeping the two factories in one
module lets both roles share the `/__policy_proxy/healthz` endpoint and the
common FastAPI scaffolding without pulling the auth code into sidecar images.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import jwt
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from . import auth as auth_mod
from . import config as config_mod
from . import jwt_verify
from . import pdp as pdp_mod
from . import pdp_cache as pdp_cache_mod
from . import proxy as proxy_mod

logger = logging.getLogger(__name__)

JWKS_CACHE_TTL_SECONDS = 15 * 60
INTERNAL_PATH_PREFIX = "/__policy_proxy"
AUTH_URL_HEADER = "X-DOH-Auth-URL"

# WebSocket close codes used when we reject an upgrade.
WS_CLOSE_AUTH_REQUIRED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_SERVICE_UNAVAILABLE = 1011


@dataclass(frozen=True)
class _AuthDecision:
    """Result of running cookie -> JWT -> PDP for one request.

    Exactly one of `identity` or `reject` is set. `reject` is a string tag the
    HTTP and WS paths translate into their own protocol-appropriate response
    (302/401/403/503 vs ws close codes).
    """
    identity: jwt_verify.SessionIdentity | None
    reject: str | None  # one of: "auth", "deny", "pdp-down"


async def _authorize_session(
    *,
    cookie_value: str | None,
    path: str,
    state: Any,
) -> _AuthDecision:
    """Verify the session cookie and run PDP. Same logic for HTTP and WS paths."""
    if not cookie_value:
        return _AuthDecision(identity=None, reject="auth")

    identity = jwt_verify.verify_session_cookie(
        jwt_value=cookie_value, jwks_client=state.jwks_client,
    )
    if identity is None:
        return _AuthDecision(identity=None, reject="auth")

    decision = state.pdp_cache.get(identity.sub)
    if decision is None:
        decision = await pdp_mod.evaluate(
            http_client=state.http_client,
            pdp_url=state.config.pdp_url,
            env_bearer_token=state.config.env_bearer_token,
            app_id=state.config.app_id,
            sub=identity.sub,
            username=identity.username,
            provider=identity.provider,
            path=path,
        )
        if decision is None:
            return _AuthDecision(identity=identity, reject="pdp-down")
        state.pdp_cache.put(identity.sub, decision)

    if decision.decision != "allow":
        logger.info(
            "policy-proxy deny reason=%s env=%s app=%s user=%s path=%s",
            decision.reason, state.config.env_slug, state.config.app_id,
            identity.username, path,
        )
        return _AuthDecision(identity=identity, reject="deny")

    return _AuthDecision(identity=identity, reject=None)


async def _close_ws_after_accept(websocket: WebSocket, code: int) -> None:
    """Send a browser-visible close code for rejected WebSocket upgrades."""
    await websocket.accept()
    await websocket.close(code=code)


def _extract_original_url(request: Request, cfg: config_mod.PolicyProxyConfig) -> str:
    """Reconstruct the URL the client was trying to reach, for the auth 'rd' param."""
    host = request.headers.get("host", cfg.env_domain)
    path = request.url.path
    query = request.url.query
    full = f"https://{host}{path}"
    if query:
        full = f"{full}?{query}"
    return full


def _is_valid_return_url(url: str, cfg: config_mod.PolicyProxyConfig) -> bool:
    """Return whether a post-auth redirect stays inside the environment domain."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    parent = cfg.env_domain.lower()
    return host == parent or host.endswith("." + parent)


def _extract_reauth_return_url(request: Request, cfg: config_mod.PolicyProxyConfig) -> str:
    """Use the browser page as the post-login return URL when an API call expires."""
    referer = request.headers.get("referer", "")
    if referer and _is_valid_return_url(url=referer, cfg=cfg):
        return referer
    return _extract_original_url(request=request, cfg=cfg)


def _auth_start_url(return_url: str, cfg: config_mod.PolicyProxyConfig) -> str:
    """Build the auth Lambda /start URL for the target return URL."""
    return f"{cfg.auth_base_url}/start?rd={quote(return_url, safe='')}"


def _is_fetch_request(request: Request) -> bool:
    """Return true for requests that should receive 401 instead of a 302."""
    sec_fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
    if sec_fetch_mode:
        return sec_fetch_mode != "navigate"
    if request.headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept or "text/event-stream" in accept


def _auth_required_response(request: Request, cfg: config_mod.PolicyProxyConfig) -> Response:
    """Redirect navigations, but make API/fetch callers handle reauth explicitly."""
    return_url = _extract_reauth_return_url(request=request, cfg=cfg)
    target = _auth_start_url(return_url=return_url, cfg=cfg)
    if _is_fetch_request(request=request):
        return PlainTextResponse(
            content="authentication required",
            status_code=401,
            headers={
                AUTH_URL_HEADER: target,
                "cache-control": "no-store",
            },
        )
    return RedirectResponse(url=target, status_code=302)


def create_app(cfg: config_mod.PolicyProxyConfig) -> FastAPI:
    """Build the FastAPI app. Dependencies live on app.state so tests can swap them."""


    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.config = cfg
    app.state.upstream_base = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
    # read=None so long idle gaps in streaming responses (SSE, LLM output,
    # permission prompts) don't kill the connection mid-stream. connect/write/
    # pool stay finite so unreachable upstreams and pool saturation fail fast.
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
    )
    app.state.jwks_client = jwt.PyJWKClient(
        uri=cfg.jwks_url, cache_keys=True, lifespan=JWKS_CACHE_TTL_SECONDS,
    )
    app.state.pdp_cache = pdp_cache_mod.PdpDecisionCache(
        ttl_seconds=cfg.pdp_cache_ttl_seconds,
    )

    @app.get(f"{INTERNAL_PATH_PREFIX}/healthz")
    async def healthz() -> Response:
        return PlainTextResponse(content="ok")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def catch_all(request: Request, path: str) -> Response:
        if request.url.path.startswith(INTERNAL_PATH_PREFIX):
            return PlainTextResponse(content="not found", status_code=404)

        state = request.app.state
        result = await _authorize_session(
            cookie_value=request.cookies.get(jwt_verify.SESSION_COOKIE_NAME),
            path=request.url.path,
            state=state,
        )

        if result.reject == "auth":
            return _auth_required_response(request=request, cfg=state.config)
        if result.reject == "pdp-down":
            return PlainTextResponse(
                content="authorization service unavailable", status_code=503,
            )
        if result.reject == "deny":
            return PlainTextResponse(
                content="you do not have access to this application", status_code=403,
            )

        assert result.identity is not None
        return await proxy_mod.proxy_to_upstream(
            request=request,
            identity=result.identity,
            upstream_base=state.upstream_base,
            http_client=state.http_client,
        )

    @app.websocket("/{path:path}")
    async def catch_all_ws(websocket: WebSocket, path: str) -> None:
        state = websocket.app.state

        if websocket.url.path.startswith(INTERNAL_PATH_PREFIX):
            await _close_ws_after_accept(websocket=websocket, code=WS_CLOSE_FORBIDDEN)
            return

        result = await _authorize_session(
            cookie_value=websocket.cookies.get(jwt_verify.SESSION_COOKIE_NAME),
            path=websocket.url.path,
            state=state,
        )

        if result.reject == "auth":
            await _close_ws_after_accept(websocket=websocket, code=WS_CLOSE_AUTH_REQUIRED)
            return
        if result.reject == "pdp-down":
            await _close_ws_after_accept(websocket=websocket, code=WS_CLOSE_SERVICE_UNAVAILABLE)
            return
        if result.reject == "deny":
            await _close_ws_after_accept(websocket=websocket, code=WS_CLOSE_FORBIDDEN)
            return

        assert result.identity is not None
        try:
            await proxy_mod.proxy_to_upstream_ws(
                websocket=websocket,
                identity=result.identity,
                upstream_host=state.config.upstream_host,
                upstream_port=state.config.upstream_port,
            )
        except proxy_mod.WebSocketUpstreamUnavailable:
            await _close_ws_after_accept(
                websocket=websocket, code=WS_CLOSE_SERVICE_UNAVAILABLE,
            )

    return app


def create_auth_app(cfg: config_mod.AuthServiceConfig, secrets_client: Any) -> FastAPI:
    """Build the FastAPI app for the auth-service role (no proxy/PDP paths)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.config = cfg

    @app.get(f"{INTERNAL_PATH_PREFIX}/healthz")
    async def healthz() -> Response:
        return PlainTextResponse(content="ok")

    auth_mod.install_auth_routes(app=app, cfg=cfg, secrets_client=secrets_client)
    return app
