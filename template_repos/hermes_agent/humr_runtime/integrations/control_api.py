"""URL facade the WebUI hits for broker-managed controls.

This module builds the Starlette app that answers under ``/__humr_broker/*``.
It is not the owner of most of that surface: look at the route table at the
bottom of ``build_control_app``. Some handlers are thin one-line delegates
defined here; others are mounted wholesale from sibling modules
(``mcp_aggregator``, ``permissions_control``). The point of the file is one
browser-facing URL namespace — not the credential, OAuth, Merge, or
permissions logic behind it. If you open this file hunting for those, follow
the mount or the delegate instead.

Three things that *do* belong here:

1. Card visibility. ``_card_visible`` is the only real policy in this module.
   ``GET /integrations`` does not just serialize cached status — it filters
   which connector cards an org is allowed to see (platform-only connectors
   stay off customer UIs; a platform-served Codex card is hidden so customers
   cannot infer which model backs the platform default). Hiding a card is UI
   gating only: credential resolution and TLS interception keep running.

2. The status join. TLS-intercept providers and MCP-aggregator items are
   different runtimes. This endpoint flattens them into one ``items`` list
   because the WebUI renders one card grid. That join is a presentation
   contract, not a domain model.

3. Stale-tolerant reads, plus two apply-side routes that exist because of
   that. Status is served from cache and refreshed lazily (proxy hot path
   or near expiry). ``POST .../invalidate`` is the WebUI telling the broker
   that HUMR already holds a new credential (vault save, OAuth return) and
   the broker should drop that provider's cache, rewrite gateway env, and
   restart as needed — not a generic "give me fresh cards" call. Disconnect
   never uses this HTTP route; it goes through ``credentials_disconnect``,
   which invalidates inside ``credentials_service``. ``POST .../refresh_all``
   is the explicit Refresh button (MCP catalog reload + all-providers TLS
   invalidate in one shot).
"""

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import billing_service as billing
from credentials_service import CredentialsService
import device_flow
from humr_client import HumrClient
from mcp_aggregator import MCPAggregator
import permissions_control
import tls_intercept

logger = logging.getLogger("control_api")

# Integration-card visibility policy — the single place deciding which cards an
# org's WebUI shows. Visibility only: credential resolution and TLS interception
# are untouched, and the platform-owner org sees everything. Hardcoded until
# connector gating becomes a real CP-served feature.
_PLATFORM_OWNER_ORG_SLUG = "humanity-rules"
_PLATFORM_ONLY_SLUGS = frozenset({"x"})
_CODEX_SLUG = "openai-codex"


def _card_visible(item: dict, org_slug: str) -> bool:
    """Decide whether one integration card is shown to *org_slug*.

    Platform-only slugs never show outside the platform-owner org. The Codex
    card shows only when connected through a customer's own credential — a
    platform-served (or disconnected) Codex card would reveal which model
    provider backs the platform default.
    """
    if org_slug == _PLATFORM_OWNER_ORG_SLUG:
        return True
    slug = item.get("slug", "")
    if slug in _PLATFORM_ONLY_SLUGS:
        return False
    if slug == _CODEX_SLUG:
        metadata = item.get("metadata") or {}
        return item.get("status") == tls_intercept.STATUS_CONNECTED and not metadata.get("platform_shared")
    return True


async def _handle_unified_status(
    mcp_aggregator: MCPAggregator,
    tls_intercept_runtime: tls_intercept.TlsInterceptRuntime,
    humr_client: HumrClient,
    env_slug: str,
    org_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers and MCP-aggregator items.

    Reads cached TLS-intercept entries; refresh happens lazily (proxy hot
    path or near expiry). Per-provider apply after a vault/OAuth connect is
    ``POST .../tls_intercept/{provider}/invalidate``; the Refresh button is
    ``POST .../refresh_all``.
    """
    items = await tls_intercept_runtime.status_items()
    items.extend(await mcp_aggregator.status_items())
    items = [item for item in items if _card_visible(item=item, org_slug=org_slug)]

    return JSONResponse(content={
        "humr_control_plane_url": humr_client.control_plane_url,
        "env_slug": env_slug,
        "owner_username": humr_client.owner_username,
        "app_slug": humr_client.app_slug,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def build_control_app(
    mcp_aggregator: MCPAggregator,
    tls_intercept_runtime: tls_intercept.TlsInterceptRuntime,
    oauth_device_flow: device_flow.OAuthDeviceFlow,
    credentials_service: CredentialsService,
    humr_client: HumrClient,
    billing_service: billing.BillingService,
    env_slug: str,
    org_slug: str,
) -> Starlette:
    """Wire the browser-facing /__humr_broker/* facade (integrations, billing, permissions)."""
    async def integration_status_route(request: Request) -> Response:
        return await _handle_unified_status(
            mcp_aggregator=mcp_aggregator,
            tls_intercept_runtime=tls_intercept_runtime,
            humr_client=humr_client,
            env_slug=env_slug,
            org_slug=org_slug,
        )

    async def billing_entitlement_snapshot_route(request: Request) -> Response:
        """Serve the credits card its snapshot, refreshed first if it has gone stale.

        Raw numbers only: presentation stays in the card and needs no broker
        redeploy. A null entitlement means HUMR has not answered yet and the
        card shows nothing.
        """
        entitlement_snapshot = await billing_service.entitlement_snapshot_for_display()
        return JSONResponse(content={
            "entitlement": entitlement_snapshot,
            "upgrade_url": billing_service.upgrade_url(),
        })

    async def integrations_refresh_all_route(request: Request) -> Response:
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
        """Ask HUMR for a vault setup-session submit token for this provider."""
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
        Route(path="/billing", endpoint=billing_entitlement_snapshot_route, methods=["GET"]),
        Route(path="/integrations", endpoint=integration_status_route, methods=["GET"]),
        Route(path="/integrations/refresh_all", endpoint=integrations_refresh_all_route, methods=["POST"]),

        Route(path=f"{tls}/invalidate", endpoint=credentials_invalidate_route, methods=["POST"]),
        Route(path=f"{tls}/setup-session", endpoint=credentials_setup_session_route, methods=["POST"]),
        Route(path=f"{tls}/disconnect", endpoint=credentials_disconnect_route, methods=["POST"]),

        # Device login: broker-run OAuth device flows (no redirect callback).
        Route(path=f"{tls}/device/start", endpoint=device_start_route, methods=["POST"]),
        Route(path=f"{tls}/device/status", endpoint=device_status_route, methods=["GET"]),
        Route(path=f"{tls}/device/cancel", endpoint=device_cancel_route, methods=["POST"]),

        *mcp_aggregator.routes(prefix="/integrations"),
        *permissions_control.routes(prefix="/permissions", humr_client=humr_client),
    ]
    return Starlette(routes=routes)
