"""FastAPI application for the sidecar proxy."""

import logging
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from . import config as config_mod
from . import jwks as jwks_mod
from . import jwt_verify
from . import pdp as pdp_mod
from . import proxy as proxy_mod

logger = logging.getLogger(__name__)

JWKS_CACHE_TTL_SECONDS = 15 * 60
INTERNAL_PATH_PREFIX = "/__sidecar"


def _extract_original_url(request: Request, cfg: config_mod.SidecarConfig) -> str:
    """Reconstruct the URL the client was trying to reach, for the auth 'rd' param."""
    host = request.headers.get("host", cfg.env_domain)
    path = request.url.path
    query = request.url.query
    full = f"https://{host}{path}"
    if query:
        full = f"{full}?{query}"
    return full


def _redirect_to_auth(request: Request, cfg: config_mod.SidecarConfig) -> Response:
    original = _extract_original_url(request=request, cfg=cfg)
    target = f"{cfg.auth_base_url}/start?rd={quote(original, safe='')}"
    return RedirectResponse(url=target, status_code=302)


def create_app(cfg: config_mod.SidecarConfig) -> FastAPI:
    """Build the FastAPI app. Broken out for testability — tests call this directly."""
    app = FastAPI()
    upstream_base = f"http://{cfg.upstream_host}:{cfg.upstream_port}"

    jwks_cache = jwks_mod.JwksCache.empty(
        jwks_url=cfg.jwks_url, ttl_seconds=JWKS_CACHE_TTL_SECONDS,
    )
    http_client = httpx.AsyncClient()

    app.state.config = cfg
    app.state.jwks_cache = jwks_cache
    app.state.http_client = http_client
    app.state.upstream_base = upstream_base

    @app.on_event("shutdown")
    async def _close_http_client() -> None:
        await http_client.aclose()

    @app.get(f"{INTERNAL_PATH_PREFIX}/healthz")
    async def healthz() -> Response:
        return PlainTextResponse(content="ok")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def catch_all(request: Request, path: str) -> Response:
        if request.url.path.startswith(INTERNAL_PATH_PREFIX):
            return PlainTextResponse(content="not found", status_code=404)

        cookie_value = request.cookies.get(jwt_verify.SESSION_COOKIE_NAME)
        if not cookie_value:
            return _redirect_to_auth(request=request, cfg=cfg)

        identity = await jwt_verify.verify_session_cookie(
            jwt_value=cookie_value,
            jwks_cache=jwks_cache,
            http_client=http_client,
        )
        if identity is None:
            return _redirect_to_auth(request=request, cfg=cfg)

        decision = await pdp_mod.evaluate(
            http_client=http_client,
            pdp_url=cfg.pdp_url,
            sidecar_token=cfg.sidecar_token,
            app_id=cfg.app_id,
            oidc_sub=identity.oidc_sub,
            username=identity.username,
            path=request.url.path,
        )
        if decision is None:
            return PlainTextResponse(
                content="authorization service unavailable",
                status_code=503,
            )
        if decision.decision != "allow":
            logger.info(
                "sidecar deny reason=%s env=%s app=%s user=%s path=%s",
                decision.reason, cfg.env_slug, cfg.app_id, identity.username, request.url.path,
            )
            return PlainTextResponse(
                content="you do not have access to this application",
                status_code=403,
            )

        return await proxy_mod.proxy_to_upstream(
            request=request,
            identity=identity,
            upstream_base=upstream_base,
            http_client=http_client,
        )

    return app


def validate_rd(rd_url: str, env_domain: str) -> bool:
    """Check that a redirect-destination URL stays within the env's parent domain."""
    parsed = urlparse(rd_url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    parent = env_domain.lower()
    return host == parent or host.endswith("." + parent)
