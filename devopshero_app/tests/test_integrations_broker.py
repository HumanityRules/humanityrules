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
from collections.abc import Callable
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, patch

import httpx


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
    """Load template_repos/hermes_agent/doh_runtime/integrations/integrations_broker.py as a module.

    The broker imports its sibling modules by bare name. When supervisor.sh runs
    the broker in production, PYTHONPATH includes /opt/doh/runtime/integrations.
    Under pytest we're loading via importlib from the Django repo root, so we
    have to put the integrations dir on sys.path ourselves before exec_module
    triggers the bare imports.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    integrations_dir = repo_root / "template_repos" / "hermes_agent" / "doh_runtime" / "integrations"
    script_path = integrations_dir / "integrations_broker.py"
    if str(integrations_dir) not in sys.path:
        sys.path.insert(0, str(integrations_dir))
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

# Loading the broker put the integrations dir on sys.path and imported the
# sibling modules; bind the ones the tests patch/construct directly.
import credentials_service  # noqa: E402
import doh_client  # noqa: E402
import tls_providers  # noqa: E402


def _make_doh_client() -> doh_client.DohClient:
    """Build a DohClient with the fixed test identity."""
    return doh_client.DohClient(
        control_plane_url="https://doh.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )


class TestEnvironmentFlags(unittest.TestCase):

    def test_merge_flag_defaults_enabled_when_absent(self) -> None:
        with patch.dict(broker.os.environ, {}, clear=True):
            self.assertTrue(broker._env_flag_enabled(name="DOH_MERGE_INTEGRATION_ENABLED", default=True))

    def test_merge_flag_false_values_disable(self) -> None:
        for value in ("0", "false", "no", "off", "FALSE"):
            with patch.dict(broker.os.environ, {"DOH_MERGE_INTEGRATION_ENABLED": value}, clear=True):
                self.assertFalse(broker._env_flag_enabled(name="DOH_MERGE_INTEGRATION_ENABLED", default=True))

    def test_merge_flag_invalid_falls_back_to_default(self) -> None:
        with patch.dict(broker.os.environ, {"DOH_MERGE_INTEGRATION_ENABLED": "wat"}, clear=True):
            self.assertTrue(broker._env_flag_enabled(name="DOH_MERGE_INTEGRATION_ENABLED", default=True))


def _make_token_store() -> broker.tls_intercept._TokenStore:
    """Create a fresh TLS token store for isolated broker tests."""
    return broker.tls_intercept._TokenStore(
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        doh_client=_make_doh_client(),
        refresh_lead_seconds=broker.tls_intercept.REFRESH_LEAD_SECONDS,
    )


def _make_tls_intercept_runtime(ca_dir: pathlib.Path, private_dir: pathlib.Path) -> broker.tls_intercept.TlsInterceptRuntime:
    """Create a fresh TLS-intercept runtime for control-app tests."""
    return broker.tls_intercept.TlsInterceptRuntime(
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        doh_client=_make_doh_client(),
        refresh_lead_seconds=broker.tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
    )


class TestHostToProviderRouting(unittest.TestCase):

    def test_all_google_hosts_route_to_google(self) -> None:
        for host in tls_providers.TLS_INTERCEPT_PROVIDERS["google"].hosts:
            self.assertEqual(tls_providers.HOST_TO_TLS_PROVIDER[host], "google")

    def test_unknown_host_returns_none(self) -> None:
        store = _make_token_store()
        self.assertIsNone(store.provider_for_host(host="api.tavily.com"))
        self.assertIsNone(store.provider_for_host(host="example.com"))

    def test_connect_host_routing_is_case_and_trailing_dot_insensitive(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="GitHub.COM").slug, "github")
        self.assertEqual(store.provider_for_host(host="github.com.").slug, "github")

    def test_openrouter_host_routes_to_openrouter(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="openrouter.ai").slug, "openrouter")

    def test_nous_host_routes_to_nous(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="inference-api.nousresearch.com").slug, "nous")

    def test_openai_host_routes_to_openai(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="api.openai.com").slug, "openai-api")

    def test_anthropic_host_routes_to_anthropic(self) -> None:
        store = _make_token_store()
        self.assertEqual(store.provider_for_host(host="api.anthropic.com").slug, "anthropic")


class TestRewriteAuthorization(unittest.TestCase):

    def test_existing_authorization_is_replaced(self) -> None:
        hdrs = [(b"authorization", b"Bearer SANDBOX-DUMMY"), (b"content-type", b"application/json")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="REAL-TOKEN",
            auth_format=tls_providers.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer REAL-TOKEN")

    def test_missing_authorization_gets_injected(self) -> None:
        hdrs = [(b"content-type", b"application/json")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=tls_providers.AUTH_FORMAT_BEARER,
            upstream_host="gmail.googleapis.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer T")

    def test_host_is_set_to_upstream(self) -> None:
        hdrs = [(b"host", b"whatever"), (b"authorization", b"Bearer x")]
        out = broker.tls_intercept._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=tls_providers.AUTH_FORMAT_BEARER,
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
            auth_format=tls_providers.AUTH_FORMAT_BEARER,
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
            auth_format=tls_providers.AUTH_FORMAT_BASIC_X_ACCESS_TOKEN,
            upstream_host="github.com",
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        expected = b"Basic " + base64.b64encode(b"x-access-token:ghs_real_token")
        self.assertEqual(auth, expected)

    def test_telegram_path_token_is_rewritten_without_authorization_header(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["telegram"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[
                (b"host", b"api.telegram.org"),
                (b"authorization", b"Bearer placeholder"),
                (b"content-type", b"application/json"),
            ],
            path_with_query="/bot000000:DOH_PLACEHOLDER/getUpdates?timeout=20",
            secrets={"bot_token": "123456:REAL"},
            provider=provider,
            upstream_host="api.telegram.org",
        )
        self.assertEqual(path, "/bot123456:REAL/getUpdates?timeout=20")
        header_names = [name.lower() for name, _value in headers]
        self.assertNotIn(b"authorization", header_names)
        self.assertEqual(dict((name.lower(), value) for name, value in headers)[b"host"], b"api.telegram.org")

    def test_telegram_file_path_token_is_rewritten(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["telegram"]
        _headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"api.telegram.org")],
            path_with_query="/file/bot000000:DOH_PLACEHOLDER/documents/file.txt",
            secrets={"bot_token": "123456:REAL"},
            provider=provider,
            upstream_host="api.telegram.org",
        )
        self.assertEqual(path, "/file/bot123456:REAL/documents/file.txt")

    def test_url_rewrite_fails_closed_without_placeholder(self) -> None:
        """A URL-rewrite provider must reject paths missing its placeholder.

        Url-encoded placeholders (e.g. `%3A` instead of `:`) don't substring-match
        and must be rejected so we never forward an un-rewritten URL upstream.
        """
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["telegram"]

        with self.assertRaisesRegex(broker.tls_intercept._SecretSelectionError, "placeholder"):
            broker.tls_intercept._rewrite_request_for_provider(
                headers=[(b"host", b"api.telegram.org")],
                path_with_query="/bot000000%3ADOH_PLACEHOLDER/sendMessage",
                secrets={"bot_token": "123456:REAL"},
                provider=provider,
                upstream_host="api.telegram.org",
            )

    def test_slack_app_token_placeholder_selects_app_token(self) -> None:
        """A request bearing the app-token placeholder gets the real app token."""
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["slack"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xapp-DOH_PLACEHOLDER")],
            path_with_query="/api/apps.connections.open",
            secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
            provider=provider,
            upstream_host="slack.com",
        )
        self.assertEqual(path, "/api/apps.connections.open")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer xapp-REAL")

    def test_slack_bot_token_placeholder_selects_bot_token(self) -> None:
        """A request bearing the bot-token placeholder gets the real bot token."""
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["slack"]
        headers, _path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xoxb-DOH_PLACEHOLDER")],
            path_with_query="/api/chat.postMessage",
            secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
            provider=provider,
            upstream_host="slack.com",
        )
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer xoxb-REAL")

    def test_slack_unknown_placeholder_fails_closed(self) -> None:
        """An unrecognized bearer must raise rather than forward an un-swapped token."""
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["slack"]
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            broker.tls_intercept._rewrite_request_for_provider(
                headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xoxb-NOT-OURS")],
                path_with_query="/api/chat.postMessage",
                secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
                provider=provider,
                upstream_host="slack.com",
            )

    def test_openrouter_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["openrouter"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"openrouter.ai"), (b"authorization", b"Bearer DOH_PLACEHOLDER")],
            path_with_query="/api/v1/chat/completions",
            secrets={"api_key": "sk-or-v1-real"},
            provider=provider,
            upstream_host="openrouter.ai",
        )
        self.assertEqual(path, "/api/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer sk-or-v1-real")

    def test_nous_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["nous"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"inference-api.nousresearch.com"), (b"authorization", b"Bearer DOH_PLACEHOLDER")],
            path_with_query="/v1/chat/completions",
            secrets={"access_token": "nous-access"},
            provider=provider,
            upstream_host="inference-api.nousresearch.com",
        )
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer nous-access")

    def test_openai_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["openai-api"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[(b"host", b"api.openai.com"), (b"authorization", b"Bearer DOH_PLACEHOLDER")],
            path_with_query="/v1/chat/completions",
            secrets={"api_key": "sk-real"},
            provider=provider,
            upstream_host="api.openai.com",
        )
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer sk-real")

    def test_anthropic_placeholder_x_api_key_is_rewritten(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["anthropic"]
        headers, path = broker.tls_intercept._rewrite_request_for_provider(
            headers=[
                (b"host", b"api.anthropic.com"),
                (b"x-api-key", b"DOH_PLACEHOLDER"),
                (b"anthropic-version", b"2023-06-01"),
            ],
            path_with_query="/v1/messages",
            secrets={"api_key": "sk-ant-real"},
            provider=provider,
            upstream_host="api.anthropic.com",
        )
        self.assertEqual(path, "/v1/messages")
        by_name = dict((n.lower(), v) for n, v in headers)
        # Real key swapped in; anthropic-version preserved; no bogus Authorization added.
        self.assertEqual(by_name[b"x-api-key"], b"sk-ant-real")
        self.assertEqual(by_name[b"anthropic-version"], b"2023-06-01")
        self.assertNotIn(b"authorization", by_name)

    def test_anthropic_request_without_placeholder_is_rejected(self) -> None:
        provider = tls_providers.TLS_INTERCEPT_PROVIDERS["anthropic"]
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            broker.tls_intercept._rewrite_request_for_provider(
                headers=[(b"host", b"api.anthropic.com"), (b"x-api-key", b"sk-ant-NOT-OURS")],
                path_with_query="/v1/messages",
                secrets={"api_key": "sk-ant-real"},
                provider=provider,
                upstream_host="api.anthropic.com",
            )


class TestAnonymousRequestClassification(unittest.TestCase):
    """Credential-less vault-style requests pass through; everything else stays on the DOH path.

    The classifier runs before the token-store lookup, so an anonymous
    request (False) must never cost a DOH refresh, a credentialed request
    (True) follows the rewrite path, and an unrecognized credential raises
    so BYO keys are never forwarded.
    """

    def _classify(self, *, slug: str, headers: list[tuple[bytes, bytes]], path: str) -> bool:
        return broker.tls_intercept._request_addresses_doh_credential(
            headers=headers,
            path_with_query=path,
            provider=tls_providers.TLS_INTERCEPT_PROVIDERS[slug],
        )

    def test_openrouter_request_without_authorization_is_anonymous(self) -> None:
        """The webui's public /api/v1/models fetch carries no Authorization — pass through."""
        self.assertFalse(self._classify(
            slug="openrouter",
            headers=[(b"host", b"openrouter.ai"), (b"accept", b"application/json")],
            path="/api/v1/models",
        ))

    def test_openrouter_placeholder_bearer_addresses_doh_credential(self) -> None:
        self.assertTrue(self._classify(
            slug="openrouter",
            headers=[(b"authorization", b"Bearer DOH_PLACEHOLDER")],
            path="/api/v1/chat/completions",
        ))

    def test_openrouter_unknown_bearer_is_rejected(self) -> None:
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            self._classify(
                slug="openrouter",
                headers=[(b"authorization", b"Bearer sk-or-v1-byo-key")],
                path="/api/v1/chat/completions",
            )

    def test_slack_request_without_authorization_is_anonymous(self) -> None:
        self.assertFalse(self._classify(
            slug="slack",
            headers=[(b"host", b"slack.com")],
            path="/api/api.test",
        ))

    def test_anthropic_request_without_credentials_is_anonymous(self) -> None:
        self.assertFalse(self._classify(
            slug="anthropic",
            headers=[(b"host", b"api.anthropic.com"), (b"anthropic-version", b"2023-06-01")],
            path="/v1/models",
        ))

    def test_anthropic_placeholder_x_api_key_addresses_doh_credential(self) -> None:
        self.assertTrue(self._classify(
            slug="anthropic",
            headers=[(b"x-api-key", b"DOH_PLACEHOLDER")],
            path="/v1/messages",
        ))

    def test_anthropic_foreign_x_api_key_is_rejected(self) -> None:
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            self._classify(
                slug="anthropic",
                headers=[(b"x-api-key", b"sk-ant-byo-key")],
                path="/v1/messages",
            )

    def test_anthropic_byo_authorization_bearer_is_rejected(self) -> None:
        """No x-api-key but an Authorization header (e.g. BYO OAuth bearer) must not pass through."""
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            self._classify(
                slug="anthropic",
                headers=[(b"authorization", b"Bearer byo-oauth-token")],
                path="/v1/messages",
            )

    def test_oauth_providers_address_doh_credential_even_without_authorization(self) -> None:
        """OAuth-style requests are implicitly ours — the proxy injects unconditionally."""
        for slug in ("google", "github", "nous", "openai-codex"):
            self.assertTrue(self._classify(
                slug=slug,
                headers=[(b"host", b"upstream.example")],
                path="/anything",
            ))

    def test_telegram_placeholder_path_addresses_doh_credential(self) -> None:
        self.assertTrue(self._classify(
            slug="telegram",
            headers=[(b"host", b"api.telegram.org")],
            path="/bot000000:DOH_PLACEHOLDER/getUpdates",
        ))

    def test_telegram_path_without_placeholder_is_rejected(self) -> None:
        """Every Bot API path embeds a token, so telegram has no anonymous surface."""
        with self.assertRaises(broker.tls_intercept._SecretSelectionError):
            self._classify(
                slug="telegram",
                headers=[(b"host", b"api.telegram.org")],
                path="/bot123456:BYO-TOKEN/sendMessage",
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

    def __init__(self, *, refresh_payload: dict) -> None:
        self._refresh_payload = refresh_payload
        self.refresh_calls = 0

    async def status_items(self) -> list:
        return []

    def routes(self, prefix: str) -> list:
        return []

    async def refresh_catalog(self) -> dict:
        self.refresh_calls += 1
        return self._refresh_payload


class _StubDeviceFlow:
    """Minimal device-flow surface for control-app tests."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.statused: list[str] = []
        self.cancelled: list[str] = []

    async def start(self, provider_slug: str) -> dict:
        self.started.append(provider_slug)
        return {"provider": provider_slug, "phase": "pending"}

    async def status(self, provider_slug: str) -> dict:
        self.statused.append(provider_slug)
        return {"provider": provider_slug, "phase": "pending"}

    async def cancel(self, provider_slug: str) -> None:
        self.cancelled.append(provider_slug)
        return None


def _ready_stub_aggregator() -> _StubAggregator:
    """Stub aggregator for tests that don't exercise the refresh route."""
    return _StubAggregator(refresh_payload={"ok": True, "tools": 0, "connectors": 0})


def _make_control_parts(
    tls_intercept_runtime: "broker.tls_intercept.TlsInterceptRuntime",
    aggregator: _StubAggregator,
    gateway_env_path: pathlib.Path,
    webui_state_dir: pathlib.Path,
) -> types.SimpleNamespace:
    """Wire a control app + CredentialsService the way the broker does at startup."""
    client = _make_doh_client()
    service = credentials_service.CredentialsService(
        doh_client=client,
        tls_intercept_runtime=tls_intercept_runtime,
        mcp_aggregator=aggregator,
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        gateway_env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url="http://127.0.0.1:9999",
        webui_python=pathlib.Path("/nonexistent/webui-python"),
        runtime_dir=pathlib.Path("/nonexistent/doh-runtime"),
        hermes_home=pathlib.Path("/nonexistent/hermes-home"),
    )
    device_stub = _StubDeviceFlow()
    app = broker.control_api.build_control_app(
        mcp_aggregator=aggregator,
        tls_intercept_runtime=tls_intercept_runtime,
        oauth_device_flow=device_stub,
        credentials_service=service,
        doh_client=client,
        env_slug="default",
    )
    return types.SimpleNamespace(app=app, service=service, doh_client=client, device_flow=device_stub)


def _make_credentials_service(
    tls_intercept_runtime: "broker.tls_intercept.TlsInterceptRuntime",
    gateway_env_path: pathlib.Path,
    webui_state_dir: pathlib.Path,
) -> credentials_service.CredentialsService:
    """Build a CredentialsService over a stub aggregator for choreography tests."""
    return credentials_service.CredentialsService(
        doh_client=_make_doh_client(),
        tls_intercept_runtime=tls_intercept_runtime,
        mcp_aggregator=_ready_stub_aggregator(),
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        gateway_env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url="http://127.0.0.1:9999",
        webui_python=pathlib.Path("/nonexistent/webui-python"),
        runtime_dir=pathlib.Path("/nonexistent/doh-runtime"),
        hermes_home=pathlib.Path("/nonexistent/hermes-home"),
    )


class TestControlIntegrations(unittest.IsolatedAsyncioTestCase):
    """/integrations renders from cache; it MUST NOT call DOH on the status path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.tls_intercept_runtime = _make_tls_intercept_runtime(ca_dir=root / "ca", private_dir=root / "private")
        self.gateway_env_path = root / "hermes.env"
        self.webui_state_dir = root / "webui-state"

    def _control_parts(self, aggregator: _StubAggregator) -> types.SimpleNamespace:
        return _make_control_parts(
            tls_intercept_runtime=self.tls_intercept_runtime,
            aggregator=aggregator,
            gateway_env_path=self.gateway_env_path,
            webui_state_dir=self.webui_state_dir,
        )

    async def test_get_integrations_reads_cache_without_calling_doh(self) -> None:
        """Status reads never call DOH; connected items come from the pre-warmed cache."""
        from starlette.testclient import TestClient

        app = self._control_parts(aggregator=_ready_stub_aggregator()).app

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "fresh-token"},
                expires_in=3600,
                config={},
                metadata={},
            )),
        ) as fetch_mock:
            await self.tls_intercept_runtime.refresh_slug(slug="google")
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
            await self.tls_intercept_runtime._token_store.token_for_host(host="gmail.googleapis.com"),
            "fresh-token",
        )

    async def test_model_provider_status_marks_model_picker_affecting_items(self) -> None:
        """The WebUI extension refreshes model dropdowns only for LLM providers."""
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}

        self.assertFalse(items_by_slug["google"]["affects_model_picker"])
        self.assertFalse(items_by_slug["github"]["affects_model_picker"])
        self.assertFalse(items_by_slug["telegram"]["affects_model_picker"])
        self.assertFalse(items_by_slug["slack"]["affects_model_picker"])
        self.assertTrue(items_by_slug["openai-codex"]["affects_model_picker"])
        self.assertTrue(items_by_slug["nous"]["affects_model_picker"])
        self.assertTrue(items_by_slug["openrouter"]["affects_model_picker"])
        self.assertTrue(items_by_slug["openai-api"]["affects_model_picker"])
        self.assertTrue(items_by_slug["anthropic"]["affects_model_picker"])

    async def test_absent_provider_is_not_cached(self) -> None:
        """An `absent` outcome from DOH must remove (not store) the cache entry."""
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_intercept_runtime._token_store._cache)
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")

    async def test_refresh_slug_refetches_even_when_cache_is_fresh(self) -> None:
        """Explicit refresh must hit DOH even when the cached token is still fresh."""
        responses = [
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"}, expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T2"}, expires_in=3600, config={}, metadata={},
            )),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=responses) as fetch_mock:
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(self.tls_intercept_runtime._token_store._cache["google"].secrets, {"access_token": "T2"})

    async def test_transient_after_eviction_does_not_fabricate_entry(self) -> None:
        """A transient refresh outcome must not write a sentinel into an empty cache."""
        responses = [
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"}, expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ]
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=responses):
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            await self.tls_intercept_runtime._token_store.invalidate(slug="google")
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_intercept_runtime._token_store._cache)

    async def test_transient_during_lead_window_keeps_serving_cached_token(self) -> None:
        """Refresh-ahead transient failure must NOT make the proxy say "not connected"
        when the cached token is unfresh (inside the lead window) but still un-expired.

        Without this, a DOH hiccup during the final `refresh_lead_seconds` of
        an access_token's life would surface as "not connected" to the
        sandbox even though we hold a usable token. The proxy should keep
        serving the cached token for the rest of its expires_at window.
        """
        import time
        store = self.tls_intercept_runtime._token_store
        # Seed an entry inside the lead window (lead is 300s; this has 120s left).
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            secrets={"access_token": "STILL-VALID"},
            expires_at=time.monotonic() + 120,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertEqual(token, "STILL-VALID")
        # And the cache entry survives the failed refresh-ahead.
        self.assertEqual(store._cache["google"].secrets, {"access_token": "STILL-VALID"})

    async def test_invalidate_races_with_inflight_refresh(self) -> None:
        """Invalidate must serialize behind an in-flight refresh for the same slug.

        Without the store lock around fetch+apply, this sequence used to
        silently lose the invalidate:
          1. Proxy hot path's `_ensure_fresh` starts the DOH refresh
             fetch (slow).
          2. User clicks Disconnect → `invalidate(slug)` clears the cache.
          3. Proxy's in-flight fetch resolves and writes a (now stale)
             entry back into the cache.
          4. The service's post-invalidate `refresh_slug` → `_ensure_fresh`
             reads the fresh-looking stale entry and returns without
             refetching.

        Correct behavior: invalidate waits for the in-flight refresh, the
        stale write lands, invalidate pops it, and the service's refresh
        starts from an empty cache and re-asks DOH.
        """
        fetch_calls: list[str] = []
        started = asyncio.Event()
        delayed = asyncio.Event()

        async def first_stale(doh_client: object, slugs: list[str]) -> object:
            fetch_calls.append("first")
            started.set()
            await asyncio.wait_for(delayed.wait(), timeout=5)
            return _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "STALE-IN-FLIGHT"}, expires_in=3600,
                config={}, metadata={},
            ))

        async def second_absent(doh_client: object, slugs: list[str]) -> object:
            fetch_calls.append("second")
            return _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ))

        fetches = [first_stale, second_absent]
        idx = 0
        async def dispatch(*args: object, **kwargs: object) -> object:
            nonlocal idx
            fn = fetches[min(idx, len(fetches) - 1)]
            idx += 1
            return await fn(*args, **kwargs)

        store = self.tls_intercept_runtime._token_store
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=dispatch):
            proxy_task = asyncio.create_task(store.token_for_host(host="gmail.googleapis.com"))
            await asyncio.wait_for(started.wait(), timeout=5)
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0)  # let invalidate queue on the store lock
            delayed.set()  # release the proxy's in-flight refresh
            await proxy_task
            await invalidate_task

            # Service step: after a disconnect the service calls refresh. It
            # must see an empty cache and re-ask DOH (second_absent fires here).
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
        delayed = asyncio.Event()
        started = asyncio.Event()

        async def parked_has_token(doh_client: object, slugs: list[str]) -> object:
            started.set()
            await asyncio.wait_for(delayed.wait(), timeout=5)
            return {
                slug: broker.tls_intercept.RefreshResult(
                    outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                    secrets={"access_token": f"STALE-{slug}"}, expires_in=3600,
                    config={}, metadata={},
                )
                for slug in slugs
            }

        store = self.tls_intercept_runtime._token_store
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", side_effect=parked_has_token):
            refresh_all_task = asyncio.create_task(store.refresh_all())
            await asyncio.wait_for(started.wait(), timeout=5)
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0)  # let invalidate queue on the store lock
            delayed.set()
            await refresh_all_task
            await invalidate_task

        # google's STALE write landed during refresh_all, then invalidate
        # popped it. github + telegram were untouched by the invalidate
        # so their refresh_all writes survive.
        self.assertNotIn("google", store._cache)
        self.assertEqual(store._cache["github"].secrets, {"access_token": "STALE-github"})
        self.assertEqual(store._cache["telegram"].secrets, {"access_token": "STALE-telegram"})

    async def test_transient_with_expired_cache_returns_none(self) -> None:
        """A transient refresh on a cache entry that's already past expires_at
        must return None — we don't hand the proxy an expired token just
        because the cache happens to still hold one.
        """
        import time
        store = self.tls_intercept_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertIsNone(token)

    async def test_status_items_prunes_expired_entry_before_render(self) -> None:
        """Status reads must prune expired entries before projecting connected state."""
        import time
        store = self.tls_intercept_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
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
        store = self.tls_intercept_runtime._token_store
        store._cache["telegram"] = broker.tls_intercept._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={"bot_token": "STALE"},
            metadata={},
        )

        snapshot = await self.tls_intercept_runtime.gateway_env_snapshot()
        slugs = [provider.slug for provider, _config in snapshot]
        self.assertNotIn("telegram", slugs)
        self.assertNotIn("telegram", store._cache)

    async def test_proxy_hot_path_prunes_expired_entry_after_transient(self) -> None:
        """Transient refresh on an expired entry must leave the cache empty."""
        import time
        store = self.tls_intercept_runtime._token_store
        store._cache["google"] = broker.tls_intercept._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            token = await store.token_for_host(host="gmail.googleapis.com")

        self.assertIsNone(token)
        self.assertNotIn("google", store._cache)

    async def test_refresh_endpoint_reloads_catalog_and_drops_tls_cache(self) -> None:
        """POST /integrations/refresh_all fans out catalog reload + all-providers TLS invalidate."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(refresh_payload={"ok": True, "tools": 12, "connectors": 3})
        parts = self._control_parts(aggregator=aggregator)

        google_connected = _batched(slug="google", result=broker.tls_intercept.RefreshResult(
            outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"access_token": "fresh-token"},
            expires_in=3600,
            config={},
            metadata={},
        ))
        absent_for_every_slug = {
            slug: broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_providers.TLS_INTERCEPT_PROVIDERS
        }
        # First call pre-warms google; the route's invalidate-all then refreshes
        # every provider from DOH, which reports them all disconnected.
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            side_effect=[google_connected, absent_for_every_slug],
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            self.assertIn("google", self.tls_intercept_runtime._token_store._cache)
            with TestClient(parts.app) as client:
                resp = client.post("/integrations/refresh_all")
                # A successful refresh arms the service-owned cooldown: an
                # immediate second press is rejected without touching the
                # catalog or the TLS cache again.
                resp_on_cooldown = client.post("/integrations/refresh_all")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "tools": 12, "connectors": 3})
        self.assertEqual(aggregator.refresh_calls, 1)
        self.assertEqual(self.tls_intercept_runtime._token_store._cache, {})
        self.assertEqual(resp_on_cooldown.status_code, 429)
        self.assertEqual(resp_on_cooldown.json()["error"], "refresh_cooldown")
        self.assertEqual(aggregator.refresh_calls, 1)

    async def test_refresh_endpoint_cooldown_skips_tls_invalidate(self) -> None:
        """Cooldown 429 must short-circuit before invalidate_all fires (would kick the gateway)."""
        import time
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(refresh_payload={"ok": True, "tools": 0, "connectors": 0})
        parts = self._control_parts(aggregator=aggregator)
        # 13s into the 30s cooldown window ⇒ int(30 - 13.x) + 1 = 17s left.
        parts.service._last_refresh_all_ts = time.time() - 13

        with patch.object(
            parts.service._tls_intercept_runtime,
            "invalidate_all",
            new_callable=AsyncMock,
        ) as invalidate_all_mock:
            with TestClient(parts.app) as client:
                resp = client.post("/integrations/refresh_all")

        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json(), {"error": "refresh_cooldown", "retry_after_seconds": 17})
        invalidate_all_mock.assert_not_awaited()
        self.assertEqual(aggregator.refresh_calls, 0)

    async def test_refresh_endpoint_surfaces_gateway_restart_failure(self) -> None:
        """Catalog refresh succeeded but TLS invalidate's gateway restart failed -> 502 with error."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(refresh_payload={"ok": True, "tools": 1, "connectors": 1})
        parts = self._control_parts(aggregator=aggregator)

        with patch.object(
            parts.service,
            "_refresh_and_apply",
            new_callable=AsyncMock,
            side_effect=RuntimeError("gateway restart failed (process-compose returned 502)"),
        ):
            with TestClient(parts.app) as client:
                resp = client.post("/integrations/refresh_all")

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("gateway restart failed", body["error"])

    async def test_provider_invalidate_endpoint_drops_one_provider_cache(self) -> None:
        """POST /integrations/tls_intercept/{provider}/invalidate evicts one provider."""
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator())

        with patch.object(parts.service, "credentials_invalidate", new_callable=AsyncMock) as invalidate_mock:
            with patch.object(
                parts.service._tls_intercept_runtime,
                "invalidate_all",
                new_callable=AsyncMock,
            ) as invalidate_all_mock:
                with TestClient(parts.app) as client:
                    resp = client.post("/integrations/tls_intercept/github/invalidate")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "github")
        invalidate_mock.assert_awaited_once_with(slug="github")
        invalidate_all_mock.assert_not_awaited()

    async def test_device_routes_are_provider_keyed(self) -> None:
        """Device start/status/cancel routes dispatch by provider slug."""
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator())
        device_stub = parts.device_flow
        app = parts.app

        with TestClient(app) as client:
            start_resp = client.post("/integrations/tls_intercept/nous/device/start")
            status_resp = client.get("/integrations/tls_intercept/nous/device/status")
            cancel_resp = client.post("/integrations/tls_intercept/nous/device/cancel")

        self.assertEqual(start_resp.status_code, 200)
        self.assertEqual(start_resp.json()["provider"], "nous")
        self.assertEqual(status_resp.json()["provider"], "nous")
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(device_stub.started, ["nous"])
        self.assertEqual(device_stub.statused, ["nous"])
        self.assertEqual(device_stub.cancelled, ["nous"])

    async def test_vault_setup_session_requests_submit_token_from_doh(self) -> None:
        """POST /integrations/tls_intercept/{provider}/setup-session asks DOH for a submit token.

        The owner/app identity rides inside DohClient.post_json (see
        TestDohClient), so the service only supplies the provider fields.
        """
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator())

        with patch.object(
            parts.doh_client,
            "post_json",
            return_value=(200, {"submit_token": "signed-token"}),
        ) as post_mock:
            with TestClient(parts.app) as client:
                resp = client.post("/integrations/tls_intercept/telegram/setup-session?origin=https%3A%2F%2Fhermes.dev.example.com")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["submit_token"], "signed-token")
        post_mock.assert_called_once_with(
            path="/api/integrations/credentials/setup-session",
            payload={
                "provider": "telegram",
                "public_origin": "https://hermes.dev.example.com",
            },
            timeout_seconds=30,
        )

    async def test_vault_disconnect_invalidates_only_provider_cache_on_success(self) -> None:
        """POST /integrations/tls_intercept/{provider}/disconnect evicts only that provider (vault)."""
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator())

        with patch.object(parts.doh_client, "post_json", return_value=(200, {"ok": True})) as post_mock:
            with patch.object(parts.service, "credentials_invalidate", new_callable=AsyncMock) as invalidate_mock:
                with patch.object(
                    parts.service._tls_intercept_runtime,
                    "invalidate_all",
                    new_callable=AsyncMock,
                ) as invalidate_all_mock:
                    with TestClient(parts.app) as client:
                        resp = client.post("/integrations/tls_intercept/telegram/disconnect")

        self.assertEqual(resp.status_code, 200)
        post_mock.assert_called_once_with(
            path="/api/integrations/credentials/disconnect",
            payload={"provider": "telegram"},
            timeout_seconds=30,
        )
        invalidate_mock.assert_awaited_once_with(slug="telegram")
        invalidate_all_mock.assert_not_awaited()

    async def test_oauth_disconnect_invalidates_only_provider_cache_on_success(self) -> None:
        """POST /integrations/tls_intercept/{provider}/disconnect evicts only that provider (OAuth)."""
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator())

        with patch.object(parts.doh_client, "post_json", return_value=(200, {"ok": True})) as post_mock:
            with patch.object(parts.service, "credentials_invalidate", new_callable=AsyncMock) as invalidate_mock:
                with TestClient(parts.app) as client:
                    resp = client.post("/integrations/tls_intercept/github/disconnect")

        self.assertEqual(resp.status_code, 200)
        # OAuth and vault disconnects now share one broker path and one DOH
        # endpoint; DOH resolves the provider kind and revokes upstream for
        # OAuth providers server-side.
        post_mock.assert_called_once_with(
            path="/api/integrations/credentials/disconnect",
            payload={"provider": "github"},
            timeout_seconds=30,
        )
        invalidate_mock.assert_awaited_once_with(slug="github")


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
                secrets={"access_token": "T1"},
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
                secrets={"access_token": "T1"},
                expires_in=3600,
                config={},
                metadata={},
            )),
            _batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T2"},
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
                secrets=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            self.assertIsNone(await self.token_store.token_for_host(host="gmail.googleapis.com"))
        self.assertNotIn("google", self.token_store._cache)


def _patched_doh_httpx_client(handler: Callable[[httpx.Request], httpx.Response], timeouts: list[int]) -> AbstractContextManager:
    """Patch doh_client's httpx.AsyncClient with a MockTransport-backed factory; records each client timeout."""
    # `doh_client.httpx` is the global httpx module, so the factory must hold
    # the real class — referencing `httpx.AsyncClient` inside it would resolve
    # to the patched attribute (itself).
    real_async_client = httpx.AsyncClient

    def make_client(timeout: int) -> httpx.AsyncClient:
        timeouts.append(timeout)
        return real_async_client(transport=httpx.MockTransport(handler), timeout=timeout)

    return patch.object(doh_client.httpx, "AsyncClient", make_client)


