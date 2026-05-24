"""Tests for integrations_broker.py.

The broker runs inside the customer-env Hermes container (not Django), but
its correctness is load-bearing for the WebUI extension's Integrations pane
and for all Google Workspace tool calls. We unit-test the load-bearing
primitives (host→provider routing, header rewrite, CA/leaf generation, and
refresh-loop outcome classification) directly without standing up the
asyncio servers.
"""

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
        self.assertEqual(
            broker.tls_intercept._rewrite_telegram_path(
                path_with_query="/file/bot000000:DOH_PLACEHOLDER/documents/file.txt",
                token="123456:REAL",
            ),
            "/file/bot123456:REAL/documents/file.txt",
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
    """/integrations renders from cache; it must NOT force a refresh on every read."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.tls_runtime = _make_tls_runtime(ca_dir=root / "ca", private_dir=root / "private")

    async def test_get_integrations_returns_unified_status(self) -> None:
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
                status=broker.tls_intercept.STATUS_CONNECTED,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ):
            with TestClient(app) as client:
                resp = client.get("/integrations")
                self.assertEqual(resp.status_code, 200)
                payload = resp.json()

        items_by_slug = {item["slug"]: item for item in payload["items"]}
        self.assertEqual(items_by_slug["google"]["kind"], "tls_intercept")
        self.assertEqual(items_by_slug["google"]["status"], "connected")
        self.assertEqual(
            await self.tls_runtime._token_store.token_for_host(host="gmail.googleapis.com"),
            "fresh-token",
        )

    async def test_status_does_not_re_refresh_when_cache_is_fresh(self) -> None:
        """Two back-to-back status reads should hit DOH at most once per provider.

        Regression guard for the original behavior where /integrations
        force-refreshed every TLS provider on every read — for GitHub that
        rotates the refresh_token and invalidates any in-flight access
        token used by concurrent git/gh requests through the proxy.
        """
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
                status=broker.tls_intercept.STATUS_CONNECTED,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ) as fetch_mock:
            with TestClient(app) as client:
                client.get("/integrations")
                client.get("/integrations")
                client.get("/integrations")

        # Provider count == once-per-provider regardless of how many reads.
        self.assertEqual(fetch_mock.call_count, len(broker.tls_intercept.TLS_INTERCEPT_PROVIDERS))

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
                status=broker.tls_intercept.STATUS_CONNECTED,
                access_token="fresh-token",
                expires_in=3600,
                config={},
                metadata={},
            ),
        ) as fetch_mock:
            with TestClient(app) as client:
                client.get("/integrations")  # fills cache
                client.post("/integrations/invalidate_tls_cache")
                client.get("/integrations")  # cache cleared, refetches

        # Once per provider on first GET, then once again per provider
        # on the second GET because invalidate dropped the cache.
        self.assertEqual(fetch_mock.call_count, 2 * len(broker.tls_intercept.TLS_INTERCEPT_PROVIDERS))

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
                status=broker.tls_intercept.STATUS_CONNECTED,
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
                status=broker.tls_intercept.STATUS_CONNECTED,
                access_token="T1",
                expires_in=3600,
                config={},
                metadata={},
            ),
            broker.tls_intercept.RefreshResult(
                status=broker.tls_intercept.STATUS_CONNECTED,
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

    async def test_not_connected_caches_no_token(self) -> None:
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_token",
            return_value=broker.tls_intercept.RefreshResult(
                status=broker.tls_intercept.STATUS_NOT_CONNECTED,
                access_token=None,
                expires_in=None,
                config={},
                metadata={},
            ),
        ):
            self.assertIsNone(await self.token_store.token_for_host(host="gmail.googleapis.com"))
        self.assertEqual(self.token_store._cache["google"].status, broker.tls_intercept.STATUS_NOT_CONNECTED)


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
            credential_location=broker.tls_intercept.CREDENTIAL_LOCATION_AUTHORIZATION_HEADER,
            auth_format=broker.tls_intercept.AUTH_FORMAT_BEARER,
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
        self.assertEqual(out.status, broker.tls_intercept.STATUS_CONNECTED)
        self.assertEqual(out.access_token, "abc")
        self.assertEqual(out.expires_in, 3600)

    def test_404_is_not_connected(self) -> None:
        self.assertEqual(self._run(status=404, payload={}).status, broker.tls_intercept.STATUS_NOT_CONNECTED)

    def test_410_is_revoked(self) -> None:
        self.assertEqual(self._run(status=410, payload={}).status, broker.tls_intercept.STATUS_REVOKED)

    def test_401_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=401, payload={"error": "bad bearer"}).status,
            broker.tls_intercept.STATUS_TRANSIENT_ERROR,
        )

    def test_500_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=500, payload={"error": "bad config"}).status,
            broker.tls_intercept.STATUS_TRANSIENT_ERROR,
        )

    def test_503_is_transient(self) -> None:
        self.assertEqual(
            self._run(status=503, payload={"error": "try later"}).status,
            broker.tls_intercept.STATUS_TRANSIENT_ERROR,
        )
