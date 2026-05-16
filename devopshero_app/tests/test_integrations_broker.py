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
import json
import pathlib
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch


def _load_broker_module():
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
    spec = importlib.util.spec_from_file_location(
        name="integrations_broker_under_test",
        location=str(script_path),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["integrations_broker_under_test"] = module
    spec.loader.exec_module(module)
    return module


broker = _load_broker_module()


class TestHostToProviderRouting(unittest.TestCase):

    def test_all_google_hosts_route_to_google(self) -> None:
        for host in broker.PROVIDERS["google"]["hosts"]:
            self.assertEqual(broker._host_to_provider_slug(host=host), "google")

    def test_unknown_host_returns_none(self) -> None:
        self.assertIsNone(broker._host_to_provider_slug(host="api.tavily.com"))
        self.assertIsNone(broker._host_to_provider_slug(host="example.com"))


class TestRewriteAuthorization(unittest.TestCase):

    def test_existing_authorization_is_replaced(self) -> None:
        hdrs = [(b"authorization", b"Bearer SANDBOX-DUMMY"), (b"content-type", b"application/json")]
        out = broker._rewrite_authorization(headers=hdrs, token="REAL-TOKEN", upstream_host="gmail.googleapis.com")
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer REAL-TOKEN")

    def test_missing_authorization_gets_injected(self) -> None:
        hdrs = [(b"content-type", b"application/json")]
        out = broker._rewrite_authorization(headers=hdrs, token="T", upstream_host="gmail.googleapis.com")
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer T")

    def test_host_is_set_to_upstream(self) -> None:
        hdrs = [(b"host", b"whatever"), (b"authorization", b"Bearer x")]
        out = broker._rewrite_authorization(headers=hdrs, token="T", upstream_host="gmail.googleapis.com")
        host = dict([(n.lower(), v) for n, v in out])[b"host"]
        self.assertEqual(host, b"gmail.googleapis.com")

    def test_hop_by_hop_proxy_headers_are_stripped(self) -> None:
        hdrs = [
            (b"authorization", b"Bearer x"),
            (b"proxy-connection", b"keep-alive"),
            (b"proxy-authorization", b"Basic xxx"),
        ]
        out = broker._rewrite_authorization(headers=hdrs, token="T", upstream_host="gmail.googleapis.com")
        names = [n.lower() for n, _ in out]
        self.assertNotIn(b"proxy-connection", names)
        self.assertNotIn(b"proxy-authorization", names)


class TestCertMinter(unittest.TestCase):

    def test_bootstrap_writes_bundle_and_leaf_mint_returns_ssl_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca_dir = pathlib.Path(tmp) / "ca"
            private_dir = pathlib.Path(tmp) / "private"
            minter = broker._CertMinter(ca_dir=ca_dir, private_dir=private_dir)
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
            minter = broker._CertMinter(ca_dir=pathlib.Path(tmp) / "ca", private_dir=private_dir)
            minter.bootstrap()
            self.assertEqual(private_dir.stat().st_mode & 0o777, 0o700)


class _StubAggregator:
    """Minimal MCPAggregator surface for the unified-status + control-app tests."""

    async def status_items(self, request):
        return []

    def routes(self, prefix):
        return []


class TestControlIntegrations(unittest.IsolatedAsyncioTestCase):
    """The /integrations endpoint refreshes every TLS-intercept provider before rendering."""

    def setUp(self) -> None:
        broker._provider_cache.clear()
        broker._provider_refresh_locks.clear()
        for slug in broker.PROVIDERS:
            broker._provider_refresh_locks[slug] = asyncio.Lock()
        broker._doh_refresh_config.update({
            "control_plane_url": "https://doh.example",
            "bearer": "env-bearer",
            "owner_username": "vmendi",
        })

    async def test_get_integrations_refreshes_then_returns_unified_status(self) -> None:
        from starlette.testclient import TestClient

        app = broker._build_control_app(
            aggregator=_StubAggregator(),
            control_plane_url="https://doh.example",
            owner_username="vmendi",
            env_slug="default",
        )

        with patch.object(
            broker,
            "_fetch_provider_token",
            return_value={"status": "connected", "access_token": "fresh-token", "expires_in": 3600},
        ) as fetch_mock:
            with TestClient(app) as client:
                resp = client.get("/integrations")
                self.assertEqual(resp.status_code, 200)
                payload = resp.json()

        items_by_slug = {item["slug"]: item for item in payload["items"]}
        self.assertEqual(items_by_slug["google"]["kind"], "tls_intercept")
        self.assertEqual(items_by_slug["google"]["status"], "connected")
        self.assertEqual(
            await broker._current_token_for_host(host="gmail.googleapis.com"),
            "fresh-token",
        )
        fetch_mock.assert_called_once()


class TestLazyTokenForHost(unittest.IsolatedAsyncioTestCase):
    """_current_token_for_host fetches lazily and reuses the cache until near-expiry."""

    def setUp(self) -> None:
        broker._provider_cache.clear()
        broker._provider_refresh_locks.clear()
        for slug in broker.PROVIDERS:
            broker._provider_refresh_locks[slug] = asyncio.Lock()
        broker._doh_refresh_config.update({
            "control_plane_url": "https://doh.example",
            "bearer": "env-bearer",
            "owner_username": "vmendi",
        })

    async def test_first_call_fetches_subsequent_calls_use_cache(self) -> None:
        with patch.object(
            broker,
            "_fetch_provider_token",
            return_value={"status": "connected", "access_token": "T1", "expires_in": 3600},
        ) as fetch_mock:
            self.assertEqual(await broker._current_token_for_host(host="gmail.googleapis.com"), "T1")
            self.assertEqual(await broker._current_token_for_host(host="drive.googleapis.com"), "T1")
            fetch_mock.assert_called_once()

    async def test_refresh_when_within_lead_window(self) -> None:
        responses = [
            {"status": "connected", "access_token": "T1", "expires_in": 3600},
            {"status": "connected", "access_token": "T2", "expires_in": 3600},
        ]
        with patch.object(broker, "_fetch_provider_token", side_effect=responses):
            self.assertEqual(await broker._current_token_for_host(host="gmail.googleapis.com"), "T1")
            # Backdate the cached entry past the lead window to force a refresh.
            broker._provider_cache["google"]["expires_at"] = (
                broker._provider_cache["google"]["expires_at"] - 3600
            )
            self.assertEqual(await broker._current_token_for_host(host="gmail.googleapis.com"), "T2")

    async def test_unknown_host_returns_none_without_fetching(self) -> None:
        with patch.object(broker, "_fetch_provider_token") as fetch_mock:
            self.assertIsNone(await broker._current_token_for_host(host="api.tavily.com"))
            fetch_mock.assert_not_called()

    async def test_not_connected_caches_no_token(self) -> None:
        with patch.object(
            broker,
            "_fetch_provider_token",
            return_value={"status": "not_connected", "access_token": None, "expires_in": None},
        ):
            self.assertIsNone(await broker._current_token_for_host(host="gmail.googleapis.com"))
        self.assertEqual(broker._provider_cache["google"]["status"], "not_connected")


class TestFetchProviderTokenClassification(unittest.TestCase):
    """Drive _fetch_provider_token through each outcome by faking urlopen."""

    def _run(self, status: int, payload: dict | None) -> dict:
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
        if 200 <= status < 300:
            opener = _FakeResp(status=status, body=body)
            with patch.object(broker.urllib.request, "urlopen", return_value=opener):
                return broker._fetch_provider_token(
                    control_plane_url="https://example.invalid",
                    bearer="b",
                    owner_username="u",
                    refresh_path="/api/x",
                )
        raise_with = urllib.error.HTTPError(
            url="https://example.invalid", code=status, msg="x", hdrs={}, fp=None,
        )
        raise_with.read = lambda: body  # type: ignore[assignment]
        with patch.object(broker.urllib.request, "urlopen", side_effect=raise_with):
            return broker._fetch_provider_token(
                control_plane_url="https://example.invalid",
                bearer="b",
                owner_username="u",
                refresh_path="/api/x",
            )

    def test_200_is_connected(self) -> None:
        out = self._run(status=200, payload={"access_token": "abc", "expires_in": 3600})
        self.assertEqual(out["status"], "connected")
        self.assertEqual(out["access_token"], "abc")
        self.assertEqual(out["expires_in"], 3600)

    def test_404_is_not_connected(self) -> None:
        self.assertEqual(self._run(status=404, payload={})["status"], "not_connected")

    def test_410_is_revoked(self) -> None:
        self.assertEqual(self._run(status=410, payload={})["status"], "revoked")

    def test_401_is_transient(self) -> None:
        self.assertEqual(self._run(status=401, payload={"error": "bad bearer"})["status"], "transient_error")

    def test_500_is_transient(self) -> None:
        self.assertEqual(self._run(status=500, payload={"error": "bad config"})["status"], "transient_error")

    def test_503_is_transient(self) -> None:
        self.assertEqual(self._run(status=503, payload={"error": "try later"})["status"], "transient_error")
