"""PostHog DCR connector. Self-contained: spec + backend + config + meta-tools.

Distinct from Notion in two ways:

  - PostHog issues resource-scoped tokens (each tool checks `<resource>:read|write`),
    so the spec carries a wide `default_scope` covering every category the MCP
    server exposes. Anything missing turns into a 403 from PostHog at call time,
    not a UX cliff at consent.
  - PostHog's MCP exposes ~350 tools and accepts `?features=` / `?tools=` /
    `organization_id` / `project_id` to narrow the set per session. We persist
    the user's choice next to client.json/token.json and rebuild the upstream
    URL on every catalog load and call. Three meta-tools — `posthog-list-feature-
    categories`, `posthog-get-config`, `posthog-set-config` — are exposed as
    synthetic catalog entries so the LLM finds them via integrations_search_tools
    just like any other PostHog tool.
"""

import json
import logging
import urllib.parse
from pathlib import Path
from typing import Awaitable, Callable

from fastmcp import Client

import mcp_top_level_tools

from ._common import DCRConnectorSpec, mutates_from_dash_name


logger = logging.getLogger("connectors.posthog")


# Tool names where the verb-prefix heuristic mis-classifies. Keep alphabetical.
MUTATION_OVERRIDES: dict[str, bool] = {
    # Native-language SQL queries can mutate via INSERT/UPDATE/DELETE — treat as write.
    "execute-sql": True,
    "query-run": True,
    # `query-trends`, `query-funnel`, etc. read.
}


# PostHog MCP feature categories that ?features= accepts. Used to validate the
# value the LLM sets via posthog-set-config, and surfaced through
# posthog-list-feature-categories.
FEATURE_CATEGORIES: frozenset[str] = frozenset({
    "workspace", "actions", "activity_logs", "alerts", "annotations", "cohorts",
    "conversations", "dashboards", "data_schema", "data_warehouse", "debug", "docs",
    "early_access_features", "error_tracking", "events", "experiments", "flags",
    "hog_functions", "hog_function_templates", "insights", "llm_analytics",
    "prompts", "logs", "notebooks", "persons", "reverse_proxy", "search",
    "sdk_doctor", "sql", "surveys", "workflows",
})


_DEFAULT_SCOPE = (
    "openid profile email "
    "action:read action:write activity_log:read alert:read alert:write "
    "annotation:read annotation:write batch_export:read batch_import:read "
    "cohort:read cohort:write comment:read comment:write conversation:read "
    "dashboard:read dashboard:write dashboard_template:read "
    "early_access_feature:read early_access_feature:write "
    "endpoint:read error_tracking:read error_tracking:write "
    "evaluation:read evaluation:write event_definition:read event_filter:read "
    "experiment:read experiment:write experiment_saved_metric:read "
    "external_data_schema:read external_data_source:read "
    "feature_flag:read feature_flag:write file_system:read group:read "
    "health_issue:read heatmap:read hog_flow:read hog_function:read "
    "insight:read insight:write insight_variable:read "
    "live_debugger:read llm_analytics:read llm_analytics:write "
    "llm_gateway:read llm_prompt:read llm_prompt:write llm_skill:read "
    "logs:read notebook:read notebook:write organization:read "
    "organization_integration:read organization_member:read person:read "
    "plugin:read product_tour:read project:read property_definition:read "
    "query:read query:write revenue_analytics:read session_recording:read "
    "session_recording_playlist:read sharing_configuration:read survey:read "
    "survey:write tagger:read tracing:read user:read user_interview:read "
    "warehouse_objects:read warehouse_table:read warehouse_view:read "
    "web_analytics:read webhook:read"
)


class PostHogConfig:
    """Persisted per-session PostHog MCP scoping (features/tools/org/project)."""

    def __init__(self, persistent_dir: Path) -> None:
        self._path = persistent_dir / "config.json"
        self._dir = persistent_dir
        self.features: list[str] = []
        self.tools: list[str] = []
        self.organization_id: str | None = None
        self.project_id: str | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
        except (ValueError, OSError):
            logger.exception("posthog config %s unreadable; using defaults", self._path)
            return
        self.features = list(data.get("features", []))
        self.tools = list(data.get("tools", []))
        self.organization_id = data.get("organization_id") or None
        self.project_id = data.get("project_id") or None

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "features": self.features,
            "tools": self.tools,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
        }, indent=2))

    def as_dict(self) -> dict:
        return {
            "features": list(self.features),
            "tools": list(self.tools),
            "organization_id": self.organization_id,
            "project_id": self.project_id,
        }


