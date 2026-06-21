"""Merge.dev Agent Handler backend, talking through the HUMR-side relay.

Sibling to mcp_aggregator and connectors/. Lives outside connectors/ on purpose:
Merge isn't a single-service DCR/PKCE connector. It's a Magic Link relay that
stores credentials at Merge's end, and one MergeBackend serves up ~150 third-
party-service "connectors" at runtime via Merge's connectors API. The
DCRConnectorSpec abstraction doesn't fit; this module is bespoke.

The aggregator owns the lifecycle:

  - Constructs MergeBackend in its `__init__`.
  - Calls MergeBackend.boot() once at startup (idempotent registration with
    Merge via HUMR).
  - Includes MergeBackend.routes(prefix=...) in its own `routes()` so the
    integrations_broker mounts the Merge passthrough endpoints alongside the
    DCR OAuth ones.

The browser-facing handlers proxy POST/GET to HUMR with the env bearer and
identity (app_slug, owner_username) auto-injected. HUMR owns the Merge API key
and does the real work; we just relay.
"""

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import mcp_top_level_tools
from connectors._common import READ_VERBS, WRITE_VERBS


logger = logging.getLogger("mcp_merge_backend")


CONNECTOR_STATUS_CACHE_TTL_SECONDS = 60

# Tool names where the verb-prefix heuristic mis-classifies. Keep alphabetical.
MUTATION_OVERRIDES: dict[str, bool] = {}


def _mutates_from_double_underscore_name(*, name: str) -> bool:
    """Heuristic for Merge-style `connector__verb_object` names. Default to mutates on ambiguity."""
    try:
        verb = name.split("__", 1)[1].split("_", 1)[0].lower()
    except IndexError:
        return True
    if verb in READ_VERBS:
        return False
    if verb in WRITE_VERBS:
        return True
    return True


