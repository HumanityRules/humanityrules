"""Tests for the MCP aggregator's state-change machinery.

The aggregator runs inside the customer-env Hermes container, but two pieces
of its catalog state are too easy to break to leave untested:

  - `CatalogStore.drop` (disconnect path: pure in-memory mutation)
  - `CatalogStore.reload_backend` (connect/reconfigure path: per-backend
    slice replacement)
  - `MCPAggregator._on_state_change` dispatcher (block on disconnect,
    background on connect/reconfigure)
  - `MergeBackend.handle_connector_status` / `handle_disconnect` firing
    `on_config_change` with the right transition

These tests load the runtime modules in-process (with `fastmcp` and
`rank_bm25` stubbed when missing) and use fake Backend implementations to
avoid any network. The aim is to lock the slice-replacement and dispatch
contract — ranking correctness is BM25's job and is intentionally not
asserted here.
"""

import asyncio
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock


def _install_stubs_if_needed() -> None:
    """Stub fastmcp + rank_bm25 only when the local venv lacks them."""
    if "rank_bm25" not in sys.modules:
        try:
            __import__("rank_bm25")
        except ModuleNotFoundError:
            stub = types.ModuleType("rank_bm25")

            class _BM25Okapi:
                def __init__(self, corpus: list[list[str]]) -> None:
                    self._corpus = corpus

                def get_scores(self, tokens: list[str]) -> list[float]:
                    # Score = number of matching tokens between query and doc.
                    out = []
                    for doc in self._corpus:
                        out.append(float(sum(1 for t in tokens if t in doc)))
                    return out

            stub.BM25Okapi = _BM25Okapi
            sys.modules["rank_bm25"] = stub

    if "fastmcp" not in sys.modules:
        try:
            __import__("fastmcp")
        except ModuleNotFoundError:
            fastmcp = types.ModuleType("fastmcp")

            class _FastMCP:
                def __init__(self, name: str) -> None:
                    self.name = name

                def tool(self, **_):
                    def deco(f):
                        return f
                    return deco

                def http_app(self, **_):
                    return None

            class _Client:
                def __init__(self, *_, **__) -> None:
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    return None

                async def list_tools(self):
                    return []

                async def call_tool(self, *_, **__):
                    return None

            fastmcp.FastMCP = _FastMCP
            fastmcp.Client = _Client
            sys.modules["fastmcp"] = fastmcp

            transports = types.ModuleType("fastmcp.client.transports")

            class _StreamableHttpTransport:
                def __init__(self, *_, **__) -> None:
                    pass

            transports.StreamableHttpTransport = _StreamableHttpTransport
            sys.modules["fastmcp.client"] = types.ModuleType("fastmcp.client")
            sys.modules["fastmcp.client.transports"] = transports