class PostHogBackend:
    """PostHog's hosted MCP server. Includes synthetic config tools."""

    name = "posthog"

    def __init__(
        self,
        *,
        oauth_state,
        refresh_fn: Callable[[], Awaitable[str | None]],
        upstream_url: str,
        config: PostHogConfig,
        on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]],
    ) -> None:
        self._oauth_state = oauth_state
        self._refresh_fn = refresh_fn
        self._upstream_url = upstream_url
        self._config = config
        # Called when posthog-set-config mutates state — the aggregator hooks this
        # to reload the catalog so the new scoping takes effect immediately.
        self.on_config_change = on_config_change

    @property
    def config(self) -> PostHogConfig:
        return self._config

    async def _token(self) -> str | None:
        token = self._oauth_state.access_token
        if token is not None:
            return token
        return await self._refresh_fn()

    def _scoped_url(self) -> str:
        params: list[tuple[str, str]] = []
        if self._config.features:
            params.append(("features", ",".join(self._config.features)))
        if self._config.tools:
            params.append(("tools", ",".join(self._config.tools)))
        if self._config.organization_id:
            params.append(("organization_id", self._config.organization_id))
        if self._config.project_id:
            params.append(("project_id", self._config.project_id))
        if not params:
            return self._upstream_url
        sep = "&" if "?" in self._upstream_url else "?"
        return self._upstream_url + sep + urllib.parse.urlencode(params)

    # ── Synthetic meta-tools ──────────────────────────────────────────
    #
    # Inlined into list_tool_catalog/call so the LLM finds and invokes them via
    # integrations_search_tools / integrations_call_tool — same UX as any other
    # PostHog tool. No top-level FastMCP registration; nothing about these
    # leaks into mcp_top_level_tools.py.

    def _meta_tool_catalog_entries(self) -> list[mcp_top_level_tools.CatalogEntry]:
        """Synthetic catalog entries the LLM should always see when PostHog is connected."""
        return [
            mcp_top_level_tools.CatalogEntry(
                tool_id="posthog-list-feature-categories",
                backend=self.name,
                connector="posthog",
                description=(
                    "List the PostHog MCP feature categories the user can enable to scope the "
                    "agent's PostHog tool surface (~350 tools full firehose). Categories accepted "
                    "by ?features=. Hyphens and underscores are equivalent server-side. "
                    "Use before posthog-set-config when you don't already know the category names."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                mutates=False,
            ),
            mcp_top_level_tools.CatalogEntry(
                tool_id="posthog-get-config",
                backend=self.name,
                connector="posthog",
                description=(
                    "Return the current PostHog MCP scoping: features, tools, organization_id, "
                    "project_id. Empty features+tools means the agent sees the full PostHog surface."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                mutates=False,
            ),
            mcp_top_level_tools.CatalogEntry(
                tool_id="posthog-set-config",
                backend=self.name,
                connector="posthog",
                description=(
                    "Narrow (or widen) the PostHog tool surface the agent can see. Use after the "
                    "user confirms which areas they want the agent working on — e.g. ['flags','experiments'] "
                    "for an experimentation workflow, or ['logs','error_tracking','events'] for oncall. "
                    "Pinning project_id (PostHog Project Settings → 'Project ID') strongly recommended; "
                    "multi-project orgs otherwise round-trip through `switch-project` on every call. "
                    "All args optional and leave the field unchanged if omitted; pass [] to clear "
                    "features/tools, '' to clear org/project. Catalog reloads on success."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "features": {"type": "array", "items": {"type": "string"}},
                        "tools": {"type": "array", "items": {"type": "string"}},
                        "organization_id": {"type": "string"},
                        "project_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                mutates=True,
            ),
        ]

    async def _handle_meta(self, *, tool_id: str, args: dict) -> dict:
        if tool_id == "posthog-list-feature-categories":
            return {"structured_content": {"categories": sorted(FEATURE_CATEGORIES)}, "content": [], "is_error": False}
        if tool_id == "posthog-get-config":
            return {"structured_content": {"config": self._config.as_dict()}, "content": [], "is_error": False}
        if tool_id == "posthog-set-config":
            return await self._set_config(args=args)
        return {"is_error": True, "structured_content": None, "content": [{"type": "text", "text": f"unknown meta tool {tool_id!r}"}]}

    async def _set_config(self, *, args: dict) -> dict:
        features = args.get("features")
        tools = args.get("tools")
        organization_id = args.get("organization_id")
        project_id = args.get("project_id")
        if features is not None:
            invalid = [f for f in features if f.replace("-", "_") not in FEATURE_CATEGORIES]
            if invalid:
                return {
                    "is_error": True,
                    "structured_content": {"error": "invalid_features", "invalid": invalid, "valid": sorted(FEATURE_CATEGORIES)},
                    "content": [],
                }
            self._config.features = [f.replace("-", "_") for f in features]
        if tools is not None:
            self._config.tools = list(tools)
        if organization_id is not None:
            self._config.organization_id = organization_id or None
        if project_id is not None:
            self._config.project_id = project_id or None
        self._config.save()
        await self.on_config_change(self.name, "posthog", "reconfigured")
        return {"is_error": False, "structured_content": {"ok": True, "config": self._config.as_dict()}, "content": []}

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
                connector="posthog",
                description=tool.description or "",
                input_schema=tool.inputSchema or {},
                mutates=mutates,
            ))
        return out

    async def list_known_connectors(self) -> list[mcp_top_level_tools.KnownConnector]:
        return [mcp_top_level_tools.KnownConnector(
            backend=self.name,
            slug="posthog",
            name="PostHog",
            status="connected" if self._oauth_state.has_token else "not_connected",
        )]

    async def call(self, *, tool_id: str, args: dict) -> dict:
        if tool_id.startswith("posthog-") and tool_id in {
            "posthog-list-feature-categories", "posthog-get-config", "posthog-set-config",
        }:
            return await self._handle_meta(tool_id=tool_id, args=args)
        token = await self._token()
        if token is None:
            return {"error": "not_connected", "connector": "posthog", "connect_kind": "oauth_dcr_pkce"}
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


def _make_backend(*, oauth_state, refresh_fn, persistent_dir: Path, on_config_change: Callable[[str, str, mcp_top_level_tools.StateTransition], Awaitable[None]]) -> PostHogBackend:
    config = PostHogConfig(persistent_dir=persistent_dir)
    return PostHogBackend(
        oauth_state=oauth_state,
        refresh_fn=refresh_fn,
        upstream_url=SPEC.upstream_url,
        config=config,
        on_config_change=on_config_change,
    )


SPEC = DCRConnectorSpec(
    slug="posthog",
    label="PostHog",
    upstream_url="https://mcp.posthog.com/mcp",
    oauth_metadata_url="https://mcp.posthog.com/.well-known/oauth-authorization-server",
    default_scope=_DEFAULT_SCOPE,
    make_backend=_make_backend,
    connect_kind="oauth_dcr_pkce",
)
