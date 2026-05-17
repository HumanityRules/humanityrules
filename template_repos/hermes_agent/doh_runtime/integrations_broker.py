"""Outside-the-sandbox broker for per-user third-party integrations.

Runs as a supervisor-managed sidecar process. It starts:

1. The TLS-intercept proxy on 127.0.0.1:9950. The nono sandbox gets
   HTTPS_PROXY pointed here and SSL_CERT_FILE pointed at the CA bundle created
   by `tls_intercept.TlsInterceptRuntime`.

2. The integrations control API on 127.0.0.1:9951, reached same-origin by the
   WebUI extension via the /__doh_broker/* reverse-proxy patch. This API owns
   the unified browser-facing integration status surface and mounts the
   MCP-aggregator management routes under /integrations.

The aggregator's port 9952 is sandbox-only MCP traffic. Refresh tokens, DOH's
OAuth client secrets, and the env bearer never enter the sandbox.

Environment contract (set by deploy_app.py's env-bearer overlay):
- DOH_ENV_BEARER        — bearer for DOH's per-env integration endpoints.
- DOH_OWNER_USERNAME    — whose grants this container is for.
- DOH_CONTROL_PLANE_URL — base URL for DOH (e.g. https://devopshero.ai).
- DOH_ENV_SLUG          — env slug, for logging only.

Required file system:
- BROKER_CA_DIR must be writable by the broker user. The CA bundle is written
  here on startup for SSL_CERT_FILE to pick up.
- BROKER_PRIVATE_DIR must be writable by the broker user and unreadable by the
  sandbox.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import mcp_aggregator
import tls_intercept


DEFAULT_PROXY_PORT = 9950
DEFAULT_CONTROL_PORT = 9951
DEFAULT_MCP_PORT = 9952
DEFAULT_CA_DIR = Path("/run/doh/integrations-broker/ca")
DEFAULT_PRIVATE_DIR = Path("/run/doh/integrations-broker/private")
DEFAULT_MCP_PERSISTENT_DIR = Path("/hermes-persistent-root/mcp-aggregator")

logger = logging.getLogger("integrations_broker")


async def _handle_unified_status(
    request: Request,
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    control_plane_url: str,
    owner_username: str,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers and MCP-aggregator items."""
    items = await tls_runtime.status_items(force_refresh=True)
    items.extend(await aggregator.status_items(request=request))
    return JSONResponse(content={
        "doh_control_plane_url": control_plane_url,
        "env_slug": env_slug,
        "owner_username": owner_username,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def _build_control_app(
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    control_plane_url: str,
    owner_username: str,
    env_slug: str,
) -> Starlette:
    """Wire the unified /__doh_broker/* router for browser-facing integration management."""
    async def status_route(request: Request) -> Response:
        return await _handle_unified_status(
            request=request,
            aggregator=aggregator,
            tls_runtime=tls_runtime,
            control_plane_url=control_plane_url,
            owner_username=owner_username,
            env_slug=env_slug,
        )

    async def refresh_catalog_route(request: Request) -> Response:
        ok, payload = await aggregator.refresh_catalog()
        return JSONResponse(content=payload, status_code=200 if ok else 429)

    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/refresh_catalog", endpoint=refresh_catalog_route, methods=["POST"]),
        *aggregator.routes(prefix="/integrations"),
    ]
    return Starlette(routes=routes)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


async def _run(proxy_port: int, control_port: int, mcp_port: int, ca_dir: Path, private_dir: Path, mcp_persistent_dir: Path) -> None:
    control_plane_url = _require_env(name="DOH_CONTROL_PLANE_URL")
    bearer = _require_env(name="DOH_ENV_BEARER")
    owner_username = _require_env(name="DOH_OWNER_USERNAME")
    app_slug = _require_env(name="DOH_APP_SLUG")
    env_slug = os.environ.get("DOH_ENV_SLUG", "")
    logger.info(
        "starting integrations_broker for owner=%s env=%s against %s (proxy=%d, control=%d, mcp=%d)",
        owner_username, env_slug, control_plane_url, proxy_port, control_port, mcp_port,
    )

    tls_runtime = tls_intercept.TlsInterceptRuntime(
        providers=tls_intercept.TLS_INTERCEPT_PROVIDERS,
        refresh_config=tls_intercept.DohRefreshConfig(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
        ),
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
    )

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, lambda s=sig: (logger.info("signal %d; shutting down", s), stop.done() or stop.set_result(None)))

    tls_proxy_server = await tls_runtime.start_proxy_server(host="127.0.0.1", port=proxy_port)
    logger.info("proxy listening on 127.0.0.1:%d", proxy_port)

    public_base_url = os.environ.get("DOH_APP_PUBLIC_URL")
    mcp_persistent_dir.mkdir(parents=True, exist_ok=True)
    aggregator = mcp_aggregator.MCPAggregator(
        port=mcp_port,
        persistent_dir=mcp_persistent_dir,
        public_base_url=public_base_url,
        doh_control_plane_url=control_plane_url,
        doh_env_bearer=bearer,
        doh_app_slug=app_slug,
        doh_owner_username=owner_username,
    )

    control_app = _build_control_app(
        aggregator=aggregator,
        tls_runtime=tls_runtime,
        control_plane_url=control_plane_url,
        owner_username=owner_username,
        env_slug=env_slug,
    )
    control_uvicorn_config = uvicorn.Config(
        app=control_app, host="127.0.0.1", port=control_port, log_level="warning", access_log=False,
    )
    control_server = uvicorn.Server(config=control_uvicorn_config)
    control_server.install_signal_handlers = lambda: None
    logger.info("control API listening on 127.0.0.1:%d", control_port)

    async with tls_proxy_server:
        proxy_task = asyncio.create_task(tls_proxy_server.serve_forever())
        control_task = asyncio.create_task(control_server.serve())
        mcp_task = asyncio.create_task(aggregator.serve())
        done, pending = await asyncio.wait(
            {stop, proxy_task, control_task, mcp_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            exc = task.exception() if task.done() and not task.cancelled() else None
            if exc:
                logger.error("task exited: %r", exc)
                raise exc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [integrations_broker] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT)
    parser.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    parser.add_argument("--private-dir", type=Path, default=DEFAULT_PRIVATE_DIR)
    parser.add_argument("--mcp-persistent-dir", type=Path, default=DEFAULT_MCP_PERSISTENT_DIR)
    args = parser.parse_args()
    try:
        asyncio.run(
            _run(
                proxy_port=args.proxy_port,
                control_port=args.control_port,
                mcp_port=args.mcp_port,
                ca_dir=args.ca_dir,
                private_dir=args.private_dir,
                mcp_persistent_dir=args.mcp_persistent_dir,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