def _load_runtime_module(name: str) -> types.ModuleType:
    """Load template_repos/hermes_agent/doh_runtime/<name>.py as a top-level module.

    Mirrors the load-by-bare-name behaviour of supervisor.sh / production. We
    add the runtime dir to sys.path so peer modules (`mcp_top_level_tools`,
    `connectors`, …) resolve.

    A sibling test file (test_integrations_broker.py) installs a tiny stub
    `mcp_aggregator` into sys.modules when fastmcp is absent; if it ran first
    under Django's test runner, we'd otherwise hand back that stub here. Pop
    any cached entry so we always exec the real runtime file.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    runtime_dir = repo_root / "template_repos" / "hermes_agent" / "doh_runtime"
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    sys.modules.pop(name, None)
    script_path = runtime_dir / f"{name.split('.')[0]}.py"
    if name.startswith("connectors."):
        script_path = runtime_dir / "connectors" / f"{name.split('.')[1]}.py"
    spec = importlib.util.spec_from_file_location(name=name, location=str(script_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_install_stubs_if_needed()
mcp_top_level_tools = _load_runtime_module("mcp_top_level_tools")
mcp_aggregator = _load_runtime_module("mcp_aggregator")
mcp_merge_backend = _load_runtime_module("mcp_merge_backend")


CatalogEntry = mcp_top_level_tools.CatalogEntry
KnownConnector = mcp_top_level_tools.KnownConnector
CatalogStore = mcp_top_level_tools.CatalogStore


def _make_entry(*, tool_id: str, backend: str, connector: str, mutates: bool = False) -> CatalogEntry:
    return CatalogEntry(
        tool_id=tool_id,
        backend=backend,
        connector=connector,
        description=f"{tool_id} description",
        input_schema={},
        mutates=mutates,
    )


class _FakeBackend:
    """In-memory Backend with mutable catalog/known-connectors state.

    Tests adjust `catalog` and `known` between calls and call
    `reload_backend` to verify slice replacement picks up the change.
    """

    def __init__(self, *, name: str, catalog: list[CatalogEntry], known: list[KnownConnector]) -> None:
        self.name = name
        self.catalog = list(catalog)
        self.known = list(known)
        self.list_tool_catalog_calls = 0
        self.list_known_calls = 0
        # The aggregator wires this; tests can override.
        self.on_config_change = None  # type: ignore[assignment]

    async def list_tool_catalog(self) -> list[CatalogEntry]:
        self.list_tool_catalog_calls += 1
        return list(self.catalog)

    async def list_known_connectors(self) -> list[KnownConnector]:
        self.list_known_calls += 1
        return list(self.known)

    async def call(self, *, tool_id: str, args: dict) -> dict:
        return {"is_error": False, "content": [], "structured_content": None}

    async def connector_status(self, *, connector_slug: str) -> str:
        for kc in self.known:
            if kc.slug == connector_slug:
                return kc.status
        return "unknown"

    async def invalidate_caches(self) -> None:
        return None


class TestAggregatorMergeDisabled(unittest.IsolatedAsyncioTestCase):
    """The Merge flag removes only Merge-backed catalog/status/routes."""

    async def asyncSetUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.agg = mcp_aggregator.MCPAggregator(
            port=9952,
            persistent_dir=pathlib.Path(self.tempdir.name),
            public_base_url="https://hermes.example",
            doh_control_plane_url="https://doh.example",
            doh_env_bearer="b",
            doh_app_slug="hermes-test",
            doh_owner_username="vmendi",
            merge_enabled=False,
        )

    async def test_disabled_constructor_omits_merge_backend(self) -> None:
        self.assertIsNone(self.agg._merge_backend)
        self.assertNotIn("merge", {backend.name for backend in self.agg._backends})

    async def test_disabled_routes_omit_merge_routes(self) -> None:
        paths = {route.path for route in self.agg.routes(prefix="/integrations")}
        self.assertNotIn("/integrations/merge/link-token", paths)
        self.assertNotIn("/integrations/merge/connector-status", paths)
        self.assertNotIn("/integrations/merge/disconnect", paths)

    async def test_disabled_status_items_omit_merge_connectors(self) -> None:
        items = await self.agg.status_items()
        self.assertNotIn("merge_connector", {item["kind"] for item in items})


class TestCatalogStoreDrop(unittest.IsolatedAsyncioTestCase):
    """`drop` is the disconnect path: pure dict mutation, no network."""

    async def asyncSetUp(self) -> None:
        self.store = CatalogStore()
        merge = _FakeBackend(
            name="merge",
            catalog=[
                _make_entry(tool_id="datadog__list_monitors", backend="merge", connector="datadog"),
                _make_entry(tool_id="datadog__create_monitor", backend="merge", connector="datadog", mutates=True),
                _make_entry(tool_id="slack__send_message", backend="merge", connector="slack", mutates=True),
            ],
            known=[
                KnownConnector(backend="merge", slug="datadog", name="Datadog", status="connected"),
                KnownConnector(backend="merge", slug="slack", name="Slack", status="connected"),
            ],
        )
        notion = _FakeBackend(
            name="notion",
            catalog=[
                _make_entry(tool_id="notion-search", backend="notion", connector="notion"),
            ],
            known=[KnownConnector(backend="notion", slug="notion", name="Notion", status="connected")],
        )
        await self.store.reload(backends=[merge, notion])

    async def test_drop_removes_only_matching_entries(self) -> None:
        # Force BM25 build so we can verify it gets invalidated.
        self.store.search(query="datadog", limit=10)
        self.assertIsNotNone(self.store._bm25)

        self.store.drop(backend_name="merge", connector="datadog")

        # Datadog tools gone; Slack and Notion intact.
        tool_ids = set(self.store.entries.keys())
        self.assertNotIn("datadog__list_monitors", tool_ids)
        self.assertNotIn("datadog__create_monitor", tool_ids)
        self.assertIn("slack__send_message", tool_ids)
        self.assertIn("notion-search", tool_ids)

    async def test_drop_flips_status_and_zeroes_tool_count(self) -> None:
        idx_before = self.store.connectors[("merge", "datadog")]
        self.assertEqual(idx_before.status, "connected")
        self.assertEqual(idx_before.tool_count, 2)

        self.store.drop(backend_name="merge", connector="datadog")

        idx_after = self.store.connectors[("merge", "datadog")]
        self.assertEqual(idx_after.status, "not_connected")
        self.assertEqual(idx_after.tool_count, 0)

    async def test_drop_invalidates_bm25(self) -> None:
        self.store.search(query="datadog", limit=10)
        self.assertIsNotNone(self.store._bm25)
        self.store.drop(backend_name="merge", connector="slack")
        self.assertIsNone(self.store._bm25)

    async def test_drop_does_not_remove_same_tool_id_from_other_backend(self) -> None:
        """Tool-id collision safety: `drop` must filter on (backend, connector), not tool_id alone."""
        # Synthesize a collision: same tool_id from a different backend.
        self.store._entries["notion-search"] = _make_entry(
            tool_id="notion-search", backend="notion", connector="notion",
        )
        # Same id under merge, different connector — pretend the catalog had it.
        self.store._entries["merge-shadow"] = _make_entry(
            tool_id="merge-shadow", backend="merge", connector="datadog",
        )

        self.store.drop(backend_name="merge", connector="datadog")
        self.assertIn("notion-search", self.store.entries)  # untouched
        self.assertNotIn("merge-shadow", self.store.entries)

    async def test_drop_unknown_connector_is_noop(self) -> None:
        before = dict(self.store.entries)
        self.store.drop(backend_name="merge", connector="never-existed")
        self.assertEqual(set(self.store.entries.keys()), set(before.keys()))


class TestCatalogStoreReloadBackend(unittest.IsolatedAsyncioTestCase):
    """`reload_backend` swaps one backend's slice in place."""

    async def asyncSetUp(self) -> None:
        self.store = CatalogStore()
        self.merge = _FakeBackend(
            name="merge",
            catalog=[
                _make_entry(tool_id="slack__send_message", backend="merge", connector="slack", mutates=True),
            ],
            known=[KnownConnector(backend="merge", slug="slack", name="Slack", status="connected")],
        )
        self.notion = _FakeBackend(
            name="notion",
            catalog=[_make_entry(tool_id="notion-search", backend="notion", connector="notion")],
            known=[KnownConnector(backend="notion", slug="notion", name="Notion", status="connected")],
        )
        await self.store.reload(backends=[self.merge, self.notion])

    async def test_reload_backend_picks_up_new_connector(self) -> None:
        # User just connected Datadog via Magic Link; Merge's tools/list now
        # returns Datadog tools too.
        self.merge.catalog.extend([
            _make_entry(tool_id="datadog__list_monitors", backend="merge", connector="datadog"),
            _make_entry(tool_id="datadog__create_monitor", backend="merge", connector="datadog", mutates=True),
        ])
        self.merge.known.append(
            KnownConnector(backend="merge", slug="datadog", name="Datadog", status="connected")
        )

        await self.store.reload_backend(backend=self.merge)

        self.assertIn("datadog__list_monitors", self.store.entries)
        self.assertIn("datadog__create_monitor", self.store.entries)
        idx = self.store.connectors[("merge", "datadog")]
        self.assertEqual(idx.status, "connected")
        self.assertEqual(idx.tool_count, 2)

    async def test_reload_backend_does_not_touch_other_backends(self) -> None:
        """Notion's slice must survive a Merge reload untouched."""
        # Mutate the *fake* notion backend after the initial reload — to prove
        # reload_backend(merge) does NOT re-call notion.list_tool_catalog().
        self.notion.catalog = [
            _make_entry(tool_id="notion-search", backend="notion", connector="notion"),
            _make_entry(tool_id="notion-create-page", backend="notion", connector="notion", mutates=True),
        ]
        notion_calls_before = self.notion.list_tool_catalog_calls

        await self.store.reload_backend(backend=self.merge)

        # Notion's existing entries are intact.
        self.assertIn("notion-search", self.store.entries)
        # And the new Notion entry is NOT picked up — reload_backend(merge) skipped it.
        self.assertNotIn("notion-create-page", self.store.entries)
        self.assertEqual(self.notion.list_tool_catalog_calls, notion_calls_before)

    async def test_reload_backend_drops_disappeared_connector(self) -> None:
        """If Merge drops a connector from its tool pack, reload_backend should mirror that."""
        self.merge.catalog = []  # Slack disappeared
        self.merge.known = []

        await self.store.reload_backend(backend=self.merge)

        self.assertNotIn("slack__send_message", self.store.entries)
        self.assertNotIn(("merge", "slack"), self.store.connectors)
        # Notion still present.
        self.assertIn("notion-search", self.store.entries)
        self.assertIn(("notion", "notion"), self.store.connectors)

    async def test_reload_backend_invalidates_bm25(self) -> None:
        self.store.search(query="slack", limit=10)
        self.assertIsNotNone(self.store._bm25)
        await self.store.reload_backend(backend=self.merge)
        self.assertIsNone(self.store._bm25)

    async def test_reload_backend_status_fixup_when_tools_present(self) -> None:
        """Merge marks public-API connectors not_connected; tools-callable implies connected."""
        self.merge.catalog = [
            _make_entry(tool_id="weather__get_forecast", backend="merge", connector="weather"),
        ]
        self.merge.known = [
            KnownConnector(backend="merge", slug="weather", name="Weather", status="not_connected"),
        ]
        await self.store.reload_backend(backend=self.merge)
        self.assertEqual(self.store.connectors[("merge", "weather")].status, "connected")

    async def test_bm25_search_picks_up_new_tools_after_reload_backend(self) -> None:
        """End-to-end: a search before/after the reload reflects the new slice."""
        results_before = self.store.search(query="datadog monitors", limit=10)
        self.assertEqual(results_before, [])

        self.merge.catalog.append(
            _make_entry(tool_id="datadog__list_monitors", backend="merge", connector="datadog")
        )
        self.merge.known.append(
            KnownConnector(backend="merge", slug="datadog", name="Datadog", status="connected")
        )
        await self.store.reload_backend(backend=self.merge)

        results_after = self.store.search(query="datadog monitors", limit=10)
        kinds_and_ids = [(kind, getattr(payload, "tool_id", None)) for kind, payload in results_after]
        self.assertIn(("tool", "datadog__list_monitors"), kinds_and_ids)


