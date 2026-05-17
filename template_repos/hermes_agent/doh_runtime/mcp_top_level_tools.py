"""Progressive-disclosure surface the aggregator exposes to Hermes.

Four `@mcp.tool` functions sit on the FastMCP server in mcp_aggregator.py.
Together they let the LLM discover, inspect, and invoke arbitrary integration
tools without paying any per-tool prompt cost — which is the whole point: a
transparent ProxyProvider over Merge's Tool Pack ballooned the prompt past
the context window with ~1500 tool defs the user hadn't asked for.

The catalog is a flat dict of CatalogEntry keyed by `tool_id` (the upstream
backend's verbatim native name — no normalization). Backends stamp their
own `connector` and `mutates` fields. BM25 over `tool_id + connector +
description` is the search index.

Catalog load is async-on-boot, lenient on per-backend failure, and re-runs
on the user-facing Refresh button via CatalogStore.reload(...).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from fastmcp import FastMCP
from rank_bm25 import BM25Okapi


logger = logging.getLogger("mcp_top_level_tools")


SEARCH_DEFAULT_LIMIT = 10
SEARCH_HARD_CAP = 25
ONE_LINE_MAX_CHARS = 120
ENSURE_LOADED_TIMEOUT_SECONDS = 30
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({"a", "the", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are"})


@dataclass
class CatalogEntry:
    tool_id: str
    backend: str
    connector: str
    description: str
    input_schema: dict
    mutates: bool


@dataclass
class KnownConnector:
    """A connector the backend knows about, with or without action tools currently exposed."""
    backend: str
    slug: str
    name: str
    status: str


class Backend(Protocol):
    name: str
    # Fired by the backend after observing a state change (Merge connect/
    # disconnect, PostHog/Datadog meta-tool config change, etc.) so the
    # aggregator can rebuild the catalog. Backends without mutable session
    # state default this to a no-op; the aggregator reassigns it to its own
    # _reload_catalog at construction time.
    on_config_change: Callable[[], Awaitable[None]]

    async def list_catalog(self) -> list[CatalogEntry]: ...
    async def list_known_connectors(self) -> list[KnownConnector]: ...
    async def call(self, *, tool_id: str, args: dict) -> dict: ...
    async def connector_status(self, *, connector_slug: str) -> str: ...
    async def invalidate_caches(self) -> None: ...


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS]


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


@dataclass
class _ConnectorIndex:
    """Per-(backend, connector) row used by list_connectors and search shells."""
    backend: str
    slug: str
    name: str
    status: str
    tool_count: int


class CatalogStore:
    def __init__(self) -> None:
        self._entries: dict[str, CatalogEntry] = {}
        self._connectors: dict[tuple[str, str], _ConnectorIndex] = {}
        self._loaded = asyncio.Event()
        self._bm25: BM25Okapi | None = None
        self._bm25_keys: list[tuple[str, str]] = []  # ("tool", tool_id) | ("connector", "<backend>:<slug>")
        self._lock = asyncio.Lock()

    @property
    def entries(self) -> dict[str, CatalogEntry]:
        return self._entries

    @property
    def connectors(self) -> dict[tuple[str, str], _ConnectorIndex]:
        return self._connectors

    async def ensure_loaded(self, *, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._loaded.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def load_in_background(self, *, backends: list[Backend]) -> None:
        try:
            await self._reload_locked(backends=backends)
        finally:
            self._loaded.set()

    async def reload(self, *, backends: list[Backend]) -> None:
        async with self._lock:
            await self._reload_locked(backends=backends)
            self._loaded.set()

    async def _reload_locked(self, *, backends: list[Backend]) -> None:
        catalog_results = await asyncio.gather(
            *[self._safe_list_catalog(backend=b) for b in backends],
            return_exceptions=False,
        )
        connector_results = await asyncio.gather(
            *[self._safe_list_connectors(backend=b) for b in backends],
            return_exceptions=False,
        )

        new_entries: dict[str, CatalogEntry] = {}
        collisions = 0
        for backend, entries in zip(backends, catalog_results):
            for entry in entries:
                existing = new_entries.get(entry.tool_id)
                if existing is not None:
                    collisions += 1
                    logger.error(
                        "catalog tool_id collision: %r already from backend=%s, overwriting with backend=%s",
                        entry.tool_id, existing.backend, entry.backend,
                    )
                new_entries[entry.tool_id] = entry
        self._entries = new_entries

        # Build the connector index. Start from each backend's known connector list, then
        # fold in tool counts derived from the catalog.
        new_connectors: dict[tuple[str, str], _ConnectorIndex] = {}
        for backend, known in zip(backends, connector_results):
            for kc in known:
                key = (kc.backend, kc.slug)
                new_connectors[key] = _ConnectorIndex(
                    backend=kc.backend, slug=kc.slug, name=kc.name, status=kc.status, tool_count=0,
                )
        for entry in new_entries.values():
            key = (entry.backend, entry.connector)
            existing = new_connectors.get(key)
            if existing is None:
                # Catalog has tools for a connector the backend didn't list — surface it anyway.
                new_connectors[key] = _ConnectorIndex(
                    backend=entry.backend, slug=entry.connector, name=entry.connector, status="connected", tool_count=1,
                )
            else:
                existing.tool_count += 1
        # If the catalog already exposes callable tools for a connector, status is implicitly
        # "connected" regardless of what the backend's known-connectors view says — Merge marks
        # public-API connectors (Weather, Wikipedia) as not_connected because there's no OAuth
        # grant, but their tools are callable as-is. Reconcile here so list_connectors and
        # search reflect callability, not credential plumbing.
        for idx in new_connectors.values():
            if idx.tool_count > 0 and idx.status != "connected":
                idx.status = "connected"
        self._connectors = new_connectors

        self._bm25 = None
        self._bm25_keys = []
        logger.info(
            "catalog loaded: %d tool entries, %d connectors from %d backends (collisions=%d)",
            len(new_entries), len(new_connectors), len(backends), collisions,
        )

    async def _safe_list_catalog(self, *, backend: Backend) -> list[CatalogEntry]:
        try:
            return await backend.list_catalog()
        except Exception:
            logger.exception("backend %r failed list_catalog; contributing 0 entries", backend.name)
            return []

    async def _safe_list_connectors(self, *, backend: Backend) -> list[KnownConnector]:
        try:
            return await backend.list_known_connectors()
        except Exception:
            logger.exception("backend %r failed list_known_connectors; contributing 0 connectors", backend.name)
            return []

    def _ensure_bm25(self) -> None:
        if self._bm25 is not None:
            return
        keys: list[tuple[str, str]] = []
        corpus: list[list[str]] = []
        for tool_id in sorted(self._entries.keys()):
            entry = self._entries[tool_id]
            keys.append(("tool", tool_id))
            corpus.append(_tokenize(f"{tool_id} {entry.connector} {entry.description}"))
        for (backend, slug), idx in sorted(self._connectors.items()):
            if idx.tool_count > 0:
                continue  # action tools already represent this connector
            keys.append(("connector", f"{backend}:{slug}"))
            corpus.append(_tokenize(f"{slug} {idx.name}"))
        if not corpus:
            self._bm25 = BM25Okapi([["__empty__"]])
            self._bm25_keys = []
            return
        corpus = [doc if doc else ["__empty__"] for doc in corpus]
        self._bm25 = BM25Okapi(corpus)
        self._bm25_keys = keys

    def search(self, *, query: str, limit: int) -> list[tuple[str, object]]:
        """Return ranked results as (kind, payload) tuples.

        kind="tool"   payload=CatalogEntry
        kind="connector"   payload=_ConnectorIndex
        """
        self._ensure_bm25()
        if self._bm25 is None or not self._bm25_keys:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out: list[tuple[str, object]] = []
        for idx in ranked:
            if scores[idx] <= 0:
                break
            kind, key = self._bm25_keys[idx]
            if kind == "tool":
                out.append(("tool", self._entries[key]))
            else:
                backend, slug = key.split(":", 1)
                out.append(("connector", self._connectors[(backend, slug)]))
            if len(out) >= limit:
                break
        return out


def register(*, mcp: FastMCP, store: CatalogStore, backends: list[Backend], connect_kinds: dict[str, str]) -> None:
    """Register the four LLM-facing tools on the FastMCP server.

    `connect_kinds` maps backend.name → the string returned in the `connect_kind`
    field of a `not_connected` error. Built by the aggregator from the connector
    spec registry plus a fixed entry for Merge.
    """

    backends_by_name: dict[str, Backend] = {b.name: b for b in backends}

    async def _ensure_loaded_or_error() -> dict | None:
        ok = await store.ensure_loaded(timeout=ENSURE_LOADED_TIMEOUT_SECONDS)
        if not ok:
            return {"error": "catalog_unavailable"}
        return None

    async def _connector_status_for_entry(entry: CatalogEntry) -> str:
        """Status for a connector that has an entry in the catalog. Tools-in-catalog implies callable."""
        idx = store.connectors.get((entry.backend, entry.connector))
        if idx is not None:
            return idx.status
        backend = backends_by_name.get(entry.backend)
        if backend is None:
            return "unknown"
        return await backend.connector_status(connector_slug=entry.connector)

    @mcp.tool(
        name="integrations_search_tools",
        description=(
            "Search third-party integration tools (Slack, GitHub, Linear, Notion, etc.) by intent or name. "
            "Returns ranked matches as either kind='tool' (a callable tool) or kind='connector' (a connector that exists "
            "but isn't connected yet — its tools will appear after the user connects it). "
            "Use this whenever the user requests an action against an external system — these tools are NOT in your prompt."
        ),
    )
    async def integrations_search_tools(query: str, limit: int = SEARCH_DEFAULT_LIMIT, status_filter: str = "available") -> dict:
        err = await _ensure_loaded_or_error()
        if err is not None:
            return err
        bounded_limit = max(1, min(int(limit), SEARCH_HARD_CAP))
        if not query or not query.strip():
            return {"results": []}
        candidates = store.search(query=query, limit=bounded_limit * 4)
        results: list[dict] = []
        for kind, payload in candidates:
            if kind == "tool":
                entry = payload  # type: ignore[assignment]
                status = await _connector_status_for_entry(entry)
                if status_filter == "connected" and status != "connected":
                    continue
                results.append({
                    "kind": "tool",
                    "tool_id": entry.tool_id,
                    "connector": entry.connector,
                    "connector_status": status,
                    "one_line": _truncate(entry.description, ONE_LINE_MAX_CHARS),
                    "mutates": entry.mutates,
                })
            else:
                idx = payload  # type: ignore[assignment]
                if status_filter == "connected" and idx.status != "connected":
                    continue
                results.append({
                    "kind": "connector",
                    "connector": idx.slug,
                    "connector_name": idx.name,
                    "connector_status": idx.status,
                    "one_line": (
                        f"{idx.name} is available but not yet connected. "
                        "Tell the user to open the Integrations panel and click Connect to expose its tools."
                    ),
                })
            if len(results) >= bounded_limit:
                break
        return {"results": results}

    @mcp.tool(
        name="integrations_describe_tool",
        description="Return the full description and JSON Schema for an integration tool, by tool_id from a search result.",
    )
    async def integrations_describe_tool(tool_id: str) -> dict:
        err = await _ensure_loaded_or_error()
        if err is not None:
            return err
        entry = store.entries.get(tool_id)
        if entry is None:
            return {"error": "tool_not_found", "tool_id": tool_id}
        status = await _connector_status_for_entry(entry)
        return {
            "tool_id": entry.tool_id,
            "connector": entry.connector,
            "connector_status": status,
            "description": entry.description,
            "input_schema": entry.input_schema,
            "mutates": entry.mutates,
        }

    @mcp.tool(
        name="integrations_call_tool",
        description=(
            "Invoke an integration tool by tool_id with its arguments. "
            "If the connector is not connected, returns a structured not_connected error — do not retry; tell the user to open the Integrations panel."
        ),
    )
    async def integrations_call_tool(tool_id: str, args: dict) -> dict:
        err = await _ensure_loaded_or_error()
        if err is not None:
            return err
        entry = store.entries.get(tool_id)
        if entry is None:
            return {"error": "tool_not_found", "tool_id": tool_id}
        backend = backends_by_name.get(entry.backend)
        if backend is None:
            return {"error": "backend_unavailable", "backend": entry.backend}
        status = await _connector_status_for_entry(entry)
        if status != "connected":
            return {
                "error": "not_connected",
                "connector": entry.connector,
                "connect_kind": connect_kinds.get(entry.backend, "unknown"),
            }
        try:
            return await backend.call(tool_id=tool_id, args=args)
        except Exception as exc:
            logger.exception("integrations_call_tool failed for %r", tool_id)
            return {"error": "call_failed", "detail": str(exc)}

    @mcp.tool(
        name="integrations_list_connectors",
        description=(
            "List all known integration connectors with their connection status and action-tool counts. "
            "tool_count=0 is normal for an unconnected connector — Merge often only exposes its tools after the user connects. "
            "Use this when the user asks what's connected or what integrations are available."
        ),
    )
    async def integrations_list_connectors(status_filter: str = "available") -> dict:
        err = await _ensure_loaded_or_error()
        if err is not None:
            return err
        connectors: list[dict] = []
        for (_, slug), idx in sorted(store.connectors.items()):
            if status_filter == "connected" and idx.status != "connected":
                continue
            connectors.append({
                "connector": idx.slug,
                "connector_name": idx.name,
                "status": idx.status,
                "tool_count": idx.tool_count,
            })
        return {"connectors": connectors}

    _ = (integrations_search_tools, integrations_describe_tool, integrations_call_tool, integrations_list_connectors)