class TestDohClient(unittest.IsolatedAsyncioTestCase):
    """DohClient owns the bearer and merges the owner/app identity into every payload."""

    async def test_post_json_merges_identity_and_sends_bearer(self) -> None:
        import json as _json
        captured: dict = {}
        timeouts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(status_code=200, json={"ok": True})

        with _patched_doh_httpx_client(handler=handler, timeouts=timeouts):
            status, payload = await _make_doh_client().post_json(
                path="/api/integrations/credentials/disconnect",
                payload={"provider": "telegram"},
                timeout_seconds=30,
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(timeouts, [30])
        request = captured["request"]
        self.assertEqual(str(request.url), "https://doh.example/api/integrations/credentials/disconnect")
        self.assertEqual(request.headers["Authorization"], "Bearer env-bearer")
        self.assertEqual(
            _json.loads(request.content.decode("utf-8")),
            {"owner_username": "vmendi", "app_slug": "hermes", "provider": "telegram"},
        )

    async def test_network_error_maps_to_synthetic_502(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_doh_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 502)
        self.assertIn("error", payload)

    async def test_unparseable_success_body_maps_to_synthetic_502(self) -> None:
        """A 2xx with a non-JSON body degrades to the synthetic 502 — callers never see a parse error."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"<html>not json</html>")

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_doh_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 502)
        self.assertEqual(payload, {"error": "control plane request failed"})

    async def test_unparseable_error_body_keeps_real_status(self) -> None:
        """A non-2xx with a non-JSON body keeps its real status so callers can branch on it."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b"<html>maintenance</html>")

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_doh_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"error": "control plane returned HTTP 503"})


