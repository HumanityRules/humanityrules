"""FastAPI application for the per-app policy-proxy sidecar.

The sidecar verifies the session cookie via the central JWKS, runs PDP, and
proxies authorized traffic to the upstream container.

It also exposes ``/__humr_session_install``, the landing path the control plane
redirects browsers to after a successful IdP dance: it verifies the session
JWT minted upstream, sets the ``humr_session`` cookie scoped to the env domain,
and bounces the browser to the original ``rd`` URL.
"""

import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import jwt
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from . import activity_reporter as activity_reporter_mod
from . import config as config_mod
from . import jwt_verify
from . import pdp as pdp_mod
from . import pdp_cache as pdp_cache_mod
from . import proxy as proxy_mod

logger = logging.getLogger(__name__)

JWKS_CACHE_TTL_SECONDS = 15 * 60
INTERNAL_PATH_PREFIX = "/__policy_proxy"
SESSION_INSTALL_PATH = "/__humr_session_install"
AUTH_URL_HEADER = "X-HUMR-Auth-URL"
MIN_COOKIE_TTL_SECONDS = 60
# Must answer inside the ALB health-check timeout (2s). On loopback a
# not-yet-listening upstream refuses instantly, so this only caps pathological cases.
UPSTREAM_PROBE_TIMEOUT_SECONDS = 1.0

# WebSocket close codes used when we reject an upgrade.
WS_CLOSE_AUTH_REQUIRED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_SERVICE_UNAVAILABLE = 1011

# Anonymous public-webapp requests only. Authenticated traffic is uncapped.
PUBLIC_MAX_BODY_BYTES = 10 * 1024 * 1024
PUBLIC_CACHE_MAX_ENTRIES = 512

# User webapp slugs as routed by the agent's Caddy (webapps_lib.SLUG_PATTERN
# minus the `__` internal prefix — internal webapps are path-routed on the
# bare agent host, which is never matched as a user webapp hostname).
_WEBAPP_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}[a-z0-9]$")


def _webapp_slug_for_host(host: str, public_hostname: str | None) -> str | None:
    """Parse '<slug>-<public_hostname>' into the webapp slug, else None.

    Host is attacker-chosen: normalize port/case/trailing-dot before matching
    so variants of one hostname can't become distinct cache keys, and require
    the remainder to be a valid webapp slug.
    """
    if not public_hostname or not host:
        return None
    hostname = host.split(":", 1)[0].rstrip(".").lower()
    suffix = "-" + public_hostname.lower().rstrip(".")
    if not hostname.endswith(suffix):
        return None
    label = hostname[: -len(suffix)]
    if not _WEBAPP_SLUG_RE.match(label):
        return None
    return label


@dataclass(frozen=True)
class _AuthDecision:
    """Result of authorizing one request, same logic for HTTP and WS paths.

    Three shapes: `identity` set (authenticated allow), `public` True with no
    identity (anonymous allow for a publicly-granted webapp hostname), or
    `reject` set — a string tag the HTTP and WS paths translate into their own
    protocol-appropriate response (302/401/403/503 vs ws close codes).
    """
    identity: jwt_verify.SessionIdentity | None
    reject: str | None  # one of: "auth", "deny", "pdp-down"
    public: bool


async def _authorize_session(
    *,
    cookie_value: str | None,
    path: str,
    host: str,
    state: Any,
) -> _AuthDecision:
    """Public-webapp check on the Host, then cookie -> JWT -> PDP.

    A webapp hostname with a live public grant is allowed anonymously — the
    session flow never runs, so org members and visitors see the same thing.
    A webapp hostname without a live public grant falls through to the session flow.
    """
    webapp_slug = _webapp_slug_for_host(host=host, public_hostname=state.config.public_hostname)
    if webapp_slug is not None:
        decision = state.public_cache.get(slug=webapp_slug)
        if decision is None:
            decision = await pdp_mod.evaluate_public(
                http_client=state.http_client,
                pdp_url=state.config.pdp_url,
                app_bearer_token=state.config.app_bearer_token,
                webapp_slug=webapp_slug,
                path=path,
            )
            if decision is None:
                return _AuthDecision(identity=None, reject="pdp-down", public=False)
            state.public_cache.put(slug=webapp_slug, decision=decision)
        if decision.decision == "allow":
            return _AuthDecision(identity=None, reject=None, public=True)

    if not cookie_value:
        return _AuthDecision(identity=None, reject="auth", public=False)

    identity = jwt_verify.verify_session_jwt(
        jwt_value=cookie_value,
        jwks_client=state.jwks_client,
        env_domain=state.config.env_domain,
    )
    if identity is None:
        return _AuthDecision(identity=None, reject="auth", public=False)

    decision = state.pdp_cache.get(provider=identity.provider, sub=identity.sub)
    if decision is None:
        decision = await pdp_mod.evaluate(
            http_client=state.http_client,
            pdp_url=state.config.pdp_url,
            app_bearer_token=state.config.app_bearer_token,
            provider=identity.provider,
            sub=identity.sub,
            username=identity.username,
            path=path,
        )
        if decision is None:
            return _AuthDecision(identity=identity, reject="pdp-down", public=False)
        state.pdp_cache.put(provider=identity.provider, sub=identity.sub, decision=decision)

    if decision.decision != "allow":
        logger.info(
            "policy-proxy deny reason=%s env=%s app=%s user=%s path=%s",
            decision.reason, state.config.env_slug, state.config.app_id,
            identity.username, path,
        )
        return _AuthDecision(identity=identity, reject="deny", public=False)

    return _AuthDecision(identity=identity, reject=None, public=False)


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
    """Build the control-plane env-start URL for the target return URL."""
    return f"{cfg.auth_base_url}/auth/env-start?rd={quote(return_url, safe='')}"


