"""Tests for integrations_broker.py.

The broker runs inside the customer-env Hermes container (not Django), but
its correctness is load-bearing for the WebUI extension's Integrations pane
and for all Google Workspace tool calls. We unit-test the load-bearing
primitives (host→provider routing, header rewrite, CA/leaf generation, and
refresh-loop outcome classification) directly without standing up the
asyncio servers.
"""

import asyncio
import importlib.util
import pathlib
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch


def _batched(*, slug: str, result) -> dict:
    """Build a fake one-slug DOH response map for mocking `fetch_provider_tokens_batch`.

    The broker only ever asks DOH about slugs it knows; in tests every
    refresh path under inspection is single-provider, so one-key maps are
    enough.
    """
    return {slug: result}


def _install_mcp_aggregator_stub_if_needed() -> None:
    """Stub MCPAggregator only when the local test env lacks FastMCP."""
    if "mcp_aggregator" in sys.modules:
        return
    try:
        __import__("mcp_aggregator")
        return
    except ModuleNotFoundError as exc:
        if exc.name != "fastmcp":
            raise
    sys.modules.pop("mcp_aggregator", None)
    stub = types.ModuleType("mcp_aggregator")

    class MCPAggregator:
        pass

    stub.MCPAggregator = MCPAggregator
    sys.modules["mcp_aggregator"] = stub