class TestFetchProviderTokensBatch(unittest.IsolatedAsyncioTestCase):
    """Parse DOH's `/api/integrations/tokens` response into a slug→RefreshResult map."""

    async def _run_with_response(self, payload: dict) -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=payload)

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            return await broker.tls_intercept.fetch_provider_tokens_batch(
                doh_client=_make_doh_client(),
                slugs=["google", "github", "telegram"],
            )

    async def test_mixed_outcomes_parse_per_slug(self) -> None:
        results = await self._run_with_response(payload={
            "results": {
                "google": {
                    "outcome": "has_token",
                    "secrets": {"access_token": "g-abc"},
                    "expires_in": 3600,
                    "config": {},
                    "metadata": {},
                },
                "github": {"outcome": "absent"},
                "telegram": {"outcome": "transient"},
            },
        })

        self.assertEqual(results["google"].outcome, broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN)
        self.assertEqual(results["google"].secrets, {"access_token": "g-abc"})
        self.assertEqual(results["google"].expires_in, 3600)
        self.assertEqual(results["github"].outcome, broker.tls_intercept.REFRESH_OUTCOME_ABSENT)
        self.assertEqual(results["telegram"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    async def test_telegram_config_and_metadata_pass_through(self) -> None:
        results = await self._run_with_response(payload={
            "results": {
                "google": {"outcome": "absent"},
                "github": {"outcome": "absent"},
                "telegram": {
                    "outcome": "has_token",
                    "secrets": {"bot_token": "123:REAL"},
                    "expires_in": 3600,
                    "config": {"allowed_users": ["42", "7"]},
                    "metadata": {"bot_username": "doh_bot"},
                },
            },
        })
        self.assertEqual(results["telegram"].config, {"allowed_users": ["42", "7"]})
        self.assertEqual(results["telegram"].metadata, {"bot_username": "doh_bot"})

    async def test_slug_missing_from_response_is_transient(self) -> None:
        """A partial server response must NOT clear the broker's cache for the missing slug."""
        results = await self._run_with_response(payload={"results": {"google": {"outcome": "absent"}}})
        self.assertEqual(results["github"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)
        self.assertEqual(results["telegram"].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    async def test_has_token_with_missing_or_malformed_secrets_is_transient(self) -> None:
        """A has_token entry without a usable secrets map must degrade to transient,
        not crash or coerce a non-string value into a literal bearer token.
        """
        for bad_secrets in ({}, {"access_token": None}, {"access_token": ""}, {"": "tok"}, "nope"):
            results = await self._run_with_response(payload={
                "results": {
                    "google": {"outcome": "has_token", "secrets": bad_secrets, "expires_in": 3600},
                    "github": {"outcome": "absent"},
                    "telegram": {"outcome": "absent"},
                },
            })
            self.assertEqual(
                results["google"].outcome,
                broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                msg=f"bad_secrets={bad_secrets!r} should be transient",
            )
            self.assertIsNone(results["google"].secrets)

    async def test_network_error_returns_transient_for_every_slug(self) -> None:
        """Any transport failure must surface as transient across the board, preserving the cache."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            results = await broker.tls_intercept.fetch_provider_tokens_batch(
                doh_client=_make_doh_client(),
                slugs=["google", "github", "telegram"],
            )
        for slug in ("google", "github", "telegram"):
            self.assertEqual(results[slug].outcome, broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT)

    async def test_http_error_returns_transient_for_every_slug(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, content=b"x")

        with _patched_doh_httpx_client(handler=handler, timeouts=[]):
            results = await broker.tls_intercept.fetch_provider_tokens_batch(
                doh_client=_make_doh_client(),
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
            secrets={"access_token": "PRIOR-GITHUB"},
            expires_at=time.monotonic() + 600,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        batched_results = {
            "google": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "G"}, expires_in=3600, config={}, metadata={},
            ),
            "github": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "telegram": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "slack": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "openai-codex": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "openrouter": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "nous": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "openai-api": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
            "anthropic": broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ),
        }
        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=batched_results,
        ) as batch_mock:
            await store.refresh_all()

        self.assertEqual(batch_mock.call_count, 1)
        self.assertEqual(store._cache["google"].secrets, {"access_token": "G"})
        self.assertEqual(store._cache["github"].secrets, {"access_token": "PRIOR-GITHUB"})  # transient ⇒ preserved
        self.assertNotIn("telegram", store._cache)


class TestGatewayEnvRender(unittest.TestCase):
    """Render the DOH-managed block from a token-store snapshot."""

    def _telegram_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["telegram"]

    def _google_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["google"]

    def _github_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["github"]

    def _slack_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["slack"]

    def _openrouter_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["openrouter"]

    def _openai_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["openai-api"]

    def _anthropic_provider(self) -> "tls_providers.TlsProviderSpec":
        return tls_providers.TLS_INTERCEPT_PROVIDERS["anthropic"]

    def test_slack_vault_header_provider_renders_both_placeholders(self) -> None:
        """Env bindings render static placeholders plus list config."""
        snapshot = [(self._slack_provider(), {"allowed_users": ["U1", "U2"], "home_channel": "DOWNER"})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("SLACK_APP_TOKEN=xapp-DOH_PLACEHOLDER", block)
        self.assertIn("SLACK_BOT_TOKEN=xoxb-DOH_PLACEHOLDER", block)
        self.assertIn("SLACK_ALLOWED_USERS=U1,U2", block)
        # Personal mode resolves the owner DM as the home channel.
        self.assertIn("SLACK_HOME_CHANNEL=DOWNER", block)

    def test_slack_company_wide_renders_allow_all_flag(self) -> None:
        """Company-wide config (allow_all_users) renders SLACK_ALLOW_ALL_USERS=true.

        The upstream gateway denies users by default, so this flag is what
        lets anyone in an invited channel drive a company-wide bot.
        """
        snapshot = [(self._slack_provider(), {"workspace_scope": "company_wide", "allow_all_users": "true"})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("SLACK_ALLOW_ALL_USERS=true", block)
        # No per-user allowlist in company-wide mode.
        self.assertNotIn("SLACK_ALLOWED_USERS=", block)
        # No single owner to DM, so no home channel is rendered.
        self.assertNotIn("SLACK_HOME_CHANNEL=", block)

    def test_connected_vault_provider_renders_managed_block(self) -> None:
        snapshot = [(self._telegram_provider(), {"allowed_users": [42, 7]})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn(credentials_service.GATEWAY_ENV_BLOCK_BEGIN, block)
        self.assertIn(credentials_service.GATEWAY_ENV_BLOCK_END, block)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", block)
        self.assertIn("TELEGRAM_ALLOWED_USERS=42,7", block)

    def test_empty_snapshot_renders_empty_block(self) -> None:
        """No connected providers ⇒ no managed block at all (disconnected = absent)."""
        self.assertEqual(credentials_service._render_managed_block(snapshot=[]), "")

    def test_oauth_provider_contributes_no_env_lines(self) -> None:
        """Google stays env-free; its helper injects a subprocess-local sentinel."""
        snapshot = [(self._google_provider(), {"some_key": "some_value"})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertEqual(block, "")

    def test_connected_github_renders_github_placeholder(self) -> None:
        """GitHub env appears only through the connected-provider snapshot."""
        snapshot = [(self._github_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("GITHUB_TOKEN=DOH_PLACEHOLDER", block)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", block)

    def test_connected_openrouter_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._openrouter_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("OPENROUTER_API_KEY=DOH_PLACEHOLDER", block)

    def test_connected_openai_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._openai_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("OPENAI_API_KEY=DOH_PLACEHOLDER", block)

    def test_connected_anthropic_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._anthropic_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("ANTHROPIC_API_KEY=DOH_PLACEHOLDER", block)

    def test_missing_list_config_skips_binding(self) -> None:
        """A connected provider without the optional list field omits its env var."""
        snapshot = [(self._telegram_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", block)
        self.assertNotIn("TELEGRAM_ALLOWED_USERS", block)

    def test_write_preserves_outside_lines_and_replaces_managed_block(self) -> None:
        """Lines outside the sentinel block survive; the block is fully replaced."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = pathlib.Path(tmp) / ".env"
            env_path.write_text(
                "USER_KEY=keep-me\n"
                f"{credentials_service.GATEWAY_ENV_BLOCK_BEGIN}\n"
                "STALE_VAR=old-value\n"
                f"{credentials_service.GATEWAY_ENV_BLOCK_END}\n"
                "ANOTHER=also-keep\n",
                encoding="utf-8",
            )
            snapshot = [(self._telegram_provider(), {"allowed_users": [1]})]
            block = credentials_service._render_managed_block(snapshot=snapshot)
            changed = credentials_service._write_gateway_env_file(env_path=env_path, managed_block=block)
            text = env_path.read_text(encoding="utf-8")

        self.assertTrue(changed)
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
                f"{credentials_service.GATEWAY_ENV_BLOCK_BEGIN}\n"
                "TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER\n"
                f"{credentials_service.GATEWAY_ENV_BLOCK_END}\n",
                encoding="utf-8",
            )
            changed = credentials_service._write_gateway_env_file(env_path=env_path, managed_block="")
            text = env_path.read_text(encoding="utf-8")

        self.assertTrue(changed)
        self.assertEqual(text, "USER_KEY=keep-me\n")

    def test_write_identical_block_reports_no_change(self) -> None:
        """The broker uses this to avoid unnecessary process restarts."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = pathlib.Path(tmp) / ".env"
            snapshot = [(self._github_provider(), {})]
            block = credentials_service._render_managed_block(snapshot=snapshot)
            first_changed = credentials_service._write_gateway_env_file(env_path=env_path, managed_block=block)
            second_changed = credentials_service._write_gateway_env_file(env_path=env_path, managed_block=block)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)


class TestCredentialsServiceChoreography(unittest.IsolatedAsyncioTestCase):
    """User-initiated invalidate triggers env render + restart for vault providers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = pathlib.Path(self.tmp.name) / "hermes.env"
        self.webui_state_dir = pathlib.Path(self.tmp.name) / "webui-state"

    def _make_runtime(self) -> "broker.tls_intercept.TlsInterceptRuntime":
        root = pathlib.Path(self.tmp.name)
        return _make_tls_intercept_runtime(ca_dir=root / "ca", private_dir=root / "private")

    def _make_service(self) -> credentials_service.CredentialsService:
        return _make_credentials_service(
            tls_intercept_runtime=self._make_runtime(),
            gateway_env_path=self.env_path,
            webui_state_dir=self.webui_state_dir,
        )

    async def test_runtime_invalidate_is_pure_cache_drop(self) -> None:
        """The TLS runtime fans out no side effects on invalidate.

        Credential-change choreography (DOH refresh, env render, process
        restarts) belongs to CredentialsService. Both the runtime's public
        invalidate and the proxy 401-eviction path (inner token store) only
        touch the cache, so a 401-eviction during normal traffic can never
        trigger a gateway restart.
        """
        runtime = self._make_runtime()
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch") as fetch_mock:
            await runtime.invalidate(slug="telegram")
            await runtime.invalidate_all()
            await runtime._token_store.invalidate(slug="telegram")
        fetch_mock.assert_not_called()

    async def test_processes_requiring_restart_follow_provider_specs(self) -> None:
        """Vault env restarts gateway; GitHub placeholder env restarts WebUI."""
        service = self._make_service()
        gateway = credentials_service.GATEWAY_PROCESS_NAME
        webui = credentials_service.WEBUI_PROCESS_NAME
        self.assertEqual(service._processes_requiring_restart(slug="telegram"), (gateway,))
        self.assertEqual(service._processes_requiring_restart(slug="github"), (webui,))
        self.assertEqual(service._processes_requiring_restart(slug="openrouter"), (gateway, webui))
        self.assertEqual(service._processes_requiring_restart(slug="google"), ())
        self.assertEqual(service._processes_requiring_restart(slug="nous"), ())
        # Unknown slug: don't restart.
        self.assertEqual(service._processes_requiring_restart(slug="bogus"), ())
        # None (Refresh-all) covers every restart-declaring provider in scope.
        self.assertEqual(service._processes_requiring_restart(slug=None), (gateway, webui))

    async def test_per_slug_invalidate_refreshes_only_that_slug(self) -> None:
        """Slug-targeted invalidate must NOT fan out to disconnected providers.

        Connecting one provider used to spam DOH with `no integration row`
        404s for every other (still disconnected) provider. The service
        narrows to `refresh_slug(slug)` when a slug is named, so DOH only
        hears about the one that actually changed.
        """
        service = self._make_service()

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "t"}, expires_in=3600, config={}, metadata={},
            )),
        ) as fetch_mock:
            await service.credentials_invalidate(slug="google")

        # Exactly one DOH round-trip and only for the named slug, not one per provider.
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(fetch_mock.call_args.kwargs["slugs"], ["google"])

    async def test_github_invalidate_rewrites_env_and_restarts_webui(self) -> None:
        """GitHub connect/disconnect reloads WebUI so provider env is re-read."""
        service = self._make_service()
        self.webui_state_dir.mkdir(parents=True)
        models_cache = self.webui_state_dir / "models_cache.json"
        models_cache.write_text("stale", encoding="utf-8")

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="github", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "ghu_token"}, expires_in=3600, config={}, metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="github")

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("GITHUB_TOKEN=DOH_PLACEHOLDER", text)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", text)
        self.assertTrue(models_cache.exists())
        restart_mock.assert_called_once_with(
            process_compose_url="http://127.0.0.1:9999",
            process_name=credentials_service.WEBUI_PROCESS_NAME,
        )

    async def test_openrouter_invalidate_deletes_models_cache_and_restarts_webui(self) -> None:
        """OpenRouter changes provider availability, so WebUI must rebuild /api/models."""
        service = self._make_service()
        self.webui_state_dir.mkdir(parents=True)
        models_cache = self.webui_state_dir / "models_cache.json"
        models_cache.write_text("stale", encoding="utf-8")

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openrouter", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"api_key": "sk-or-v1-real"}, expires_in=3600, config={}, metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="openrouter")

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_API_KEY=DOH_PLACEHOLDER", text)
        self.assertFalse(models_cache.exists())
        self.assertEqual(
            [call.kwargs["process_name"] for call in restart_mock.call_args_list],
            [credentials_service.GATEWAY_PROCESS_NAME, credentials_service.WEBUI_PROCESS_NAME],
        )

    async def test_codex_invalidate_deletes_models_cache_without_process_restart(self) -> None:
        """Model-provider cache refresh is generic, even when no env changes."""
        service = self._make_service()
        self.webui_state_dir.mkdir(parents=True)
        models_cache = self.webui_state_dir / "models_cache.json"
        models_cache.write_text("stale", encoding="utf-8")

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openai-codex", result=broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "codex-access", "chatgpt_account_id": "account-id"},
                expires_in=3600,
                config={},
                metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="openai-codex")

        self.assertFalse(models_cache.exists())
        restart_mock.assert_not_called()

    async def test_invalidate_all_uses_single_batched_call(self) -> None:
        """Explicit Refresh-all collapses to one DOH round-trip across every provider.

        The previous per-slug fan-out emitted one `INFO no integration row`
        Django log line per disconnected provider on every Refresh-all.
        Coalescing into a single POST (where `absent` is a normal entry,
        not a 4xx) makes that log line disappear.
        """
        service = self._make_service()

        absent_for_every_slug = {
            slug: broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_providers.TLS_INTERCEPT_PROVIDERS
        }

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=absent_for_every_slug,
        ) as batch_mock, patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ):
            await service.refresh_all_integrations()

        self.assertEqual(batch_mock.call_count, 1)
        called_slugs = batch_mock.call_args.kwargs["slugs"]
        self.assertEqual(set(called_slugs), set(tls_providers.TLS_INTERCEPT_PROVIDERS))


