"""FastAPI app for local compose — forwards all traffic, no SSO/PDP."""

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse, Response

from . import config as config_mod
from . import proxy as proxy_mod

logger = logging.getLogger(__name__)

HEALTH_PATH = "/__local_policy_proxy/healthz"
WS_CLOSE_SERVICE_UNAVAILABLE = 1011


def create_app(cfg: config_mod.LocalPolicyProxyConfig) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.http_client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.config = cfg
    app.state.upstream_base = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
    )

    @app.get(HEALTH_PATH)
    async def healthz() -> Response:
        return PlainTextResponse(content="ok")

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all(request: Request, path: str) -> Response:
        if request.url.path.startswith("/__local_policy_proxy"):
            return PlainTextResponse(content="not found", status_code=404)
        return await proxy_mod.proxy_to_upstream(
            request=request,
            upstream_base=request.app.state.upstream_base,
            http_client=request.app.state.http_client,
        )

    @app.websocket("/{path:path}")
    async def catch_all_ws(websocket: WebSocket, path: str) -> None:
        if websocket.url.path.startswith("/__local_policy_proxy"):
            await websocket.accept()
            await websocket.close(code=1008)
            return
        try:
            await proxy_mod.proxy_to_upstream_ws(
                websocket=websocket,
                upstream_host=websocket.app.state.config.upstream_host,
                upstream_port=websocket.app.state.config.upstream_port,
            )
        except proxy_mod.WebSocketUpstreamUnavailable:
            await websocket.accept()
            await websocket.close(code=WS_CLOSE_SERVICE_UNAVAILABLE)

    return app
