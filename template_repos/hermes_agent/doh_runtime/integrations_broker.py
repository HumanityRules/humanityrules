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
- DOH_APP_SLUG          — logical app key for app-scoped credentials.
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
import json
import logging
import os
import signal
import sys
import urllib.error
import urllib.request
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
DEFAULT_GATEWAY_ENV_PATH = Path("/workspace/.hermes/.env")
DEFAULT_PROCESS_COMPOSE_URL = "http://127.0.0.1:9956"
GATEWAY_PROCESS_NAME = "system.gateway"

logger = logging.getLogger("integrations_broker")


async def _handle_unified_status(
    request: Request,
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    control_plane_url: str,
    owner_username: str,
    app_slug: str,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers and MCP-aggregator items.

    Uses cached TLS-intercept entries (refreshed lazily by the proxy hot
    path or near expiry). A previous version force-refreshed every TLS
    provider on every status read, which on GitHub rotated the
    refresh_token and invalidated any in-flight access_token — racing
    concurrent git/gh requests through the proxy. If the caller knows the
    cache is stale (e.g. the user just clicked Disconnect on DOH and
    landed back here), they should POST /__doh_broker/integrations/invalidate
    first.
    """
    items = await tls_runtime.status_items()
    items.extend(await aggregator.status_items())
    return JSONResponse(content={
        "doh_control_plane_url": control_plane_url,
        "env_slug": env_slug,
        "owner_username": owner_username,
        "app_slug": app_slug,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def _build_control_app(
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    control_plane_url: str,
    bearer: str,
    owner_username: str,
    app_slug: str,
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
            app_slug=app_slug,
            env_slug=env_slug,
        )

    async def refresh_catalog_route(request: Request) -> Response:
        ok, payload = await aggregator.refresh_catalog()
        return JSONResponse(content=payload, status_code=200 if ok else 429)

    async def invalidate_tls_cache_route(request: Request) -> Response:
        """Drop every cached TLS-intercept token for explicit Refresh all."""
        try:
            await tls_runtime.invalidate_all()
        except RuntimeError as exc:
            return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse(content={"ok": True})

    async def invalidate_provider_tls_cache_route(request: Request) -> Response:
        """Drop one provider's cached TLS-intercept token after known state changes."""
        provider = request.path_params["provider"]
        try:
            await tls_runtime.invalidate(slug=provider)
        except RuntimeError as exc:
            return JSONResponse(
                content={"ok": False, "provider": provider, "error": str(exc)},
                status_code=502,
            )
        return JSONResponse(content={"ok": True, "provider": provider})

    async def vault_setup_session_route(request: Request) -> Response:
        provider = request.path_params["provider"]
        public_origin = request.query_params.get("origin", "")
        status, payload = await asyncio.to_thread(
            _post_control_plane_json,
            control_plane_url=control_plane_url,
            bearer=bearer,
            path="/api/integrations/credentials/setup-session",
            payload={
                "owner_username": owner_username,
                "app_slug": app_slug,
                "provider": provider,
                "public_origin": public_origin,
            },
        )
        return JSONResponse(content=payload, status_code=status)

    async def vault_disconnect_route(request: Request) -> Response:
        provider = request.path_params["provider"]
        status, payload = await asyncio.to_thread(
            _post_control_plane_json,
            control_plane_url=control_plane_url,
            bearer=bearer,
            path="/api/integrations/credentials/disconnect",
            payload={
                "owner_username": owner_username,
                "app_slug": app_slug,
                "provider": provider,
            },
        )
        if 200 <= status < 300:
            try:
                await tls_runtime.invalidate(slug=provider)
            except RuntimeError as exc:
                return JSONResponse(
                    content={**payload, "ok": False, "error": str(exc)},
                    status_code=502,
                )
        return JSONResponse(content=payload, status_code=status)

    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/refresh_catalog", endpoint=refresh_catalog_route, methods=["POST"]),
        Route(path="/integrations/invalidate_tls_cache", endpoint=invalidate_tls_cache_route, methods=["POST"]),
        Route(path="/integrations/{provider}/invalidate_tls_cache", endpoint=invalidate_provider_tls_cache_route, methods=["POST"]),
        Route(path="/integrations/{provider}/vault/setup-session", endpoint=vault_setup_session_route, methods=["POST"]),
        Route(path="/integrations/{provider}/vault/disconnect", endpoint=vault_disconnect_route, methods=["POST"]),
        *aggregator.routes(prefix="/integrations"),
    ]
    return Starlette(routes=routes)


def _post_control_plane_json(control_plane_url: str, bearer: str, path: str, payload: dict) -> tuple[int, dict]:
    """POST JSON to DOH from the outside-sandbox broker."""
    url = f"{control_plane_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": f"control plane returned HTTP {exc.code}"}
        return exc.code, body
    except Exception as exc:
        logger.error("control plane request failed path=%s: %s", path, exc)
        return 502, {"error": "control plane request failed"}