def _load_broker_module() -> types.ModuleType:
    """Load template_repos/hermes_agent/doh_runtime/integrations_broker.py as a module.

    The broker imports its sibling `mcp_aggregator` module by bare name. When
    supervisor.sh runs the broker as a script in production, /opt/doh/runtime/
    is automatically on sys.path. Under pytest we're loading via importlib from
    the Django repo root, so we have to put the runtime dir on sys.path
    ourselves before exec_module triggers the bare imports.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    runtime_dir = repo_root / "template_repos" / "hermes_agent" / "doh_runtime"
    script_path = runtime_dir / "integrations_broker.py"
    if str(runtime_dir) not in sys.path:
        sys.path.insert(0, str(runtime_dir))
    _install_mcp_aggregator_stub_if_needed()
    spec = importlib.util.spec_from_file_location(
        name="integrations_broker_under_test",
        location=str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["integrations_broker_under_test"] = module
    spec.loader.exec_module(module)
    return module


broker = _load_broker_module()


def _make_token_store() -> broker.tls_intercept._TokenStore:
    """Create a fresh TLS token store for isolated broker tests."""
    return broker.tls_intercept._TokenStore(
        providers=broker.tls_intercept.TLS_INTERCEPT_PROVIDERS,
        refresh_config=broker.tls_intercept.DohRefreshConfig(
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        ),
        refresh_lead_seconds=broker.tls_intercept.REFRESH_LEAD_SECONDS,
    )


def _make_tls_runtime(ca_dir: pathlib.Path, private_dir: pathlib.Path) -> broker.tls_intercept.TlsInterceptRuntime:
    """Create a fresh TLS-intercept runtime for control-app tests."""
    return broker.tls_intercept.TlsInterceptRuntime(
        providers=broker.tls_intercept.TLS_INTERCEPT_PROVIDERS,
        refresh_config=broker.tls_intercept.DohRefreshConfig(
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        ),
        refresh_lead_seconds=broker.tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
    )


class TestHostToProviderRouting(unittest.TestCase):

    def test_all_google_hosts_route_to_google(self) -> None:
        for host in broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["google"].hosts:
            self.assertEqual(broker.tls_intercept.HOST_TO_TLS_PROVIDER[host], "google")

    def test_unknown_host_returns_none(self) -> None:
        store = _make_token_store()
        self.assertIsNone(store.provider_for_host(host="api.tavily.com"))
        self.assertIsNone(store.provider_for_host(host="example.com"))

    def test_connect_host_routing_is_case_and_trailing_dot_insensitive(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="GitHub.COM").slug, "github")
        self.assertEqual(store.provider_for_host(host="github.com.").slug, "github")


class TestRewriteAuthorization(unittest.TestCase):

    def test_existing_authorization_is_replaced(self) -> None:
        hdrs = [(b"authorization", b"Bearer SANDBOX-DUMMY"), (b"content-type", b"application/json")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="REAL-TOKEN",
            auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer REAL-TOKEN")

    def test_missing_authorization_gets_injected(self) -> None:
        hdrs = [(b"content-type", b"application/json")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer T")

    def test_host_is_set_to_upstream(self) -> None:
        hdrs = [(b"host", b"whatever"), (b"authorization", b"Bearer x")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        host = dict([(n.lower(), v) for n, v in out])[b"host"]
        self.assertEqual(host, b"gmail.googleapis.com")

    def test_hop_by_hop_proxy_headers_are_stripped(self) -> None:
        hdrs = [
            (b"authorization", b"Bearer x"),
            (b"proxy-connection", b"keep-alive"),
            (b"proxy-authorization", b"Basic xxx"),
        ]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        names = [n.lower() for n, _ in out]
        self.assertNotIn(b"proxy-connection", names)
        self.assertNotIn(b"proxy-authorization", names)

    def test_basic_x_access_token_format_for_github(self) -> None:
        import base64
        hdrs = [(b"authorization", b"Basic SANDBOX-PLACEHOLDER")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="ghs_real_token",
            auth_format=broker.tls_intercept.AUTH_FORMAT_BASIC_X_ACCESS_TOKEN,
            upstream_host="github.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        expected = b"Basic " + base64.b64encode(b"x-access-token:ghs_real_token")
        self.assertEqual(auth, expected)

    def test_telegram_path_token_is_rewritten_without_authorization_header(self) -> None:
        provider = broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["telegram"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[
                (b"host", b"api.telegram.org"),
                (b"authorization", b"Bearer placeholder"),
                (b"content-type", b"application/json"),
            ],
            path_with_query="/bot000000:DOH_PLACEHOLDER/getUpdates?timeout=20",
            token="123456:REAL",
            provider=provider,
            upstream_host="api.telegram.org",
        )
        self.assertEqual(path, "/bot123456:REAL/getUpdates?timeout=20")
        header_names = [name.lower() for name, _value in headers]
        self.assertNotIn(b"authorization", header_names)
        self.assertEqual(dict((name.lower(), value) for name, value in headers)[b"host"], b"api.telegram.org")

    def test_telegram_file_path_token_is_rewritten(self) -> None:
        provider = broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["telegram"]
        _headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"api.telegram.org")],
            path_with_query="/file/bot000000:DOH_PLACEHOLDER/documents/file.txt",
            token="123456:REAL",
            provider=provider,
            upstream_host="api.telegram.org",
        )
        self.assertEqual(path, "/file/bot123456:REAL/documents/file.txt")

    def test_url_rewrite_fails_closed_without_placeholder(self) -> None:
        """A URL-rewrite provider must reject paths missing its placeholder.

        Url-encoded placeholders (e.g. `%3A` instead of `:`) don't substring-match
        and must be rejected so we never forward an un-rewritten URL upstream.
        """
        provider = broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["telegram"]

        with self.assertRaisesRegex(ValueError, "placeholder"):
            broker.tls_intercept._rewrite_request_for_provider(
                headers=[(b"host", b"api.telegram.org")],
                path_with_query="/bot000000%3ADOH_PLACEHOLDER/sendMessage",
                token="123456:REAL",
                provider=provider,
                upstream_host="api.telegram.org",
            )


class TestForwardHeaderNormalization(unittest.TestCase):

    def test_dechunked_request_gets_content_length_and_no_transfer_encoding(self) -> None:
        out = broker.tls_intercept._normalize_forward_headers(
            headers=[
                (b"Host", b"github.com"),
                (b"Transfer-Encoding", b"chunked"),
                (b"Connection", b"keep-alive"),
                (b"Content-Type", b"application/json"),
            ],
            body_length=11,
        )

        by_name = {name.lower(): value for name, value in out}
        self.assertNotIn(b"transfer-encoding", by_name)
        self.assertNotIn(b"connection", by_name)
        self.assertEqual(by_name[b"content-length"], b"11")
        self.assertEqual(by_name[b"host"], b"github.com")

    def test_existing_content_length_is_replaced(self) -> None:
        out = broker.tls_intercept._normalize_forward_headers(
            headers=[
                (b"Host", b"api.telegram.org"),
                (b"Content-Length", b"999"),
            ],
            body_length=4,
        )

        content_lengths = [value for name, value in out if name.lower() == b"content-length"]
        self.assertEqual(content_lengths, [b"4"])


class TestHttpParsing(unittest.IsolatedAsyncioTestCase):

    async def test_read_chunked_consumes_trailers(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"4\r\ntest\r\n0\r\nX-Trailer: one\r\nAnother: two\r\n\r\nNEXT")

        body = await broker.tls_intercept._read_chunked(reader=reader)

        self.assertEqual(body, b"test")
        self.assertEqual(await reader.readexactly(4), b"NEXT")


class TestCertMinter(unittest.TestCase):

    def test_bootstrap_writes_bundle_and_leaf_mint_returns_ssl_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca_dir = pathlib.Path(tmp) / "ca"
            private_dir = pathlib.Path(tmp) / "private"
            minter = broker.tls_intercept._CertMinter(ca_dir=ca_dir, private_dir=private_dir)
            minter.bootstrap()
            bundle = ca_dir / "bundle.pem"
            self.assertTrue(bundle.exists())
            self.assertGreater(bundle.stat().st_size, 100)
            ctx = minter.context_for(hostname="gmail.googleapis.com")
            self.assertIsInstance(ctx, ssl.SSLContext)
            # Second call is cached — same object.
            ctx2 = minter.context_for(hostname="gmail.googleapis.com")
            self.assertIs(ctx, ctx2)
            # Different host mints a different context.
            ctx3 = minter.context_for(hostname="drive.googleapis.com")
            self.assertIsNot(ctx, ctx3)
            self.assertEqual(list(ca_dir.glob(".leaf-*.pem")), [])
            self.assertEqual(list(private_dir.glob(".leaf-*.pem")), [])

    def test_private_dir_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            private_dir = pathlib.Path(tmp) / "private"
            minter = broker.tls_intercept._CertMinter(ca_dir=pathlib.Path(tmp) / "ca", private_dir=private_dir)
            minter.bootstrap()
            self.assertEqual(private_dir.stat().st_mode & 0o777, 0o700)


class _StubAggregator:
    """Minimal MCPAggregator surface for the unified-status + control-app tests."""

    def __init__(self, *, cooldown_remaining: int | None, refresh_payload: tuple[bool, dict]) -> None:
        self._cooldown_remaining = cooldown_remaining
        self._refresh_payload = refresh_payload
        self.refresh_calls = 0

    async def status_items(self) -> list:
        return []

    def routes(self, prefix: str) -> list:
        return []

    def cooldown_remaining_seconds(self) -> int | None:
        return self._cooldown_remaining

    async def refresh_catalog(self) -> tuple[bool, dict]:
        self.refresh_calls += 1
        return self._refresh_payload


def _ready_stub_aggregator() -> _StubAggregator:
    """Stub aggregator for tests that don't exercise the refresh route."""
    return _StubAggregator(
        cooldown_remaining=None,
        refresh_payload=(True, {"ok": True, "tools": 0, "connectors": 0}),
    )


