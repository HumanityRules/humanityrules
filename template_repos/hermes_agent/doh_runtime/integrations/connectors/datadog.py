"""Datadog DCR connector. Self-contained: spec + backend + config + meta-tools.

═════════════════════════════════════════════════════════════════════════════
NOT REGISTERED — this module is intentionally absent from DCR_CONNECTORS in
connectors/__init__.py. The aggregator never instantiates a Datadog backend
today; the WebUI shows no Datadog card; no /oauth/start route is mounted.
Re-enable by adding `_datadog.SPEC` to DCR_CONNECTORS — one line.

WHY IT'S DISABLED. Datadog's MCP authorization server enforces a host-based
redirect_uri allowlist on /authorize that does NOT match what it accepts at
DCR registration. Independently reproduced 2026-05-16:

  - DCR `POST /api/unstable/mcp-server/register` with our redirect_uri →
    201 Created; the URI is echoed back verbatim.
  - GET /authorize with the same client_id + redirect_uri → 400 plain-text
    "Invalid redirect_uri" (no error_description).

Empirical allowlist (host-based; path doesn't matter):

  - 302 to consent: localhost, 127.0.0.1, [::1] (any port/path/scheme),
    claude.ai/*, app.datadoghq.com/*
  - 400 Invalid redirect_uri: everything else, including arbitrary paths on
    our own host

OIDC `application_type` (web / native / absent) makes no difference — the
gate is purely on host. CIMD is not an escape hatch either: Datadog's AS
metadata omits `client_id_metadata_document_supported`, and /authorize
rejects URL-shaped client_ids with 400 Invalid client_id without ever
fetching the metadata document. The MCP spec permits this — see "Open
Redirection" and "Trust Policies" in
https://modelcontextprotocol.io/specification/draft/basic/authorization
— but Datadog's setup docs document no self-serve path to get on the list.
Datadog's own steer for hosted brokers is the `DD_API_KEY` /
`DD_APPLICATION_KEY` header fallback ("for example, on a server" in the
setup docs).

Full investigation in docs/dcr_candidates/datadog.md.
═════════════════════════════════════════════════════════════════════════════

Datadog's first-party MCP exposes ~110 tools across 18 toolsets. The toolset
filter (`?toolsets=`) is a near-clone of PostHog's `?features=`: per-session
config persisted next to client.json/token.json, the upstream URL rebuilt on
every catalog load and call, and a small set of meta-tools the LLM uses to
inspect and change the scoping.

Differences from PostHog:

  - Datadog has no OAuth scopes (`scopes_supported=[]`) — access is governed by
    its own `mcp_read`/`mcp_write` RBAC perms inside the user's Datadog org.
    Spec carries `default_scope=None`, like Notion.
  - Toolsets is the only knob (no organization/project pinning, no per-tool
    filter), so we expose three meta-tools instead of PostHog's four:
    `datadog-list-toolsets`, `datadog-get-config`, `datadog-set-toolsets`.
  - Default toolsets is `["core"]` — matches Datadog's documented default and
    keeps the surface conservative until the user opts into more.
  - US1 region (`mcp.datadoghq.com`) only for v1. Datadog has six regions
    (us1/us3/us5/eu/ap1/ap2); supporting non-US1 customers means storing
    region in client.json and rebuilding the metadata + upstream URL per
    region. Defer until a non-US customer asks for it.

`"all"` is a Datadog-accepted pseudo-value meaning every toolset; we pass it
through verbatim and treat it as the only non-enumerated entry the validator
accepts.
"""

import json
import logging
import urllib.parse
from pathlib import Path
from typing import Awaitable, Callable

from fastmcp import Client

import mcp_top_level_tools

from ._common import DCRConnectorSpec, mutates_from_dash_name


logger = logging.getLogger("connectors.datadog")


# Tool names where the verb-prefix heuristic mis-classifies. Keep alphabetical.
# Empty for now; populate as real Datadog tools surface during integration.
MUTATION_OVERRIDES: dict[str, bool] = {}


# Datadog toolset names accepted by `?toolsets=`. Source: docs.datadoghq.com/bits_ai/mcp_server/tools/.
# `all` is the documented pseudo-value enabling every toolset.
TOOLSETS: frozenset[str] = frozenset({
    "core", "alerting", "apm", "cases", "dashboards", "dbm", "ddsql",
    "error-tracking", "feature-flags", "kubernetes", "llmobs", "networks",
    "onboarding", "product-analytics", "reference-tables", "security",
    "software-delivery", "synthetics", "workflows",
    "all",
})


