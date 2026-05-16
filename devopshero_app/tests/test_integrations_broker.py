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


class TestControlKick(unittest.IsolatedAsyncioTestCase):

    def setUp(self) -> None:
        broker._provider_state.clear()
        broker._host_token.clear()
        broker._provider_refresh_locks.clear()

    async def test_kick_refreshes_before_returning_unified_status(self) -> None:
        """POST /integrations/google/kick should refresh, then return the unified items list."""
        from starlette.testclient import TestClient

        for slug in broker.PROVIDERS:
            broker._provider_refresh_locks[slug] = asyncio.Lock()

        # Stub MCPAggregator with the minimal surface our unified status + control app need.
        class _StubAggregator:
            async def handle_status(self, request):
                from starlette.responses import JSONResponse
                return JSONResponse(content={"providers": {}})
            async def handle_merge_connectors(self, request):
                from starlette.responses import JSONResponse
                return JSONResponse(content={"connectors": []}, status_code=502)
            def routes(self, prefix):
                return []

        app = broker._build_control_app(
            aggregator=_StubAggregator(),
            control_plane_url="https://doh.example",
            bearer="env-bearer",
            owner_username="vmendi",
            env_slug="default",
        )

        with patch.object(
            broker,
            "_fetch_provider_token",
            return_value={"kind": "ok", "access_token": "fresh-token", "expires_in": 3600},
        ) as fetch_mock:
            with TestClient(app) as client:
                resp = client.post("/integrations/google/kick")
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

    def test_200_is_ok(self) -> None:
        out = self._run(status=200, payload={"access_token": "abc", "expires_in": 3600})
        self.assertEqual(out["kind"], "ok")
        self.assertEqual(out["access_token"], "abc")
        self.assertEqual(out["expires_in"], 3600)

    def test_404_is_not_connected(self) -> None:
        self.assertEqual(self._run(status=404, payload={})["kind"], "not_connected")

    def test_410_is_revoked(self) -> None:
        self.assertEqual(self._run(status=410, payload={})["kind"], "revoked")

    def test_401_is_fatal(self) -> None:
        self.assertEqual(self._run(status=401, payload={"error": "bad bearer"})["kind"], "fatal")

    def test_500_is_fatal(self) -> None:
        self.assertEqual(self._run(status=500, payload={"error": "bad config"})["kind"], "fatal")

    def test_503_is_transient(self) -> None:
        self.assertEqual(self._run(status=503, payload={"error": "try later"})["kind"], "transient")