class TestControlIntegrations(unittest.IsolatedAsyncioTestCase):
    """/integrations renders from cache; it MUST NOT call DOH on the status path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.tls_runtime = _make_tls_runtime(ca_dir=root / "ca", private_dir=root / "private")

    async def test_get_integrations_reads_cache_without_calling_doh(self) -> None:
        """Status reads never call DOH; connected items come from the pre-warmed cache."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_ready_stub_aggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            )),
        ) as fetch_mock:
            await self.tls_runtime.refresh_slug(slug="google")
            self.assertEqual(fetch_mock.call_count, 1)
            with TestClient(app) as client:
                resp = client.get("/integrations")
                resp2 = client.get("/integrations")
                resp3 = client.get("/integrations")

        self.assertEqual(resp.status_code, 200)
        items_by_slug = {item["slug"]: item for item in resp.json()["items"]}
        self.assertEqual(items_by_slug["google"]["kind"], "tls_intercept")
        self.assertEqual(items_by_slug["google"]["status"], "connected")
        self.assertEqual(items_by_slug["github"]["status"], "not_connected")
        self.assertEqual(items_by_slug["telegram"]["status"], "not_connected")
        # Three back-to-back GETs add zero DOH calls beyond the explicit pre-warm.
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(resp2.json(), resp.json())
        self.assertEqual(resp3.json(), resp.json())
        self.assertEqual(
            await self.tls_runtime._token_store.token_for_host(host="gmail.googleapis.com"),
            "fresh-token",
        )

    async def test_absent_provider_is_not_cached(self) -> None:
        """An `absent` outcome from DOH must remove (not store) the cache entry."""
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            await self.tls_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_runtime._token_store._cache)
        items_by_slug = {item["slug"]: item for item in await self.tls_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")

    async def test_refresh_slug_refetches_even_when_cache_is_fresh(self) -> None:
        """Explicit refresh must hit DOH even when the cached token is still fresh."""
        responses = [
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1", expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T2", expires_in=3600, config={}, metadata={},
            )),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=responses) as fetch_mock:
            await self.tls_runtime.refresh_slug(slug="google")
            await self.tls_runtime.refresh_slug(slug="google")

        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(self.tls_runtime._token_store._cache["google"].access_token, "T2")

    async def test_transient_after_eviction_does_not_fabricate_entry(self) -> None:
        """A transient refresh outcome must not write a sentinel into an empty cache."""
        responses = [
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1", expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=responses):
            await self.tls_runtime.refresh_slug(slug="google")
            await self.tls_runtime._token_store.invalidate(slug="google")
            await self.tls_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_runtime._token_store._cache)

    async def test_transient_during_lead_window_keeps_serving_cached_token(self) -> None:
        """Refresh-ahead transient failure must NOT make the proxy say "not connected"
        when the cached token is unfresh (inside the lead window) but still un-expired.

        Without this, a DOH hiccup during the final `refresh_lead_seconds` of
        an access_token's life would surface as "not connected" to the
        sandbox even though we hold a usable token. The proxy should keep
        serving the cached token for the rest of its expires_at window.
        """
        import time
        store = self.tls_runtime._token_store
        # Seed an entry inside the lead window (lead is 300s; this has 120s left).
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            access_token="STILL-VALID",
            expires_at=time.monotonic() + 120,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertEqual(token, "STILL-VALID")
        # And the cache entry survives the failed refresh-ahead.
        self.assertEqual(store._cache["google"].access_token, "STILL-VALID")

    async def test_invalidate_races_with_inflight_refresh(self) -> None:
        """Invalidate must serialize behind an in-flight refresh for the same slug.

        Without the store lock around fetch+apply, this sequence used to
        silently lose the invalidate:
          1. Proxy hot path's `_ensure_fresh` starts the DOH refresh
             fetch (slow).
          2. User clicks Disconnect → `invalidate(slug)` clears the cache.
          3. Proxy's in-flight fetch resolves and writes a (now stale)
             entry back into the cache.
          4. Hook fires `refresh_slug` → `_ensure_fresh` reads the
             fresh-looking stale entry and returns without refetching.

        Correct behavior: invalidate waits for the in-flight refresh, the
        stale write lands, invalidate pops it, and the hook's refresh
        starts from an empty cache and re-asks DOH.
        """
        import threading
        fetch_calls: list[str] = []
        started = threading.Event()
        delayed = threading.Event()

        def first_stale(refresh_config: object, slugs: list[str]) -> object:
            fetch_calls.append("first")
            started.set()
            delayed.wait(timeout=5)
            return _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="STALE-IN-FLIGHT", expires_in=3600,
                config={}, metadata={},
            ))

        def second_absent(refresh_config: object, slugs: list[str]) -> object:
            fetch_calls.append("second")
            return _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ))

        fetches = [first_stale, second_absent]
        idx = 0
        def dispatch(*args: object, **kwargs: object) -> object:
            nonlocal idx
            fn = fetches[min(idx, len(fetches) - 1)]
            idx += 1
            return fn(*args, **kwargs)

        store = self.tls_runtime._token_store
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=dispatch):
            proxy_task = asyncio.create_task(store.token_for_host(host="gmail.googleapis.com"))
            self.assertTrue(await asyncio.to_thread(started.wait, timeout=5))
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0)  # let invalidate queue on the store lock
            delayed.set()  # release the proxy's in-flight refresh
            await proxy_task
            await invalidate_task

            # Hook step: the disconnect hook calls refresh_slug. It must
            # see an empty cache and re-ask DOH (second_absent fires here).
            await store.refresh(slug="google")

        self.assertEqual(fetch_calls, ["first", "second"])
        self.assertNotIn("google", store._cache)

    async def test_parked_refresh_all_cannot_overwrite_concurrent_invalidate(self) -> None:
        """A parked refresh_all() must not resurrect a token across a concurrent invalidate.

        Pre-single-lock repro (fetch happens outside any lock, then per-slug
        locks taken to apply):
          1. refresh_all() fetches outside the per-slug lock and parks at DOH.
          2. invalidate("google") clears the cache (its per-slug lock is free).
          3. refresh_all() resumes and applies its stale has_token result for
             google, undoing the invalidate.

        Single-lock makes this impossible: refresh_all holds `_lock`
        across fetch+apply, so invalidate queues behind it. By the time
        invalidate runs, refresh_all's stale write has already landed
        and invalidate pops it cleanly. The other providers' writes
        survive — only google's was invalidated.
        """
        import threading
        delayed = threading.Event()
        started = threading.Event()

        def parked_has_token(refresh_config: object, slugs: list[str]) -> object:
            started.set()
            delayed.wait(timeout=5)
            return {
                slug: broker.tls_intercept.RefreshResult(
                    outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                    access_token=f"STALE-{slug}", expires_in=3600,
                    config={}, metadata={},
                )
                for slug in slugs
            }

        store = self.tls_runtime._token_store
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=parked_has_token):
            refresh_all_task = asyncio.create_task(store.refresh_all())
            self.assertTrue(await asyncio.to_thread(started.wait, timeout=5))
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0)  # let invalidate queue on the store lock
            delayed.set()
            await refresh_all_task
            await invalidate_task

        # google's STALE write landed during refresh_all, then invalidate
        # popped it. github + telegram were untouched by the invalidate
        # so their refresh_all writes survive.
        self.assertNotIn("google", store._cache)
        self.assertEqual(store._cache["github"].access_token, "STALE-github")
        self.assertEqual(store._cache["telegram"].access_token, "STALE-telegram")

    async def test_transient_with_expired_cache_returns_none(self) -> None:
        """A transient refresh on a cache entry that's already past expires_at
        must return None — we don't hand the proxy an expired token just
        because the cache happens to still hold one.
        """
        import time
        store = self.tls_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            access_token="EXPIRED",
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertIsNone(token)

    async def test_status_items_prunes_expired_entry_before_render(self) -> None:
        """Status reads must prune expired entries before projecting connected state."""
        import time
        store = self.tls_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            access_token="EXPIRED",
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        items_by_slug = {item["slug"]: item for item in await store.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")
        self.assertNotIn("google", store._cache)

    async def test_gateway_env_snapshot_excludes_expired_entries(self) -> None:
        """Expired entries must be pruned before rendering gateway env bindings."""
        import time
        store = self.tls_runtime._token_store
        store._cache["telegram"] = broker.tls_intercept._TokenCacheEntry(
            access_token="EXPIRED",
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={"bot_token": "STALE"},
            metadata={},
        )

        snapshot = await self.tls_runtime.gateway_env_snapshot()
        slugs = [provider.slug for provider, _config in snapshot]
        self.assertNotIn("telegram", slugs)
        self.assertNotIn("telegram", store._cache)

    async def test_proxy_hot_path_prunes_expired_entry_after_transient(self) -> None:
        """Transient refresh on an expired entry must leave the cache empty."""
        import time
        store = self.tls_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            access_token="EXPIRED",
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertIsNone(token)
        self.assertNotIn("google", store._cache)

    async def test_refresh_endpoint_reloads_catalog_and_drops_tls_cache(self) -> None:
        """POST /integrations/refresh fans out catalog reload + all-providers TLS invalidate."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(
            cooldown_remaining=None,
            refresh_payload=(True, {"ok": True, "tools": 12, "connectors": 3}),
        )
        app = broker._build_control_app(
            aggregator=aggregator,
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            )),
        ):
            await self.tls_runtime.refresh_slug(slug="google")
            self.assertIn("google", self.tls_runtime._token_store._cache)
            with TestClient(app) as client:
                resp = client.post("/integrations/refresh")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "tools": 12, "connectors": 3})
        self.assertEqual(aggregator.refresh_calls, 1)
        self.assertEqual(self.tls_runtime._token_store._cache, {})

    async def test_refresh_endpoint_cooldown_skips_tls_invalidate(self) -> None:
        """Cooldown 429 must short-circuit before invalidate_all fires (would kick the gateway)."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(
            cooldown_remaining=17,
            refresh_payload=(True, {"ok": True, "tools": 0, "connectors": 0}),
        )
        app = broker._build_control_app(
            aggregator=aggregator,
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(self.tls_runtime, "invalidate_all", new_callable=AsyncMock) as invalidate_all_mock:
            with TestClient(app) as client:
                resp = client.post("/integrations/refresh")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json(), {"error": "refresh_cooldown", "retry_after_seconds": 17})
        invalidate_all_mock.assert_not_awaited()
        self.assertEqual(aggregator.refresh_calls, 0)

    async def test_refresh_endpoint_surfaces_gateway_restart_failure(self) -> None:
        """Catalog refresh succeeded but TLS invalidate's gateway restart failed -> 502 with error."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(
            cooldown_remaining=None,
            refresh_payload=(True, {"ok": True, "tools": 1, "connectors": 1}),
        )
        app = broker._build_control_app(
            aggregator=aggregator,
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            self.tls_runtime,
            "invalidate_all",
            new_callable=AsyncMock,
            side_effect=RuntimeError("gateway restart failed (process-compose returned 502)"),
        ):
            with TestClient(app) as client:
                resp = client.post("/integrations/refresh")

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("gateway restart failed", body["error"])

    async def test_provider_invalidate_endpoint_drops_one_provider_cache(self) -> None:
        """POST /integrations/{provider}/invalidate_tls_cache evicts one provider."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_ready_stub_aggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(self.tls_runtime, "invalidate", new_callable=AsyncMock) as invalidate_mock:
            with patch.object(self.tls_runtime, "invalidate_all", new_callable=AsyncMock) as invalidate_all_mock:
                with TestClient(app) as client:
                    resp = client.post("/integrations/github/invalidate_tls_cache")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "github")
        invalidate_mock.assert_awaited_once_with(slug="github")
        invalidate_all_mock.assert_not_awaited()

    async def test_vault_setup_session_forwards_identity_to_doh(self) -> None:
        """POST /integrations/{provider}/vault/setup-session asks DOH for a submit token."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_ready_stub_aggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            broker,
            "_post_control_plane_json",
            return_value=(200, {"submit_token": "signed-token"}),
        ) as post_mock:
            with TestClient(app) as client:
                resp = client.post("/integrations/telegram/vault/setup-session?origin=https%3A%2F%2Fhermes.dev.example.com")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["submit_token"], "signed-token")
        post_mock.assert_called_once_with(
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            path="/api/integrations/credentials/setup-session",
            payload={
                "owner_username": "vmendi",
                "app_slug": "hermes",
                "provider": "telegram",
                "public_origin": "https://hermes.dev.example.com",
            },
        )

    async def test_vault_disconnect_invalidates_only_provider_cache_on_success(self) -> None:
        """POST /integrations/{provider}/vault/disconnect evicts only that provider."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_ready_stub_aggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(broker, "_post_control_plane_json", return_value=(200, {"ok": True})) as post_mock:
            with patch.object(self.tls_runtime, "invalidate", new_callable=AsyncMock) as invalidate_mock:
                with patch.object(self.tls_runtime, "invalidate_all", new_callable=AsyncMock) as invalidate_all_mock:
                    with TestClient(app) as client:
                        resp = client.post("/integrations/telegram/vault/disconnect")

        self.assertEqual(resp.status_code, 200)
        post_mock.assert_called_once_with(
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            path="/api/integrations/credentials/disconnect",
            payload={
                "owner_username": "vmendi",
                "app_slug": "hermes",
                "provider": "telegram",
            },
        )
        invalidate_mock.assert_awaited_once_with(slug="telegram")
        invalidate_all_mock.assert_not_awaited()