# Datadog's documented default — the agent only sees the core toolset until the
# user broadens via datadog-set-toolsets.
DEFAULT_TOOLSETS: list[str] = ["core"]


class DatadogConfig:
    """Persisted per-session Datadog MCP scoping (toolsets only)."""

    def __init__(self, persistent_dir: Path) -> None:
        self._path = persistent_dir / "config.json"
        self._dir = persistent_dir
        self.toolsets: list[str] = list(DEFAULT_TOOLSETS)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (ValueError, OSError):
            logger.exception("datadog config %s unreadable; using defaults", self._path)
            return
        toolsets = data.get("toolsets")
        if toolsets:
            self.toolsets = list(toolsets)

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"toolsets": self.toolsets}, indent=2))

    def as_dict(self) -> dict:
        return {"toolsets": list(self.toolsets)}


class DatadogBackend:
    """Datadog's hosted MCP server. Includes synthetic toolset-config tools."""

    name = "datadog"

    def __init__(
        self,
        *,
        oauth_state,
        refresh_fn: Callable[[], Awaitable[str | None]],
        upstream_url: str,
        config: DatadogConfig,
        on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]],
    ) -> None:
        self._oauth_state = oauth_state
        self._refresh_fn = refresh_fn
        self._upstream_url = upstream_url
        self._config = config
        # Called when datadog-set-toolsets mutates state — the aggregator hooks
        # this to reload the catalog so the new scoping takes effect immediately.
        self.on_config_change = on_config_change

    @property
    def config(self) -> DatadogConfig:
        return self._config

    async def _token(self) -> str | None:
        token = self._oauth_state.access_token
        if token is not None:
            return token
        return await self._refresh_fn()

    def _scoped_url(self) -> str:
        if not self._config.toolsets:
            return self._upstream_url
        params = [("toolsets", ",".join(self._config.toolsets))]
        sep = "&" if "?" in self._upstream_url else "?"
        return self._upstream_url + sep + urllib.parse.urlencode(params)

    # ── Synthetic meta-tools ──────────────────────────────────────────
    #
    # Inlined into list_tool_catalog/call so the LLM finds and invokes them via
    # integrations_search_tools / integrations_call_tool — same UX as any other
    # Datadog tool. No top-level FastMCP registration; nothing about these
    # leaks into mcp_top_level_tools.py.

    def _meta_tool_catalog_entries(self) -> list[mcp_top_level_tools.CatalogEntry]:
        """Synthetic catalog entries the LLM should always see when Datadog is connected."""
        return [
            mcp_top_level_tools.CatalogEntry(
                tool_id="datadog-list-toolsets",
                backend=self.name,
                connector="datadog",
                description=(
                    "List the Datadog MCP toolsets the user can enable to scope the agent's "
                    "Datadog tool surface (~110 tools full firehose). Toolsets accepted by "
                    "?toolsets=. 'all' is a pseudo-value meaning every toolset. "
                    "Use before datadog-set-toolsets when you don't already know the toolset names."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                mutates=False,
            ),
            mcp_top_level_tools.CatalogEntry(
                tool_id="datadog-get-config",
                backend=self.name,
                connector="datadog",
                description=(
                    "Return the current Datadog MCP scoping: toolsets. "
                    "Default after a fresh connect is ['core']."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                mutates=False,
            ),
            mcp_top_level_tools.CatalogEntry(
                tool_id="datadog-set-toolsets",
                backend=self.name,
                connector="datadog",
                description=(
                    "Narrow (or widen) the Datadog tool surface the agent can see. Use after the "
                    "user confirms which areas they want the agent working on — e.g. "
                    "['core','synthetics','software-delivery'] for a release-monitoring workflow, "
                    "or ['core','apm','error-tracking'] for oncall. Pass ['all'] to expose every "
                    "toolset. Note: APM is in Preview and signup-required on Datadog's side; "
                    "those tools 403 at call time until the user enables them. Catalog reloads on success."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "toolsets": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    },
                    "required": ["toolsets"],
                    "additionalProperties": False,
                },
                mutates=True,
            ),
        ]

    async def _handle_meta(self, *, tool_id: str, args: dict) -> dict:
        if tool_id == "datadog-list-toolsets":
            return {
                "structured_content": {"toolsets": sorted(TOOLSETS)},
                "content": [],
                "is_error": False,
            }
        if tool_id == "datadog-get-config":
            return {
                "structured_content": {"config": self._config.as_dict()},
                "content": [],
                "is_error": False,
            }
        if tool_id == "datadog-set-toolsets":
            return await self._set_toolsets(args=args)
        return {
            "is_error": True,
            "structured_content": None,
            "content": [{"type": "text", "text": f"unknown meta tool {tool_id!r}"}],
        }

    async def _set_toolsets(self, *, args: dict) -> dict:
        toolsets = args.get("toolsets")
        if not isinstance(toolsets, list) or not toolsets:
            return {
                "is_error": True,
                "structured_content": {"error": "toolsets_required"},
                "content": [],
            }
        invalid = [t for t in toolsets if t not in TOOLSETS]
        if invalid:
            return {
                "is_error": True,
                "structured_content": {
                    "error": "invalid_toolsets",
                    "invalid": invalid,
                    "valid": sorted(TOOLSETS),
                },
                "content": [],
            }
        self._config.toolsets = list(toolsets)
        self._config.save()
        await self.on_config_change(self.name, "datadog", "reconfigured")
        return {
            "is_error": False,
            "structured_content": {"ok": True, "config": self._config.as_dict()},
            "content": [],
        }

    # ── Backend protocol ──────────────────────────────────────────────

    async def list_tool_catalog(self) -> list[mcp_top_level_tools.CatalogEntry]:
        if not self._oauth_state.has_token:
            return []
        token = await self._token()
        if token is None:
            return []
        async with Client(self._scoped_url(), auth=token) as c:
            tools = await c.list_tools()
        out: list[mcp_top_level_tools.CatalogEntry] = list(self._meta_tool_catalog_entries())
        for tool in tools:
            tool_id = tool.name
            mutates = MUTATION_OVERRIDES.get(tool_id)
            if mutates is None:
                mutates = mutates_from_dash_name(name=tool_id)
            out.append(mcp_top_level_tools.CatalogEntry(
                tool_id=tool_id,
                backend=self.name,
                connector="datadog",
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
                mutates=mutates,
            ))
        return out

    async def list_known_connectors(self) -> list[mcp_top_level_tools.KnownConnector]:
        return [mcp_top_level_tools.KnownConnector(
            backend=self.name,
            slug="datadog",
            name="Datadog",
            status="connected" if self._oauth_state.has_token else "not_connected",
        )]

    async def call(self, *, tool_id: str, args: dict) -> dict:
        if tool_id in {"datadog-list-toolsets", "datadog-get-config", "datadog-set-toolsets"}:
            return await self._handle_meta(tool_id=tool_id, args=args)
        token = await self._token()
        if token is None:
            return {"error": "not_connected", "connector": "datadog", "connect_kind": "oauth_dcr_pkce"}
        async with Client(self._scoped_url(), auth=token) as c:
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


def _make_backend(*, oauth_state, refresh_fn, persistent_dir: Path, on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]]) -> DatadogBackend:
    config = DatadogConfig(persistent_dir=persistent_dir)
    return DatadogBackend(
        oauth_state=oauth_state,
        refresh_fn=refresh_fn,
        upstream_url=SPEC.upstream_url,
        config=config,
        on_config_change=on_config_change,
    )


SPEC = DCRConnectorSpec(
    slug="datadog",
    label="Datadog",
    logo_url=None,
    # Real MCP endpoint per the DCR registration response (`mcp_resource_uri`)
    # and the protected-resource metadata at /.well-known/oauth-protected-resource.
    # `mcp.datadoghq.com/mcp` returns 404; the resource lives under /api/unstable/.
    upstream_url="https://mcp.datadoghq.com/api/unstable/mcp-server/mcp",
    oauth_metadata_url="https://mcp.datadoghq.com/.well-known/oauth-authorization-server",
    default_scope=None,  # Datadog's AS exposes no OAuth scopes; access is RBAC-controlled.
    make_backend=_make_backend,
    connect_kind="oauth_dcr_pkce",
)
