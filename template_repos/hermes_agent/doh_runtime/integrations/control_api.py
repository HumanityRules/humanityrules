"""Browser-facing integrations control API (127.0.0.1:9951).

Starlette app mounted by the integrations broker and reached same-origin from
the WebUI via Caddy's /__doh_broker/* route. Unifies TLS-intercept provider
management, MCP-aggregator OAuth routes, and Merge passthroughs under one URL
space.

Pure transport: every route parses the request, delegates to
`credentials_service` (state changes) or reads cached status from the
runtimes, and serializes the result. No credential choreography lives here,
and no DOH bearer — outbound DOH calls happen inside the service's DohClient.
"""

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from credentials_service import CredentialsService
import device_flow
from doh_client import DohClient
from mcp_aggregator import MCPAggregator
import tls_intercept

logger = logging.getLogger("control_api")


async def _handle_unified_status(
    mcp_aggregator: MCPAggregator,
    tls_intercept_runtime: tls_intercept.TlsInterceptRuntime,
    doh_client: DohClient,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers and MCP-aggregator items.

    Reads cached TLS-intercept entries; refresh happens lazily (proxy hot
    path or near expiry). Callers that need fresh state must POST
    /__doh_broker/integrations/tls_intercept/{provider}/invalidate for one provider
    (e.g. after a Disconnect on DOH) or /__doh_broker/integrations/refresh_all
    for the explicit-Refresh path (MCP catalog reload + all-providers TLS
    invalidate in one shot).
    """
    items = await tls_intercept_runtime.status_items()
    items.extend(await mcp_aggregator.status_items())
    return JSONResponse(content={
        "doh_control_plane_url": doh_client.control_plane_url,
        "env_slug": env_slug,
        "owner_username": doh_client.owner_username,
        "app_slug": doh_client.app_slug,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def build_control_app(
    mcp_aggregator: MCPAggregator,
    tls_intercept_runtime: tls_intercept.TlsInterceptRuntime,
    oauth_device_flow: device_flow.OAuthDeviceFlow,
    credentials_service: CredentialsService,
    doh_client: DohClient,
    env_slug: str,
) -> Starlette:
    """Wire the unified /__doh_broker/* router for browser-facing integration management."""
    async def status_route(request: Request) -> Response:
        return await _handle_unified_status(
            mcp_aggregator=mcp_aggregator,
            tls_intercept_runtime=tls_intercept_runtime,
            doh_client=doh_client,
            env_slug=env_slug,
        )

    async def refresh_all_route(request: Request) -> Response:
        """Explicit-Refresh: MCP catalog reload + all-providers TLS invalidate in one shot."""
        status, payload = await credentials_service.refresh_all_integrations()
        return JSONResponse(content=payload, status_code=status)

    async def credentials_invalidate_route(request: Request) -> Response:
        """Drop one provider's cached TLS-intercept token after known state changes."""
        provider = request.path_params["provider"]
        try:
            await credentials_service.credentials_invalidate(slug=provider)
        except RuntimeError as exc:
            return JSONResponse(
                content={"ok": False, "provider": provider, "error": str(exc)},
                status_code=502,
            )
        return JSONResponse(content={"ok": True, "provider": provider})

    async def credentials_setup_session_route(request: Request) -> Response:
        """Ask DOH for a vault setup-session submit token for this provider."""
        provider = request.path_params["provider"]
        public_origin = request.query_params.get("origin", "")
        status, payload = await credentials_service.credentials_setup_session(
            provider=provider,
            public_origin=public_origin,
        )
        return JSONResponse(content=payload, status_code=status)

    async def credentials_disconnect_route(request: Request) -> Response:
        """Disconnect any TLS-intercept provider (vault or OAuth)."""
        provider = request.path_params["provider"]
        status, payload = await credentials_service.credentials_disconnect(provider=provider)
        return JSONResponse(content=payload, status_code=status)

    async def device_start_route(request: Request) -> Response:
        """Begin a provider device login; returns the user_code to display immediately."""
        provider = request.path_params["provider"]
        try:
            view = await oauth_device_flow.start(provider_slug=provider)
        except device_flow.DeviceFlowError as exc:
            return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse(content={"ok": True, **view})

    async def device_status_route(request: Request) -> Response:
        """Report the in-flight device-login phase (pending/completed/failed)."""
        provider = request.path_params["provider"]
        view = await oauth_device_flow.status(provider_slug=provider)
        if view is None:
            return JSONResponse(content={"ok": True, "phase": None})
        return JSONResponse(content={"ok": True, **view})

    async def device_cancel_route(request: Request) -> Response:
        """Cancel an in-flight provider device login (user closed the dialog)."""
        provider = request.path_params["provider"]
        await oauth_device_flow.cancel(provider_slug=provider)
        return JSONResponse(content={"ok": True})

    tls = "/integrations/tls_intercept/{provider}"
    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/refresh_all", endpoint=refresh_all_route, methods=["POST"]),
        Route(path=f"{tls}/invalidate", endpoint=credentials_invalidate_route, methods=["POST"]),
        Route(path=f"{tls}/setup-session", endpoint=credentials_setup_session_route, methods=["POST"]),
        Route(path=f"{tls}/disconnect", endpoint=credentials_disconnect_route, methods=["POST"]),
        # Device login: broker-run OAuth device flows (no redirect callback).
        Route(path=f"{tls}/device/start", endpoint=device_start_route, methods=["POST"]),
        Route(path=f"{tls}/device/status", endpoint=device_status_route, methods=["GET"]),
        Route(path=f"{tls}/device/cancel", endpoint=device_cancel_route, methods=["POST"]),
        *mcp_aggregator.routes(prefix="/integrations"),
    ]
    return Starlette(routes=routes)