class TestLazyTokenForHost(unittest.IsolatedAsyncioTestCase):
    """The TLS token store fetches lazily and reuses the cache until near-expiry."""

    def setUp(self) -> None:
        self.token_store = _make_token_store()

    async def test_first_call_fetches_subsequent_calls_use_cache(self) -> None:
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1",
                expires_in=3600,
                config={},
                metadata={},
            )),
        ) as fetch_mock:
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T1")
            self.assertEqual(await self.token_store.token_for_host(host="drive.googleapis.com"), "T1")
            fetch_mock.assert_called_once()

    async def test_refresh_when_within_lead_window(self) -> None:
        responses = [
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1",
                expires_in=3600,
                config={},
                metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T2",
                expires_in=3600,
                config={},
                metadata={},
            )),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=responses):
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T1")
            # Backdate the cached entry past the lead window to force a refresh.
            self.token_store._cache["google"].expires_at = self.token_store._cache["google"].expires_at - 3600
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T2")

    async def test_unknown_host_returns_none_without_fetching(self) -> None:
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch") as fetch_mock:
            self.assertIsNone(await self.token_store.token_for_host(host="api.tavily.com"))
            fetch_mock.assert_not_called()

    async def test_absent_outcome_leaves_cache_empty(self) -> None:
        """An `absent` outcome from DOH yields no cache entry — disconnected = absent, not a sentinel."""
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            self.assertIsNone(await self.token_store.token_for_host(host="gmail.googleapis.com"))
        self.assertNotIn("google", self.token_store._cache)


