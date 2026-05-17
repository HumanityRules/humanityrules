"""Notion DCR connector. Self-contained: spec + backend + overrides."""

import logging
from pathlib import Path
from typing import Awaitable, Callable

from fastmcp import Client

import mcp_top_level_tools

from ._common import DCRConnectorSpec, mutates_from_dash_name


logger = logging.getLogger("connectors.notion")


# Tool names where the verb-prefix heuristic mis-classifies. Keep alphabetical.
MUTATION_OVERRIDES: dict[str, bool] = {}


class NotionBackend:
    """Notion's hosted MCP server (OAuth DCR/PKCE). One connector, no extras."""

    name = "notion"

    def __init__(
        self,
        *,
        oauth_state,
        refresh_fn: Callable[[], Awaitable[str | None]],
        upstream_url: str,
        on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]],
    ) -> None:
        self._oauth_state = oauth_state
        self._refresh_fn = refresh_fn
        self._upstream_url = upstream_url
        # Notion has no mutable session state, so this hook never fires —
        # carried only to satisfy the Backend protocol uniformly.
        self.on_config_change = on_config_change

    async def _token(self) -> str | None:
        token = self._oauth_state.access_token
        if token is not None:
            return token
        return await self._refresh_fn()

    async def list_catalog(self) -> list[mcp_top_level_tools.CatalogEntry]:
        if not self._oauth_state.has_token:
            return []
        token = await self._token()
        if token is None:
            return []
        async with Client(self._upstream_url, auth=token) as c:
            tools = await c.list_tools()
        out: list[mcp_top_level_tools.CatalogEntry] = []
        for tool in tools:
            tool_id = tool.name
            mutates = MUTATION_OVERRIDES.get(tool_id)
            if mutates is None:
                mutates = mutates_from_dash_name(name=tool_id)
            out.append(mcp_top_level_tools.CatalogEntry(
                tool_id=tool_id,
                backend=self.name,
                connector="notion",
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
                mutates=mutates,
            ))
        return out

    async def list_known_connectors(self) -> list[mcp_top_level_tools.KnownConnector]:
        return [mcp_top_level_tools.KnownConnector(
            backend=self.name,
            slug="notion",
            name="Notion",
            status="connected" if self._oauth_state.has_token else "not_connected",
        )]

    async def call(self, *, tool_id: str, args: dict) -> dict:
        token = await self._token()
        if token is None:
            return {"error": "not_connected", "connector": "notion", "connect_kind": "oauth_dcr_pkce"}
        async with Client(self._upstream_url, auth=token) as c:
            result = await c.call_tool(tool_id, args, raise_on_error=False)
        return {
            "is_error": bool(getattr(result, "is_error", False)),
            "structured_content": getattr(result, "structured_content", None),
            "content": [
                getattr(block, "model_dump", lambda: block)() for block in getattr(result, "content", []) or []
            ],
        }

    async def connector_status(self, *, connector_slug: str) -> str:
        return "connected" if self._oauth_state.has_token else "not_connected"

    async def invalidate_caches(self) -> None:
        return None


def _make_backend(*, oauth_state, refresh_fn, persistent_dir: Path, on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]]) -> NotionBackend:
    """Spec-side factory; persistent_dir unused for Notion (no per-session state)."""
    _ = persistent_dir
    return NotionBackend(
        oauth_state=oauth_state,
        refresh_fn=refresh_fn,
        upstream_url=SPEC.upstream_url,
        on_config_change=on_config_change,
    )


SPEC = DCRConnectorSpec(
    slug="notion",
    label="Notion",
    upstream_url="https://mcp.notion.com/mcp",
    oauth_metadata_url="https://mcp.notion.com/.well-known/oauth-authorization-server",
    default_scope=None,  # Notion's AS ignores scope.
    make_backend=_make_backend,
    connect_kind="oauth_dcr_pkce",
)