class TestTransientRefreshGuards(unittest.IsolatedAsyncioTestCase):
    """An all-transient DOH refresh must never clobber a good managed env block.

    Regression: the broker booted while DOH returned 503, the bootstrap
    refresh left the cache empty, and the env render stripped every
    integration from the gateway env file — the gateway then ran with no
    platforms until the next connect.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = pathlib.Path(self.tmp.name) / "hermes.env"
        self.webui_state_dir = pathlib.Path(self.tmp.name) / "webui-state"

    def _make_runtime(self) -> "broker.tls_intercept.TlsInterceptRuntime":
        root = pathlib.Path(self.tmp.name)
        return _make_tls_intercept_runtime(ca_dir=root / "ca", private_dir=root / "private")

    def _make_service(self) -> credentials_service.CredentialsService:
        return _make_credentials_service(
            tls_intercept_runtime=self._make_runtime(),
            gateway_env_path=self.env_path,
            webui_state_dir=self.webui_state_dir,
        )

    def _seed_telegram_env_block(self) -> str:
        """Write a managed block for a connected Telegram bot; returns the file text."""
        spec = tls_providers.TLS_INTERCEPT_PROVIDERS["telegram"]
        block = credentials_service._render_managed_block(snapshot=[(spec, {"allowed_users": ["123"]})])
        credentials_service._write_gateway_env_file(env_path=self.env_path, managed_block=block)
        return self.env_path.read_text(encoding="utf-8")

    def _transient_for_every_slug(self) -> dict:
        return {
            slug: broker.tls_intercept._transient_result()
            for slug in tls_providers.TLS_INTERCEPT_PROVIDERS
        }

    async def test_refresh_all_reports_doh_reachability(self) -> None:
        tls_intercept_runtime = self._make_runtime()
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", return_value=self._transient_for_every_slug()):
            self.assertFalse(await tls_intercept_runtime.refresh_all())
        absent_for_every_slug = {
            slug: broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_providers.TLS_INTERCEPT_PROVIDERS
        }
        with patch.object(broker.tls_intercept, "fetch_provider_tokens_batch", return_value=absent_for_every_slug):
            self.assertTrue(await tls_intercept_runtime.refresh_all())

    async def test_transient_invalidate_keeps_env_file_and_skips_restart(self) -> None:
        original = self._seed_telegram_env_block()
        service = self._make_service()

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="telegram", result=broker.tls_intercept._transient_result()),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="telegram")

        self.assertEqual(self.env_path.read_text(encoding="utf-8"), original)
        restart_mock.assert_not_called()

    async def test_bootstrap_transient_exits_without_touching_env_file(self) -> None:
        """A failed bootstrap refresh is fatal — the broker exits and ECS restarts the task."""
        original = self._seed_telegram_env_block()
        service = self._make_service()

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=self._transient_for_every_slug(),
        ):
            with self.assertRaises(SystemExit):
                await service.bootstrap()

        self.assertEqual(self.env_path.read_text(encoding="utf-8"), original)

    async def test_bootstrap_success_renders_env_file(self) -> None:
        # Start from the clobbered/empty state the gateway booted with.
        self.env_path.write_text("", encoding="utf-8")
        service = self._make_service()
        refreshed_results = {
            slug: broker.tls_intercept.RefreshResult(
                outcome=broker.tls_intercept.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_providers.TLS_INTERCEPT_PROVIDERS
        }
        refreshed_results["telegram"] = broker.tls_intercept.RefreshResult(
            outcome=broker.tls_intercept.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"bot_token": "123:abc"}, expires_in=3600, config={"allowed_users": ["123"]}, metadata={},
        )

        with patch.object(
            broker.tls_intercept,
            "fetch_provider_tokens_batch",
            return_value=refreshed_results,
        ):
            await service.bootstrap()

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:DOH_PLACEHOLDER", text)
        self.assertIn("TELEGRAM_ALLOWED_USERS=123", text)