class TestAggregatorDispatcher(unittest.IsolatedAsyncioTestCase):
    """`_on_state_change` chooses block-vs-background per transition."""

    async def asyncSetUp(self) -> None:
        # Build an MCPAggregator with the inner state we care about, without
        # going through __init__ (which constructs a real FastMCP server, real
        # MergeBackend with httpx clients, etc). We poke just the fields the
        # dispatcher reads.
        self.agg = mcp_aggregator.MCPAggregator.__new__(mcp_aggregator.MCPAggregator)
        self.agg._catalog_store = CatalogStore()
        self.merge = _FakeBackend(
            name="merge",
            catalog=[
                _make_entry(tool_id="slack__send_message", backend="merge", connector="slack", mutates=True),
                _make_entry(tool_id="datadog__list_monitors", backend="merge", connector="datadog"),
            ],
            known=[
                KnownConnector(backend="merge", slug="slack", name="Slack", status="connected"),
                KnownConnector(backend="merge", slug="datadog", name="Datadog", status="connected"),
            ],
        )
        self.notion = _FakeBackend(
            name="notion",
            catalog=[_make_entry(tool_id="notion-search", backend="notion", connector="notion")],
            known=[KnownConnector(backend="notion", slug="notion", name="Notion", status="connected")],
        )
        self.agg._backends = [self.merge, self.notion]
        await self.agg._catalog_store.reload(backends=self.agg._backends)

    async def test_disconnect_blocks_and_drops_in_place(self) -> None:
        """Disconnect mutates the store synchronously — by the time the call returns, the store is updated."""
        self.assertIn("datadog__list_monitors", self.agg._catalog_store.entries)

        await self.agg._on_state_change("merge", "datadog", "disconnected")

        # Synchronous: no need to await any background task.
        self.assertNotIn("datadog__list_monitors", self.agg._catalog_store.entries)
        self.assertEqual(
            self.agg._catalog_store.connectors[("merge", "datadog")].status,
            "not_connected",
        )

    async def test_disconnect_does_not_call_list_tool_catalog(self) -> None:
        before = self.merge.list_tool_catalog_calls
        await self.agg._on_state_change("merge", "datadog", "disconnected")
        self.assertEqual(self.merge.list_tool_catalog_calls, before)

    async def test_connected_schedules_per_backend_reload(self) -> None:
        """Connected fires a background task; the call returns before reload runs."""
        # Mutate Merge's catalog to add a tool the store doesn't yet have.
        self.merge.catalog.append(
            _make_entry(tool_id="github__list_issues", backend="merge", connector="github")
        )
        self.merge.known.append(
            KnownConnector(backend="merge", slug="github", name="GitHub", status="connected")
        )

        await self.agg._on_state_change("merge", "github", "connected")
        # Yield so the scheduled task runs.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertIn("github__list_issues", self.agg._catalog_store.entries)
        # Notion's list_tool_catalog was NOT called by this path.
        self.assertEqual(self.notion.list_tool_catalog_calls, 1)  # only the asyncSetUp reload

    async def test_reconfigured_schedules_per_backend_reload(self) -> None:
        """Reconfigure (PostHog/Datadog set-config) fires the same per-backend reload."""
        self.merge.catalog = [
            _make_entry(tool_id="slack__send_message", backend="merge", connector="slack", mutates=True),
        ]
        self.merge.known = [KnownConnector(backend="merge", slug="slack", name="Slack", status="connected")]

        await self.agg._on_state_change("merge", "datadog", "reconfigured")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertNotIn("datadog__list_monitors", self.agg._catalog_store.entries)

    async def test_unknown_backend_does_not_raise(self) -> None:
        """Defensive: an on_config_change for an unknown backend logs and returns."""
        # Should not raise.
        await self.agg._on_state_change("nonexistent", "x", "connected")
        await asyncio.sleep(0)