class MergeBackend:
    """Backend adapter for Merge.dev Agent Handler, relayed through HUMR."""

    name = "merge"

    def __init__(
        self,
        *,
        humr_control_plane_url: str,
        humr_env_bearer: str,
        humr_app_slug: str,
        humr_owner_username: str,
        excluded_connector_slugs: frozenset[str],
        on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]],
    ) -> None:
        self._humr_control_plane_url = humr_control_plane_url
        self._humr_env_bearer = humr_env_bearer
        self._humr_app_slug = humr_app_slug
        self._humr_owner_username = humr_owner_username
        self._excluded_connector_slugs = excluded_connector_slugs
        self._mcp_url = humr_control_plane_url + "/api/integrations/merge/mcp"
        self._status_url = humr_control_plane_url + "/api/integrations/merge/connector-status"
        self._headers = {
            "Authorization": f"Bearer {humr_env_bearer}",
            "X-Doh-App-Slug": humr_app_slug,
            "X-Doh-Owner-Username": humr_owner_username,
        }
        self._status_cache: dict[str, tuple[str, float]] = {}
        self._status_lock = asyncio.Lock()
        # Fired after a connect/disconnect we observe so the aggregator can
        # rebuild the Merge tool catalog.
        self.on_config_change = on_config_change

    def _client(self) -> Client:
        transport = StreamableHttpTransport(url=self._mcp_url, headers=dict(self._headers))
        return Client(transport)

    async def fetch_connectors(self) -> list[dict]:
        """Return Merge connector rows before native same-slug exclusions."""
        response = await self._passthrough(method="GET", path="/api/integrations/merge/connectors")
        if response.status_code != 200:
            logger.error("merge connectors list returned %d", response.status_code)
            return []
        payload = self._connectors_payload_from_response(response=response)
        if payload is None:
            return []
        return self._connectors_from_payload(payload=payload)

    def filter_visible_connectors(self, *, connectors: list[dict]) -> list[dict]:
        """Remove Merge connectors replaced by native same-slug connectors."""
        return [
            connector
            for connector in connectors
            if connector.get("slug") not in self._excluded_connector_slugs
        ]

    def _connectors_payload_from_response(self, *, response: Response) -> dict | None:
        try:
            return json.loads(response.body.decode())
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            logger.error("merge connectors passthrough returned non-JSON")
            return None

    def _connectors_from_payload(self, *, payload: dict) -> list[dict]:
        connectors = payload.get("connectors", [])
        if not isinstance(connectors, list):
            return []
        return [connector for connector in connectors if isinstance(connector, dict)]

    # ── Backend protocol ──────────────────────────────────────────────

    async def list_tool_catalog(self) -> list[mcp_top_level_tools.CatalogEntry]:
        async with self._client() as c:
            tools = await c.list_tools()
        out: list[mcp_top_level_tools.CatalogEntry] = []
        for tool in tools:
            tool_id = tool.name
            if "__" not in tool_id:
                logger.error("merge tool %r has no '__' connector prefix; skipping", tool_id)
                continue
            connector = tool_id.split("__", 1)[0]
            if connector in self._excluded_connector_slugs:
                continue
            mutates = MUTATION_OVERRIDES.get(tool_id)
            if mutates is None:
                mutates = _mutates_from_double_underscore_name(name=tool_id)
            out.append(mcp_top_level_tools.CatalogEntry(
                tool_id=tool_id,
                backend=self.name,
                connector=connector,
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
                mutates=mutates,
            ))
        return out

    async def list_known_connectors(self) -> list[mcp_top_level_tools.KnownConnector]:
        out: list[mcp_top_level_tools.KnownConnector] = []
        connectors = self.filter_visible_connectors(connectors=await self.fetch_connectors())
        for connector in connectors:
            slug = connector.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            out.append(mcp_top_level_tools.KnownConnector(
                backend=self.name,
                slug=slug,
                name=connector.get("name", slug),
                status=connector.get("status", "unknown"),
            ))
        return out

    async def call(self, *, tool_id: str, args: dict) -> dict:
        async with self._client() as c:
            result = await c.call_tool(tool_id, args, raise_on_error=False)
        # Pass through structured / text content as-is so the model sees what Merge said.
        return {
            "is_error": bool(getattr(result, "is_error", False)),
            "structured_content": getattr(result, "structured_content", None),
            "content": [
                getattr(block, "model_dump", lambda: block)() for block in getattr(result, "content", []) or []
            ],
        }

    async def connector_status(self, *, connector_slug: str) -> str:
        now = time.time()
        async with self._status_lock:
            cached = self._status_cache.get(connector_slug)
            if cached is not None and now - cached[1] < CONNECTOR_STATUS_CACHE_TTL_SECONDS:
                return cached[0]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    url=self._status_url,
                    headers=self._headers,
                    params={"connector_slug": connector_slug},
                )
            if resp.status_code == 200:
                status = resp.json().get("status", "unknown")
            else:
                logger.error("merge connector_status %s returned %d", connector_slug, resp.status_code)
                status = "transient_error"
        except Exception:
            logger.exception("merge connector_status %s failed", connector_slug)
            status = "transient_error"
        async with self._status_lock:
            self._status_cache[connector_slug] = (status, time.time())
        return status

    async def invalidate_caches(self) -> None:
        async with self._status_lock:
            self._status_cache.clear()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def boot(self) -> None:
        """Idempotent boot-time registration with Merge via HUMR passthrough."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url=f"{self._humr_control_plane_url}/api/integrations/merge/ensure-registered-user",
                    headers={
                        "Authorization": f"Bearer {self._humr_env_bearer}",
                        "Content-Type": "application/json",
                    },
                    json={"app_slug": self._humr_app_slug, "owner_username": self._humr_owner_username},
                )
            if resp.status_code != 200:
                logger.error("merge ensure-registered-user at boot returned %d: %s", resp.status_code, resp.text[:300])
                return
            logger.info("merge ensure-registered-user OK at boot")
        except Exception:
            logger.exception("merge ensure-registered-user at boot failed (continuing)")

    # ── Browser-facing passthrough routes ─────────────────────────────

    def routes(self, prefix: str) -> list[Route]:
        """Routes the integrations_broker mounts under /__humr_broker/<prefix>/merge/*.

        HUMR owns the Merge API key — these handlers forward to HUMR with bearer
        + identity injected and stream the response back to the WebUI.
        """
        return [
            Route(path=f"{prefix}/merge/connector-status", endpoint=self.handle_connector_status, methods=["GET"]),
            Route(path=f"{prefix}/merge/link-token", endpoint=self.handle_link_token, methods=["POST"]),
            Route(path=f"{prefix}/merge/disconnect", endpoint=self.handle_disconnect, methods=["POST"]),
        ]

    async def handle_connector_status(self, request: Request) -> Response:
        connector_slug = request.query_params.get("connector_slug", "")
        if not connector_slug:
            return JSONResponse(content={"error": "connector_slug is required"}, status_code=400)
        response = await self._passthrough(
            method="GET",
            path="/api/integrations/merge/connector-status",
            extra_query={"connector_slug": connector_slug},
        )
        # JS polls this every 3s during the connect modal and stops on the
        # first `connected`, so we fire the hook exactly once per successful
        # connect. Without this the aggregator's catalog still reflects the
        # boot snapshot — connector stays not_connected, tools stay missing.
        if response.status_code == 200 and self._status_says_connected(response=response):
            await self.on_config_change(self.name, connector_slug, "connected")
        return response

    def _status_says_connected(self, *, response: Response) -> bool:
        try:
            payload = json.loads(response.body.decode())
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("status") == "connected"

    async def handle_link_token(self, request: Request) -> Response:
        try:
            payload = json.loads((await request.body()).decode() or "{}")
        except json.JSONDecodeError:
            return JSONResponse(content={"error": "invalid JSON body"}, status_code=400)
        body: dict = {}
        if "connector_slug" in payload:
            body["connector_slug"] = payload["connector_slug"]
        return await self._passthrough(
            method="POST", path="/api/integrations/merge/link-token", json_body=body,
        )

    async def handle_disconnect(self, request: Request) -> Response:
        try:
            payload = json.loads((await request.body()).decode() or "{}")
        except json.JSONDecodeError:
            return JSONResponse(content={"error": "invalid JSON body"}, status_code=400)
        connector_slug = payload.get("connector_slug", "")
        if not isinstance(connector_slug, str) or not connector_slug:
            return JSONResponse(content={"error": "connector_slug is required"}, status_code=400)
        response = await self._passthrough(
            method="POST",
            path="/api/integrations/merge/disconnect",
            json_body={"connector_slug": connector_slug},
        )
        if response.status_code == 200:
            await self.on_config_change(self.name, connector_slug, "disconnected")
        return response

    async def _passthrough(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        extra_query: dict | None = None,
    ) -> Response:
        """Forward to HUMR with the env bearer attached. HUMR does the real work.

        app_slug and owner_username are auto-injected — into the query string
        for GET, into the JSON body for POST. Callers provide only the
        operation-specific fields.
        """
        url = f"{self._humr_control_plane_url}{path}"
        headers = {"Authorization": f"Bearer {self._humr_env_bearer}"}
        identity = {"app_slug": self._humr_app_slug, "owner_username": self._humr_owner_username}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "GET":
                    params = {**identity, **(extra_query or {})}
                    resp = await client.get(url=url, headers=headers, params=params)
                else:
                    body = {**identity, **(json_body or {})}
                    resp = await client.request(method=method, url=url, headers=headers, json=body)
        except Exception as exc:
            logger.exception("merge passthrough %s %s failed", method, path)
            return JSONResponse(content={"error": f"doh unreachable: {exc}"}, status_code=502)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("Content-Type", "application/json"),
        )
