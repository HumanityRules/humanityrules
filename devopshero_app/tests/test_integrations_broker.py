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

    async def status_items(self) -> list:
        return []

    def routes(self, prefix: str) -> list:
        return []


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
            aggregator=_StubAggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            ),
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
        """A 404/410 from DOH must remove (not store) the cache entry."""
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None,
                expires_in=None,
                config={},
                metadata={},
            ),
        ):
            await self.tls_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_runtime._token_store._cache)
        items_by_slug = {item["slug"]: item for item in await self.tls_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")

    async def test_transient_after_eviction_does_not_fabricate_entry(self) -> None:
        """A transient refresh outcome must not write a sentinel into an empty cache."""
        responses = [
            broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1", expires_in=3600, config={}, metadata={},
            ),
            broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_token", side_effect=responses):
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
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertEqual(token, "STILL-VALID")
        # And the cache entry survives the failed refresh-ahead.
        self.assertEqual(store._cache["google"].access_token, "STILL-VALID")

    async def test_invalidate_races_with_inflight_refresh(self) -> None:
        """Invalidate must serialize behind an in-flight refresh for the same slug.

        Without the per-provider refresh_lock around the cache pop, this
        sequence used to silently lose the invalidate:
          1. Proxy hot path calls `_ensure_fresh`, holds refresh_lock,
             starts `fetch_provider_token` (slow).
          2. User clicks Disconnect → `invalidate(slug)` clears the cache.
          3. Proxy's in-flight fetch resolves and writes a (now stale) entry
             back into the cache.
          4. Hook fires `refresh_slug` → `_ensure_fresh` reads the
             fresh-looking stale entry and returns without refetching.

        Correct behavior: invalidate waits for the in-flight refresh, the
        stale write lands, invalidate then pops it, and the hook's
        subsequent refresh starts from an empty cache and re-asks DOH.
        """
        import threading
        fetch_calls: list[str] = []
        delayed = threading.Event()

        def first_stale(refresh_config: object, provider: object) -> object:
            fetch_calls.append("first")
            delayed.wait(timeout=5)
            return broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="STALE-IN-FLIGHT", expires_in=3600,
                config={}, metadata={},
            )

        def second_absent(refresh_config: object, provider: object) -> object:
            fetch_calls.append("second")
            return broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None, expires_in=None, config={}, metadata={},
            )

        fetches = [first_stale, second_absent]
        idx = 0
        def dispatch(*args: object, **kwargs: object) -> object:
            nonlocal idx
            fn = fetches[min(idx, len(fetches) - 1)]
            idx += 1
            return fn(*args, **kwargs)

        store = self.tls_runtime._token_store
        with patch.object(broker.tls_intercept, "fetch_provider_token", side_effect=dispatch):
            proxy_task = asyncio.create_task(store.token_for_host(host="gmail.googleapis.com"))
            await asyncio.sleep(0.05)  # let proxy reach asyncio.to_thread
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0.05)  # let invalidate queue on the refresh_lock
            delayed.set()  # release the proxy's in-flight refresh
            await proxy_task
            await invalidate_task

            # Hook step: refresh_slug-equivalent. Must see an empty cache
            # and re-ask DOH (the second_absent stub fires here).
            provider = broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["google"]
            hook_entry = await store._ensure_fresh(provider=provider)

        self.assertEqual(fetch_calls, ["first", "second"])
        self.assertNotIn("google", store._cache)
        self.assertIsNone(hook_entry)

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
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertIsNone(token)

    async def test_status_items_treat_expired_entry_as_not_connected(self) -> None:
        """The UI must say "not connected" for an entry that's outlived its
        token, even if no refresh has run to prune the cache yet.

        Status reads don't go through `_ensure_fresh`, so without filtering
        on `is_usable` an expired entry would still surface as connected —
        a lie relative to what the proxy hot path would actually serve.
        """
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

    async def test_gateway_env_snapshot_excludes_expired_entries(self) -> None:
        """An expired entry must not contribute env bindings to the gateway —
        we'd be baking in credentials the proxy can no longer mint a token
        for, and the rendered env would lie to platform tools.
        """
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

    async def test_ensure_fresh_prunes_expired_entry_after_transient(self) -> None:
        """Cache hygiene: when a refresh-ahead transient lands on an entry
        that has aged past expires_at, `_ensure_fresh` should drop it rather
        than leave a zombie entry sitting in the cache.

        Read sites already filter via `_live_entry`, so this is purely about
        keeping the cache representation honest for consumers that iterate
        raw entries (e.g. gateway env rendering, debug introspection).
        """
        import time
        store = self.tls_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            access_token="EXPIRED",
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        provider = broker.tls_intercept.TLS_INTERCEPT_PROVIDERS["google"]
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        ):
            entry = await store._ensure_fresh(provider=provider)

        self.assertIsNone(entry)
        self.assertNotIn("google", store._cache)

    async def test_invalidate_endpoint_drops_cache(self) -> None:
        """POST /integrations/invalidate_tls_cache evicts every cached entry."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_StubAggregator(),
            tls_runtime=self.tls_runtime,
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
            env_slug="default",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ):
            await self.tls_runtime.refresh_slug(slug="google")
            self.assertIn("google", self.tls_runtime._token_store._cache)
            with TestClient(app) as client:
                resp = client.post("/integrations/invalidate_tls_cache")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.tls_runtime._token_store._cache, {})

    async def test_provider_invalidate_endpoint_drops_one_provider_cache(self) -> None:
        """POST /integrations/{provider}/invalidate_tls_cache evicts one provider."""
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_StubAggregator(),
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
            aggregator=_StubAggregator(),
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
            aggregator=_StubAggregator(),
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
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ) as fetch_mock:
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T1")
            self.assertEqual(await self.token_store.token_for_host(host="drive.googleapis.com"), "T1")
            fetch_mock.assert_called_once()

    async def test_refresh_when_within_lead_window(self) -> None:
        responses = [
            broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T1",
                expires_in=3600,
                config={},
                metadata={},
            ),
            broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="T2",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_token", side_effect=responses):
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T1")
            # Backdate the cached entry past the lead window to force a refresh.
            self.token_store._cache["google"].expires_at = self.token_store._cache["google"].expires_at - 3600
            self.assertEqual(await self.token_store.token_for_host(host="gmail.googleapis.com"), "T2")

    async def test_unknown_host_returns_none_without_fetching(self) -> None:
        with patch.object(broker.tls_intercept, "fetch_provider_token") as fetch_mock:
            self.assertIsNone(await self.token_store.token_for_host(host="api.tavily.com"))
            fetch_mock.assert_not_called()

    async def test_absent_outcome_leaves_cache_empty(self) -> None:
        """A 404/410 from DOH yields no cache entry — disconnected = absent, not a sentinel."""
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None,
                expires_in=None,
                config={},
                metadata={},
            ),
        ):
            self.assertIsNone(await self.token_store.token_for_host(host="gmail.googleapis.com"))
        self.assertNotIn("google", self.token_store._cache)


class TestFetchProviderTokenClassification(unittest.TestCase):
    """Drive fetch_provider_token through each outcome by faking urlopen."""

    def _run(self, status: int, payload: dict | None) -> broker.tls_intercept.RefreshResult:
        class _FakeResp:
            def __init__(self, status: int, body: bytes) -> None:
                self.status = status
                self._body = body
            def read(self) -> bytes:
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        import json as _json
        import urllib.error

        body = _json.dumps(payload or {}).encode()
        refresh_config = broker.tls_intercept.DohRefreshConfig(
            control_plane_url="https://example.invalid",
            bearer="b",
            owner_username="u",
            app_slug="app",
        )
        provider = broker.tls_intercept.TlsProviderSpec(
            slug="test",
            label="Test",
            refresh_path="/api/x",
            hosts=("example.invalid",),
            logo_url="/extensions/test.svg",
            credential_method=broker.tls_intercept.OAuthHeader(
                auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
            ),
        )
        if 200 <= status < 300:
            opener = _FakeResp(status=status, body=body)
            with patch.object(broker.tls_intercept.urllib.request, "urlopen", return_value=opener):
                return broker.tls_intercept.fetch_provider_token(refresh_config=refresh_config, provider=provider)
        raise_with = urllib.error.HTTPError(
            url="https://example.invalid", code=status, msg="x", hdrs={}, fp=None,
        )
        raise_with.read = lambda: body  # type: ignore[assignment]
        with patch.object(broker.tls_intercept.urllib.request, "urlopen", side_effect=raise_with):
            return broker.tls_intercept.fetch_provider_token(refresh_config=refresh_config, provider=provider)

    def test_200_is_connected(self) -> None:
        out = self._run(status=200, payload={"access_token": "abc", "expires_in": 3600})
        self.assertEqual(out.outcome, broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN)
        self.assertEqual(out.access_token, "abc")
        self.assertEqual(out.expires_in, 3600)

    def test_404_is_absent(self) -> None:
        self.assertEqual(self._run(status=404, payload={}).outcome, broker.tls_intercept.REFRESH_OUTCOME_ABSENT)

    def test_410_is_absent(self) -> None:
        """410 (refresh_token revoked, row deleted) collapses to absent: same user action as 404."""
        self.assertEqual(self._run(status=410, payload={}).outcome, broker.tls_intercept.REFRESH_OUTCOME_ABSENT)

    def test_401_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=401, payload={"error": "bad bearer"}).outcome,
            broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
        )

    def test_500_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=500, payload={"error": "bad config"}).outcome,
            broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
        )

    def test_503_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=503, payload={"error": "try later"}).outcome,
            broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
        )


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
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                access_token="t", expires_in=3600, config={}, metadata={},
            ),
        ) as fetch_mock:
            await on_user_invalidate("google")

        # Exactly one refresh (the named slug), not one per provider.
        self.assertEqual(fetch_mock.call_count, 1)

    async def test_invalidate_all_still_refreshes_every_provider(self) -> None:
        """Explicit Refresh-all (slug=None) must fan out to every provider."""
        runtime = self._make_runtime()
        tls_runtime_holder: dict = {"runtime": runtime}
        on_user_invalidate = broker._build_on_user_invalidate(
            tls_runtime_holder=tls_runtime_holder,
            env_path=self.env_path,
            process_compose_url="http://127.0.0.1:9999",
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                access_token=None, expires_in=None, config={}, metadata={},
            ),
        ) as fetch_mock, patch.object(
            broker,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ):
            await on_user_invalidate(None)

        self.assertEqual(fetch_mock.call_count, len(broker.tls_intercept.TLS_INTERCEPT_PROVIDERS))