class _FakeRequest:
    def __init__(self, *, query: dict | None = None, body: bytes = b"") -> None:
        self.query_params = query or {}
        self._body = body

    async def body(self) -> bytes:
        return self._body


class TestMergeBackendStateHooks(unittest.IsolatedAsyncioTestCase):
    """`MergeBackend.handle_connector_status` / `handle_disconnect` fire the dispatcher with the right transition."""

    async def asyncSetUp(self) -> None:
        self.hook = AsyncMock()
        self.merge = mcp_merge_backend.MergeBackend(
            doh_control_plane_url="https://doh.example",
            doh_env_bearer="b",
            doh_app_slug="hermes-test",
            doh_owner_username="vmendi",
            excluded_connector_slugs=frozenset({"notion", "posthog"}),
            on_config_change=self.hook,
        )

    def _stub_passthrough(self, *, status: int, body: bytes) -> None:
        """Replace MergeBackend._passthrough with a stub returning a canned Response."""
        from starlette.responses import Response

        async def fake(method: str, path: str, json_body: dict | None = None, extra_query: dict | None = None) -> Response:
            return Response(content=body, status_code=status, media_type="application/json")

        self.merge._passthrough = fake  # type: ignore[assignment]

    async def test_connector_status_fires_connected_when_upstream_connected(self) -> None:
        self._stub_passthrough(status=200, body=json.dumps({"slug": "datadog", "status": "connected"}).encode())
        req = _FakeRequest(query={"connector_slug": "datadog"})
        resp = await self.merge.handle_connector_status(req)  # type: ignore[arg-type]
        self.assertEqual(resp.status_code, 200)
        self.hook.assert_awaited_once_with("merge", "datadog", "connected")

    async def test_connector_status_silent_when_upstream_not_connected(self) -> None:
        self._stub_passthrough(status=200, body=json.dumps({"slug": "datadog", "status": "not_connected"}).encode())
        req = _FakeRequest(query={"connector_slug": "datadog"})
        await self.merge.handle_connector_status(req)  # type: ignore[arg-type]
        self.hook.assert_not_awaited()

    async def test_connector_status_silent_on_non_200(self) -> None:
        self._stub_passthrough(status=502, body=b"upstream error")
        req = _FakeRequest(query={"connector_slug": "datadog"})
        await self.merge.handle_connector_status(req)  # type: ignore[arg-type]
        self.hook.assert_not_awaited()

    async def test_connector_status_silent_on_non_json_200(self) -> None:
        self._stub_passthrough(status=200, body=b"not json")
        req = _FakeRequest(query={"connector_slug": "datadog"})
        await self.merge.handle_connector_status(req)  # type: ignore[arg-type]
        self.hook.assert_not_awaited()

    async def test_connector_status_missing_slug_returns_400_no_hook(self) -> None:
        req = _FakeRequest(query={})
        resp = await self.merge.handle_connector_status(req)  # type: ignore[arg-type]
        self.assertEqual(resp.status_code, 400)
        self.hook.assert_not_awaited()

    async def test_disconnect_fires_disconnected_on_200(self) -> None:
        self._stub_passthrough(status=200, body=b'{"ok": true}')
        body = json.dumps({"connector_slug": "slack"}).encode()
        req = _FakeRequest(body=body)
        resp = await self.merge.handle_disconnect(req)  # type: ignore[arg-type]
        self.assertEqual(resp.status_code, 200)
        self.hook.assert_awaited_once_with("merge", "slack", "disconnected")

    async def test_disconnect_silent_on_non_200(self) -> None:
        self._stub_passthrough(status=502, body=b"upstream")
        body = json.dumps({"connector_slug": "slack"}).encode()
        req = _FakeRequest(body=body)
        await self.merge.handle_disconnect(req)  # type: ignore[arg-type]
        self.hook.assert_not_awaited()

    async def test_disconnect_missing_slug_returns_400_no_hook(self) -> None:
        body = json.dumps({}).encode()
        req = _FakeRequest(body=body)
        resp = await self.merge.handle_disconnect(req)  # type: ignore[arg-type]
        self.assertEqual(resp.status_code, 400)
        self.hook.assert_not_awaited()