def _session_cookie(*, jwt_value: str, env_domain: str, ttl_seconds: int) -> str:
    return (
        f"{jwt_verify.SESSION_COOKIE_NAME}={jwt_value}; Domain=.{env_domain}; Path=/; "
        f"Max-Age={ttl_seconds}; Secure; HttpOnly; SameSite=Lax"
    )


def _auth_required_response(request: Request, cfg: config_mod.PolicyProxyConfig) -> Response:
    """Redirect navigations, but make API/fetch callers handle reauth explicitly."""
    return_url = _extract_reauth_return_url(request=request, cfg=cfg)
    target = _auth_start_url(return_url=return_url, cfg=cfg)
    if proxy_mod.is_fetch_request(request=request):
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
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.activity_reporter.close()
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
    app.state.public_cache = pdp_cache_mod.PublicWebappDecisionCache(
        ttl_seconds=cfg.public_cache_ttl_seconds,
        max_entries=PUBLIC_CACHE_MAX_ENTRIES,
    )
    app.state.activity_reporter = activity_reporter_mod.PolicyProxyActivityReporter(
        http_client_provider=lambda: app.state.http_client,
        endpoint_url=f"{cfg.control_plane_url}/api/runtime/policy-proxy-activity",
        app_bearer_token=cfg.app_bearer_token,
        interval_seconds=cfg.activity_report_interval_seconds,
    )

    @app.get(f"{INTERNAL_PATH_PREFIX}/healthz")
    async def healthz(request: Request) -> Response:
        # The ALB target-group check points here, and ECS stability / CFN
        # completion / deployment SUCCEEDED all flow from target health — so
        # "healthy" must mean "a click on the app URL reaches the app", not
        # just "this sidecar booted". Any HTTP response counts as ready; only
        # a connection-level failure means the app hasn't bound its port yet.
        state = request.app.state
        try:
            await state.http_client.head(f"{state.upstream_base}/", timeout=UPSTREAM_PROBE_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return PlainTextResponse(content="upstream not ready", status_code=503)
        return PlainTextResponse(content="ok")

    @app.get(SESSION_INSTALL_PATH)
    async def session_install(request: Request) -> Response:
        token = request.query_params.get("token", "")
        rd = request.query_params.get("rd", "")
        if not token or not rd:
            return PlainTextResponse(content="missing token or rd", status_code=400)
        if not _is_valid_return_url(url=rd, cfg=cfg):
            return PlainTextResponse(content="invalid rd", status_code=400)

        identity = jwt_verify.verify_session_jwt(
            jwt_value=token,
            jwks_client=request.app.state.jwks_client,
            env_domain=cfg.env_domain,
        )
        if identity is None:
            return PlainTextResponse(content="invalid session token", status_code=401)

        exp = jwt_verify.session_jwt_exp(jwt_value=token)
        if exp is None:
            return PlainTextResponse(content="malformed session token", status_code=401)
        cookie_ttl = max(MIN_COOKIE_TTL_SECONDS, exp - int(time.time()))

        logger.info(
            "session install env=%s user=%s -> %s", cfg.env_slug, identity.username, rd,
        )
        return Response(
            status_code=302,
            headers={
                "location": rd,
                "set-cookie": _session_cookie(
                    jwt_value=token, env_domain=cfg.env_domain, ttl_seconds=cookie_ttl,
                ),
                "cache-control": "no-store",
                "referrer-policy": "no-referrer",
            },
        )

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def catch_all(request: Request, path: str) -> Response:
        if request.url.path.startswith(INTERNAL_PATH_PREFIX):
            return PlainTextResponse(content="not found", status_code=404)

        state = request.app.state
        result = await _authorize_session(
            cookie_value=request.cookies.get(jwt_verify.SESSION_COOKIE_NAME),
            path=request.url.path,
            host=request.headers.get("host", ""),
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

        state.activity_reporter.observe()
        return await proxy_mod.proxy_to_upstream(
            request=request,
            identity=result.identity,
            upstream_base=state.upstream_base,
            http_client=state.http_client,
            max_body_bytes=PUBLIC_MAX_BODY_BYTES if result.public else None,
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
            host=websocket.headers.get("host", ""),
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

        state.activity_reporter.observe()
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