def _post_process_compose_restart(process_compose_url: str, process_name: str) -> tuple[int, str]:
    """Tell process-compose's REST API to restart the gateway entry."""
    url = f"{process_compose_url.rstrip('/')}/process/restart/{process_name}"
    req = urllib.request.Request(url=url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:
        logger.error("process-compose restart failed for %s: %s", process_name, exc)
        return 502, str(exc)


async def _render_gateway_env_file(
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    env_path: Path,
) -> None:
    """Write the DOH-managed block of the gateway env file from current cache state."""
    snapshot = await tls_runtime.gateway_env_snapshot()
    block = tls_intercept.render_managed_block(snapshot=snapshot)
    await asyncio.to_thread(tls_intercept.write_gateway_env_file, env_path, block)
    logger.info("rewrote gateway env at %s (%d bytes)", env_path, len(block))


async def _bootstrap_gateway_env(
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    env_path: Path,
) -> None:
    """At broker startup: refresh every provider, then render env file once."""
    await tls_runtime.refresh_all()
    await _render_gateway_env_file(tls_runtime=tls_runtime, env_path=env_path)


def _build_on_user_invalidate(
    tls_runtime_holder: dict,
    env_path: Path,
    process_compose_url: str,
):
    """Return the hook that re-renders env and restarts the gateway when needed.

    Uses a holder dict because the runtime is constructed *with* this hook,
    creating a chicken-and-egg. The broker fills `tls_runtime_holder["runtime"]`
    immediately after construction.
    """

    async def on_user_invalidate(slug: str | None) -> None:
        runtime = tls_runtime_holder["runtime"]
        await runtime.refresh_all()
        await _render_gateway_env_file(tls_runtime=runtime, env_path=env_path)
        if not _slug_requires_restart(slug=slug, runtime=runtime):
            return
        status, body = await asyncio.to_thread(
            _post_process_compose_restart,
            process_compose_url,
            GATEWAY_PROCESS_NAME,
        )
        if not (200 <= status < 300):
            logger.error("gateway restart returned %d: %s", status, body)
            raise RuntimeError(
                f"gateway restart failed (process-compose returned {status}); "
                f"please redeploy the app to apply the new credentials"
            )
        logger.info("gateway restart kicked off after invalidate(slug=%s)", slug)

    return on_user_invalidate


def _slug_requires_restart(slug: str | None, runtime: tls_intercept.TlsInterceptRuntime) -> bool:
    """A user-invalidate triggers a gateway restart only when a vault provider was touched.

    `slug=None` (Refresh-all) restarts only if any vault provider is in scope —
    this catches the corner case where Refresh-all reveals state diverged
    silently. Per-provider invalidates restart only for vault providers
    whose `restart_required_after_save` is True.
    """
    providers = runtime._token_store._providers
    if slug is None:
        return any(spec.credential_method.restart_required_after_save for spec in providers.values())
    spec = providers.get(slug)
    if spec is None:
        return False
    return spec.credential_method.restart_required_after_save


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


async def _run(
    proxy_port: int,
    control_port: int,
    mcp_port: int,
    ca_dir: Path,
    private_dir: Path,
    mcp_persistent_dir: Path,
    gateway_env_path: Path,
    process_compose_url: str,
) -> None:
    control_plane_url = _require_env(name="DOH_CONTROL_PLANE_URL")
    bearer = _require_env(name="DOH_ENV_BEARER")
    owner_username = _require_env(name="DOH_OWNER_USERNAME")
    app_slug = _require_env(name="DOH_APP_SLUG")
    env_slug = os.environ.get("DOH_ENV_SLUG", "")
    logger.info(
        "starting integrations_broker for owner=%s env=%s against %s (proxy=%d, control=%d, mcp=%d)",
        owner_username, env_slug, control_plane_url, proxy_port, control_port, mcp_port,
    )

    tls_runtime_holder: dict = {}
    on_user_invalidate = _build_on_user_invalidate(
        tls_runtime_holder=tls_runtime_holder,
        env_path=gateway_env_path,
        process_compose_url=process_compose_url,
    )
    tls_runtime = tls_intercept.TlsInterceptRuntime(
        providers=tls_intercept.TLS_INTERCEPT_PROVIDERS,
        refresh_config=tls_intercept.DohRefreshConfig(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            app_slug=app_slug,
        ),
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
        on_user_invalidate=on_user_invalidate,
    )
    tls_runtime_holder["runtime"] = tls_runtime
    # Render the gateway env file from current DOH state before opening the
    # control port. supervisor.sh's wait_for_port on the control port doubles
    # as the synchronization point: by the time it returns, the file is on
    # disk and webui.sh can launch the gateway with current credentials.
    await _bootstrap_gateway_env(tls_runtime=tls_runtime, env_path=gateway_env_path)

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
        bearer=bearer,
        owner_username=owner_username,
        app_slug=app_slug,
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
    parser.add_argument("--gateway-env-path", type=Path, default=DEFAULT_GATEWAY_ENV_PATH)
    parser.add_argument("--process-compose-url", default=DEFAULT_PROCESS_COMPOSE_URL)
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
                gateway_env_path=args.gateway_env_path,
                process_compose_url=args.process_compose_url,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