class TestFetchProviderTokensBatch(unittest.TestCase):
    """Parse DOH's `/api/integrations/tokens` response into a slug→RefreshResult map."""

    def _refresh_config(self) -> "broker.tls_intercept.DohRefreshConfig":
        return broker.tls_intercept.DohRefreshConfig(
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        )

    def _run_with_response(self, payload: dict) -> dict:
        class _FakeResp:
            def __init__(self, body: bytes) -> None:
                self.status = 200
                self._body = body
            def read(self) -> bytes:
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        import json as _json
        opener = _FakeResp(body=_json.dumps(payload).encode())
        with patch.object(broker.tls_intercept.urllib.request, "urlopen", return_value=opener):
            return broker.tls_intercept.fetch_provider_tokens_batch(
                refresh_config=self._refresh_config(),
                slugs=["google", "github", "telegram"],
            )

    def test_mixed_outcomes_parse_per_slug(self) -> None:
        results = self._run_with_response(payload={
            "results": {
                "google": {
                    "outcome": "has_token",
                    "access_token": "g-abc",
                    "expires_in": 3600,
                    "config": {},
                    "metadata": {},
                },
                "github": {"outcome": "absent"},
                "telegram": {"outcome": "transient"},
            },
        })

        self.assertEqual(results["google"].outcome, broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN)
        self.assertEqual(results["google"].access_token, "g-abc")
        self.assertEqual(results["google"].expires_in, 3600)
        self.assertEqual(results["github"].outcome, broker.tls_intercept.REFRESH_OUTCOME_ABSENT)
        self.assertEqual(results["telegram"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    def test_telegram_config_and_metadata_pass_through(self) -> None:
        results = self._run_with_response(payload={
            "results": {
                "google": {"outcome": "absent"},
                "github": {"outcome": "absent"},
                "telegram": {
                    "outcome": "has_token",
                    "access_token": "123:REAL",
                    "expires_in": 3600,
                    "config": {"allowed_users": ["42", "7"]},
                    "metadata": {"bot_username": "doh_bot"},
                },
            },
        })
        self.assertEqual(results["telegram"].config, {"allowed_users": ["42", "7"]})
        self.assertEqual(results["telegram"].metadata, {"bot_username": "doh_bot"})

    def test_slug_missing_from_response_is_transient(self) -> None:
        """A partial server response must NOT clear the broker's cache for the missing slug."""
        results = self._run_with_response(payload={"results": {"google": {"outcome": "absent"}}})
        self.assertEqual(results["github"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)
        self.assertEqual(results["telegram"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    def test_network_error_returns_transient_for_every_slug(self) -> None:
        """Any urlopen failure must surface as transient across the board, preserving the cache."""
        with patch.object(
            broker.tls_intercept.urllib.request,
            "urlopen",
            side_effect=OSError("connection refused"),
        ):
            results = broker.tls_intercept.fetch_provider_tokens_batch(
                refresh_config=self._refresh_config(),
                slugs=["google", "github", "telegram"],
            )
        for slug in ("google", "github", "telegram"):
            self.assertEqual(results[slug].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    def test_http_error_returns_transient_for_every_slug(self) -> None:
        import urllib.error
        http_err = urllib.error.HTTPError(
            url="https://doh.example/api/integrations/tokens", code=500, msg="x", hdrs={}, fp=None,
        )
        with patch.object(broker.tls_intercept.urllib.request, "urlopen", side_effect=http_err):
            results = broker.tls_intercept.fetch_provider_tokens_batch(
                refresh_config=self._refresh_config(),
                slugs=["google", "github"],
            )
        self.assertEqual(results["google"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)
        self.assertEqual(results["github"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)


class TestRefreshAllBatchedApply(unittest.IsolatedAsyncioTestCase):
    """`_TokenStore.refresh_all` must apply batched results under per-slug refresh_locks."""

    async def test_has_token_writes_absent_drops_transient_leaves(self) -> None:
        """One batched call covers all three apply paths."""
        import time
        store = _make_token_store()
        store._cache["github"] = broker.tls_intercept._TokenCacheEntry(
            access_token="PRIOR-GITHUB",
            expires_at=time.monotonic() + 600,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        batched_results = {
            "google": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="G", expires_in=3600, config={}, metadata={},
            ),
            "github": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
            "telegram": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        }
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=batched_results,
        ) as batch_mock:
            await store.refresh_all()

        self.assertEqual(batch_mock.call_count, 1)
        self.assertEqual(store._cache["google"].access_token, "G")
        self.assertEqual(store._cache["github"].access_token, "PRIOR-GITHUB")  # transient ⇒ preserved
        self.assertNotIn("telegram", store._cache)


class TestGatewayEnvRender(unittest.TestCase):
    """Render the DOH-managed block from a token-store snapshot."""

    def _telegram_provider(self) -> "broker.tls_intercept.TlsProviderSpec":
        return broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["telegram"]

    def _google_provider(self) -> "broker.tls_intercept.TlsProviderSpec":
        return broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["google"]

    def test_connected_vault_provider_renders_managed_block(self) -> None:
        snapshot = [(self._telegram_provider(), {"allowed_users": [42, 7]})]
        block = broker.tls_intercept.render_managed_block(snapshot=snapshot)
        self.assertIn(broker.tls_intercept.GATEWAY_ENV_BLOCK_BEGIN, block)
        self.assertIn(broker.tls_intercept.GATEWAY_ENV_BLOCK_END, block)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", block)
        self.assertIn("TELEGRAM_ALLOWED_USERS=42,7", block)

    def test_empty_snapshot_renders_empty_block(self) -> None:
        """No connected providers ⇒ no managed block at all (disconnected = absent)."""
        self.assertEqual(broker.tls_intercept.render_managed_block(snapshot=[]), "")

    def test_oauth_provider_contributes_no_env_lines(self) -> None:
        """OAuth providers (Google, GitHub) don't activate gateway platforms."""
        snapshot = [(self._google_provider(), {"some_key": "some_value"})]
        block = broker.tls_intercept.render_managed_block(snapshot=snapshot)
        self.assertEqual(block, "")

    def test_missing_list_config_skips_binding(self) -> None:
        """A connected provider without the optional list field omits its env var."""
        snapshot = [(self._telegram_provider(), {})]
        block = broker.tls_intercept.render_managed_block(snapshot=snapshot)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", block)
        self.assertNotIn("TELEGRAM_ALLOWED_USERS", block)

    def test_write_preserves_outside_lines_and_replaces_managed_block(self) -> None:
        """Lines outside the sentinel block survive; the block is fully replaced."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = pathlib.Path(tmp) / ".env"
            env_path.write_text(
                "USER_KEY=keep-me\n"
                f"{broker.tls_intercept.GATEWAY_ENV_BLOCK_BEGIN}\n"
                "STALE_VAR=old-value\n"
                f"{broker.tls_intercept.GATEWAY_ENV_BLOCK_END}\n"
                "ANOTHER=also-keep\n",
                encoding="utf-8",
            )
            snapshot = [(self._telegram_provider(), {"allowed_users": [1]})]
            block = broker.tls_intercept.render_managed_block(snapshot=snapshot)
            broker.tls_intercept.write_gateway_env_file(env_path=env_path, managed_block=block)
            text = env_path.read_text(encoding="utf-8")

        self.assertIn("USER_KEY=keep-me", text)
        self.assertIn("ANOTHER=also-keep", text)
        self.assertNotIn("STALE_VAR=old-value", text)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", text)
        self.assertIn("TELEGRAM_ALLOWED_USERS=1", text)

    def test_write_empty_block_strips_sentinels_entirely(self) -> None:
        """Disconnect path: empty managed block leaves no DOH-managed sentinels."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = pathlib.Path(tmp) / ".env"
            env_path.write_text(
                "USER_KEY=keep-me\n"
                f"{broker.tls_intercept.GATEWAY_ENV_BLOCK_BEGIN}\n"
                "TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER\n"
                f"{broker.tls_intercept.GATEWAY_ENV_BLOCK_END}\n",
                encoding="utf-8",
            )
            broker.tls_intercept.write_gateway_env_file(env_path=env_path, managed_block="")
            text = env_path.read_text(encoding="utf-8")

        self.assertEqual(text, "USER_KEY=keep-me\n")


class TestGatewayEnvHookIntegration(unittest.IsolatedAsyncioTestCase):
    """User-initiated invalidate triggers env render + restart for vault providers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = pathlib.Path(self.tmp.name) / "hermes.env"

    def _make_runtime(self, on_user_invalidate=None) -> "broker.tls_intercept.TlsInterceptRuntime":
        root = pathlib.Path(self.tmp.name)
        return broker.tls_intercept.TlsInterceptRuntime(
            providers=broker.tls_intercept.TLS_INTERCEPT_PROVIDERS,
            refresh_config=broker.tls_intercept.DohRefreshConfig(
                control_plane_url="https://doh.example",
                bearer="env-bearer",
                owner_username="vmendi",
                app_slug="hermes",
            ),
            refresh_lead_seconds=broker.tls_intercept.REFRESH_LEAD_SECONDS,
            ca_dir=root / "ca",
            private_dir=root / "private",
            on_user_invalidate=on_user_invalidate,
        )

    async def test_invalidate_fires_on_user_invalidate_hook(self) -> None:
        seen: list[str | None] = []

        async def on_user_invalidate(slug: str | None) -> None:
            seen.append(slug)

        runtime = self._make_runtime(on_user_invalidate=on_user_invalidate)
        await runtime.invalidate(slug="telegram")
        await runtime.invalidate_all()
        self.assertEqual(seen, ["telegram", None])

    async def test_proxy_hot_path_eviction_does_not_fire_hook(self) -> None:
        """Inner token_store.invalidate (used by proxy 401-evict path) bypasses the hook.

        Credential changes go through `runtime.invalidate`, not the inner
        store. A 401-eviction during normal traffic must NOT trigger a
        gateway restart.
        """
        seen: list[str | None] = []

        async def on_user_invalidate(slug: str | None) -> None:
            seen.append(slug)

        runtime = self._make_runtime(on_user_invalidate=on_user_invalidate)
        await runtime._token_store.invalidate(slug="telegram")
        self.assertEqual(seen, [])

    async def test_slug_requires_restart_only_for_vault_providers(self) -> None:
        """OAuth providers don't need a gateway restart when their cache flips."""
        runtime = self._make_runtime()
        self.assertTrue(broker._slug_requires_restart(slug="telegram", runtime=runtime))
        self.assertFalse(broker._slug_requires_restart(slug="google", runtime=runtime))
        self.assertFalse(broker._slug_requires_restart(slug="github", runtime=runtime))
        # Unknown slug: don't restart.
        self.assertFalse(broker._slug_requires_restart(slug="bogus", runtime=runtime))
        # None (Refresh-all) covers any vault provider in scope.
        self.assertTrue(broker._slug_requires_restart(slug=None, runtime=runtime))

    async def test_per_slug_invalidate_refreshes_only_that_slug(self) -> None:
        """Slug-targeted invalidate must NOT fan out to disconnected providers.

        Connecting one provider used to spam DOH with `no integration row`
        404s for every other (still disconnected) provider. The hook now
        narrows to `refresh_slug(slug)` when a slug is named, so DOH only
        hears about the one that actually changed.
        """
        runtime = self._make_runtime()
        tls_runtime_holder: dict = {"runtime": runtime}
        on_user_invalidate = broker._build_on_user_invalidate(
            tls_runtime_holder=tls_runtime_holder,
            env_path=self.env_path,
            process_compose_url="http://127.0.0.1:9999",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="t", expires_in=3600, config={}, metadata={},
            )),
        ) as fetch_mock:
            await on_user_invalidate("google")

        # Exactly one DOH round-trip and only for the named slug, not one per provider.
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(fetch_mock.call_args.kwargs["slugs"], ["google"])

    async def test_invalidate_all_uses_single_batched_call(self) -> None:
        """Explicit Refresh-all collapses to one DOH round-trip across every provider.

        The previous per-slug fan-out emitted one `INFO no integration row`
        Django log line per disconnected provider on every Refresh-all.
        Coalescing into a single POST (where `absent` is a normal entry,
        not a 4xx) makes that log line disappear.
        """
        runtime = self._make_runtime()
        tls_runtime_holder: dict = {"runtime": runtime}
        on_user_invalidate = broker._build_on_user_invalidate(
            tls_runtime_holder=tls_runtime_holder,
            env_path=self.env_path,
            process_compose_url="http://127.0.0.1:9999",
        )

        absent_for_every_slug = {
            slug: broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )
            for slug in broker.tls_intercept.TLS_INTERCEPT_PROVIDERS
        }

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=absent_for_every_slug,
        ) as batch_mock, patch.object(
            broker,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ):
            await on_user_invalidate(None)

        self.assertEqual(batch_mock.call_count, 1)
        called_slugs = batch_mock.call_args.kwargs["slugs"]
        self.assertEqual(set(called_slugs), set(broker.tls_intercept.TLS_INTERCEPT_PROVIDERS))
