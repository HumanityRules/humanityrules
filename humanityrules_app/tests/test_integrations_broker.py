"""Tests for humr_broker.py.

The broker runs inside the customer-env Hermes container (not Django), but
its correctness is load-bearing for the WebUI extension's Integrations pane
and for all Google Workspace tool calls. We unit-test the load-bearing
primitives (host→provider routing, header rewrite, CA/leaf generation, and
refresh-loop outcome classification) directly without standing up the
asyncio servers.
"""

import asyncio
import dataclasses
import importlib.util
import pathlib
import ssl
import sys
import tempfile
import types
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, call, patch

import httpx


def _batched(*, slug: str, result) -> dict:
    """Build a fake one-slug HUMR response map for mocking `fetch_provider_tokens_batch`.

    The broker only ever asks HUMR about slugs it knows; in tests every
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
    """Load template_repos/hermes_agent/humr_runtime/integrations/humr_broker.py as a module.

    The broker imports its sibling modules by bare name. When supervisor.sh runs
    the broker in production, PYTHONPATH includes /opt/humr/runtime/integrations.
    Under pytest we're loading via importlib from the Django repo root, so we
    have to put the integrations dir on sys.path ourselves before exec_module
    triggers the bare imports.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    integrations_dir = repo_root / "template_repos" / "hermes_agent" / "humr_runtime" / "integrations"
    script_path = integrations_dir / "humr_broker.py"
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
import humr_client  # noqa: E402
import tls_certificate_authority  # noqa: E402
import tls_credential_injection  # noqa: E402
import tls_http_message_relay  # noqa: E402
import tls_intercept  # noqa: E402
import tls_provider_catalog  # noqa: E402
import tls_token_store  # noqa: E402


def _make_humr_client() -> humr_client.HumrClient:
    """Build a HumrClient with the fixed test identity."""
    return humr_client.HumrClient(
        control_plane_url="https://humr.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )


class TestEnvironmentFlags(unittest.TestCase):

    def test_merge_flag_defaults_enabled_when_absent(self) -> None:
        with patch.dict(broker.os.environ, {}, clear=True):
            self.assertTrue(broker._env_flag_enabled(name="HUMR_MERGE_INTEGRATION_ENABLED", default=True))

    def test_merge_flag_false_values_disable(self) -> None:
        for value in ("0", "false", "no", "off", "FALSE"):
            with patch.dict(broker.os.environ, {"HUMR_MERGE_INTEGRATION_ENABLED": value}, clear=True):
                self.assertFalse(broker._env_flag_enabled(name="HUMR_MERGE_INTEGRATION_ENABLED", default=True))

    def test_merge_flag_invalid_falls_back_to_default(self) -> None:
        with patch.dict(broker.os.environ, {"HUMR_MERGE_INTEGRATION_ENABLED": "wat"}, clear=True):
            self.assertTrue(broker._env_flag_enabled(name="HUMR_MERGE_INTEGRATION_ENABLED", default=True))


def _make_credential_state_store() -> tls_token_store.CredentialStateStore:
    """Create a fresh credential-state store for isolated broker tests."""
    return tls_token_store.CredentialStateStore(
        provider_slugs=tuple(tls_provider_catalog.TLS_INTERCEPT_PROVIDERS),
        humr_client=_make_humr_client(),
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
    )


def _make_tls_intercept_runtime(ca_dir: pathlib.Path, private_dir: pathlib.Path) -> broker.tls_intercept.TlsInterceptRuntime:
    """Create a fresh TLS-intercept runtime for control-app tests."""
    return broker.tls_intercept.TlsInterceptRuntime(
        providers=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS,
        humr_client=_make_humr_client(),
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
        usage_reporter=None,
    )


class TestHostToProviderRouting(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(cls.tmp.name)
        cls.runtime = _make_tls_intercept_runtime(ca_dir=root / "ca", private_dir=root / "private")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _routed_slug(self, host: str) -> str | None:
        normalized = tls_provider_catalog.normalize_connect_host(host=host)
        return self.runtime._host_to_provider.get(normalized)

    def test_every_catalog_host_routes_to_its_provider(self) -> None:
        for slug, provider in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS.items():
            for host in provider.hosts:
                with self.subTest(slug=slug, host=host):
                    self.assertEqual(self._routed_slug(host=host), slug)

    def test_unknown_host_returns_none(self) -> None:
        self.assertIsNone(self._routed_slug(host="api.notaprovider.com"))
        self.assertIsNone(self._routed_slug(host="example.com"))

    def test_connect_host_routing_is_case_and_trailing_dot_insensitive(self) -> None:
        self.assertEqual(self._routed_slug(host="GitHub.COM"), "github")
        self.assertEqual(self._routed_slug(host="github.com."), "github")

    def test_duplicate_host_claim_is_rejected(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["google"]
        with self.assertRaisesRegex(RuntimeError, "claimed by both"):
            tls_provider_catalog.build_host_to_provider(providers={"first": provider, "second": provider})


class TestRewriteAuthorization(unittest.TestCase):

    def test_existing_authorization_is_replaced(self) -> None:
        hdrs = [
            (b"authorization", b"Bearer SANDBOX-DUMMY-1"),
            (b"Authorization", b"Bearer SANDBOX-DUMMY-2"),
            (b"content-type", b"application/json"),
        ]
        out = tls_credential_injection._rewrite_authorization(
            headers=hdrs, token="REAL-TOKEN",
            auth_format=tls_provider_catalog.AUTH_FORMAT_BEARER,
        )
        auth_values = [value for name, value in out if name.lower() == b"authorization"]
        self.assertEqual(auth_values, [b"Bearer REAL-TOKEN"])

    def test_missing_authorization_gets_injected(self) -> None:
        hdrs = [(b"content-type", b"application/json")]
        out = tls_credential_injection._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=tls_provider_catalog.AUTH_FORMAT_BEARER,
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        self.assertEqual(auth, b"Bearer T")

    def test_host_is_left_for_relay_normalization(self) -> None:
        hdrs = [(b"host", b"whatever"), (b"authorization", b"Bearer x")]
        out = tls_credential_injection._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=tls_provider_catalog.AUTH_FORMAT_BEARER,
        )
        host = dict([(n.lower(), v) for n, v in out])[b"host"]
        self.assertEqual(host, b"whatever")

    def test_proxy_headers_are_left_for_relay_normalization(self) -> None:
        hdrs = [
            (b"authorization", b"Bearer x"),
            (b"proxy-connection", b"keep-alive"),
            (b"proxy-authorization", b"Basic xxx"),
        ]
        out = tls_credential_injection._rewrite_authorization(
            headers=hdrs, token="T",
            auth_format=tls_provider_catalog.AUTH_FORMAT_BEARER,
        )
        names = [n.lower() for n, _ in out]
        self.assertIn(b"proxy-connection", names)
        self.assertIn(b"proxy-authorization", names)

    def test_basic_x_access_token_format_for_github(self) -> None:
        import base64
        hdrs = [(b"authorization", b"Basic SANDBOX-PLACEHOLDER")]
        out = tls_credential_injection._rewrite_authorization(
            headers=hdrs, token="ghs_real_token",
            auth_format=tls_provider_catalog.AUTH_FORMAT_BASIC_X_ACCESS_TOKEN,
        )
        auth = dict([(n.lower(), v) for n, v in out])[b"authorization"]
        expected = b"Basic " + base64.b64encode(b"x-access-token:ghs_real_token")
        self.assertEqual(auth, expected)

    def test_telegram_path_token_is_rewritten_without_authorization_header(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[
                (b"host", b"api.telegram.org"),
                (b"authorization", b"Bearer placeholder"),
                (b"content-type", b"application/json"),
            ],
            path_with_query="/bot000000:HUMR_PLACEHOLDER/getUpdates?timeout=20",
            secrets={"bot_token": "123456:REAL"},
            provider=provider,
        )
        self.assertEqual(path, "/bot123456:REAL/getUpdates?timeout=20")
        header_names = [name.lower() for name, _value in headers]
        self.assertNotIn(b"authorization", header_names)

    def test_telegram_file_path_token_is_rewritten(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]
        _headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"api.telegram.org")],
            path_with_query="/file/bot000000:HUMR_PLACEHOLDER/documents/file.txt",
            secrets={"bot_token": "123456:REAL"},
            provider=provider,
        )
        self.assertEqual(path, "/file/bot123456:REAL/documents/file.txt")

    def test_url_rewrite_fails_closed_without_placeholder(self) -> None:
        """A URL-rewrite provider must reject paths missing its placeholder.

        Url-encoded placeholders (e.g. `%3A` instead of `:`) don't substring-match
        and must be rejected so we never forward an un-rewritten URL upstream.
        """
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]

        with self.assertRaisesRegex(tls_credential_injection.SecretSelectionError, "placeholder"):
            tls_credential_injection.rewrite_request_for_provider(
                headers=[(b"host", b"api.telegram.org")],
                path_with_query="/bot000000%3AHUMR_PLACEHOLDER/sendMessage",
                secrets={"bot_token": "123456:REAL"},
                provider=provider,
            )

    def test_slack_app_token_placeholder_selects_app_token(self) -> None:
        """A request bearing the app-token placeholder gets the real app token."""
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["slack"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xapp-HUMR_PLACEHOLDER")],
            path_with_query="/api/apps.connections.open",
            secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
            provider=provider,
        )
        self.assertEqual(path, "/api/apps.connections.open")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer xapp-REAL")

    def test_slack_bot_token_placeholder_selects_bot_token(self) -> None:
        """A request bearing the bot-token placeholder gets the real bot token."""
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["slack"]
        headers, _path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xoxb-HUMR_PLACEHOLDER")],
            path_with_query="/api/chat.postMessage",
            secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
            provider=provider,
        )
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer xoxb-REAL")

    def test_slack_unknown_placeholder_fails_closed(self) -> None:
        """An unrecognized bearer must raise rather than forward an un-swapped token."""
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["slack"]
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
            tls_credential_injection.rewrite_request_for_provider(
                headers=[(b"host", b"slack.com"), (b"authorization", b"Bearer xoxb-NOT-OURS")],
                path_with_query="/api/chat.postMessage",
                secrets={"app_token": "xapp-REAL", "bot_token": "xoxb-REAL"},
                provider=provider,
            )

    def test_openrouter_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["openrouter"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"openrouter.ai"), (b"authorization", b"Bearer HUMR_PLACEHOLDER")],
            path_with_query="/api/v1/chat/completions",
            secrets={"api_key": "sk-or-v1-real"},
            provider=provider,
        )
        self.assertEqual(path, "/api/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer sk-or-v1-real")

    def test_nous_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["nous"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"inference-api.nousresearch.com"), (b"authorization", b"Bearer HUMR_PLACEHOLDER")],
            path_with_query="/v1/chat/completions",
            secrets={"access_token": "nous-access"},
            provider=provider,
        )
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer nous-access")

    def test_openai_placeholder_bearer_is_rewritten(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["openai-api"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[(b"host", b"api.openai.com"), (b"authorization", b"Bearer HUMR_PLACEHOLDER")],
            path_with_query="/v1/chat/completions",
            secrets={"api_key": "sk-real"},
            provider=provider,
        )
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(dict((n.lower(), v) for n, v in headers)[b"authorization"], b"Bearer sk-real")

    def test_anthropic_placeholder_x_api_key_is_rewritten(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["anthropic"]
        headers, path = tls_credential_injection.rewrite_request_for_provider(
            headers=[
                (b"host", b"api.anthropic.com"),
                (b"x-api-key", b"HUMR_PLACEHOLDER"),
                (b"anthropic-version", b"2023-06-01"),
            ],
            path_with_query="/v1/messages",
            secrets={"api_key": "sk-ant-real"},
            provider=provider,
        )
        self.assertEqual(path, "/v1/messages")
        by_name = dict((n.lower(), v) for n, v in headers)
        # Real key swapped in; anthropic-version preserved; no bogus Authorization added.
        self.assertEqual(by_name[b"x-api-key"], b"sk-ant-real")
        self.assertEqual(by_name[b"anthropic-version"], b"2023-06-01")
        self.assertNotIn(b"authorization", by_name)

    def test_anthropic_request_without_placeholder_is_rejected(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["anthropic"]
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
            tls_credential_injection.rewrite_request_for_provider(
                headers=[(b"host", b"api.anthropic.com"), (b"x-api-key", b"sk-ant-NOT-OURS")],
                path_with_query="/v1/messages",
                secrets={"api_key": "sk-ant-real"},
                provider=provider,
            )


class TestAnonymousRequestClassification(unittest.TestCase):
    """Credential-less vault-style requests pass through; everything else stays on the HUMR path.

    The classifier runs before the token-store lookup, so an anonymous
    request (False) must never cost a HUMR refresh, a credentialed request
    (True) follows the rewrite path, and an unrecognized credential raises
    so BYO keys are never forwarded.
    """

    def _classify(self, *, slug: str, headers: list[tuple[bytes, bytes]], path: str) -> bool:
        return tls_credential_injection.needs_injection(
            headers=headers,
            path_with_query=path,
            provider=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS[slug],
        )

    def test_openrouter_request_without_authorization_is_anonymous(self) -> None:
        """The webui's public /api/v1/models fetch carries no Authorization — pass through."""
        self.assertFalse(self._classify(
            slug="openrouter",
            headers=[(b"host", b"openrouter.ai"), (b"accept", b"application/json")],
            path="/api/v1/models",
        ))

    def test_openrouter_placeholder_bearer_needs_injection(self) -> None:
        self.assertTrue(self._classify(
            slug="openrouter",
            headers=[(b"authorization", b"Bearer HUMR_PLACEHOLDER")],
            path="/api/v1/chat/completions",
        ))

    def test_openrouter_unknown_bearer_is_rejected(self) -> None:
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
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

    def test_anthropic_placeholder_x_api_key_needs_injection(self) -> None:
        self.assertTrue(self._classify(
            slug="anthropic",
            headers=[(b"x-api-key", b"HUMR_PLACEHOLDER")],
            path="/v1/messages",
        ))

    def test_anthropic_foreign_x_api_key_is_rejected(self) -> None:
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
            self._classify(
                slug="anthropic",
                headers=[(b"x-api-key", b"sk-ant-byo-key")],
                path="/v1/messages",
            )

    def test_anthropic_byo_authorization_bearer_is_rejected(self) -> None:
        """No x-api-key but an Authorization header (e.g. BYO OAuth bearer) must not pass through."""
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
            self._classify(
                slug="anthropic",
                headers=[(b"authorization", b"Bearer byo-oauth-token")],
                path="/v1/messages",
            )

    def test_oauth_providers_need_injection_even_without_authorization(self) -> None:
        """OAuth-style requests are implicitly ours — the proxy injects unconditionally."""
        for slug in ("google", "github", "nous", "openai-codex"):
            self.assertTrue(self._classify(
                slug=slug,
                headers=[(b"host", b"upstream.example")],
                path="/anything",
            ))

    def test_telegram_placeholder_path_needs_injection(self) -> None:
        self.assertTrue(self._classify(
            slug="telegram",
            headers=[(b"host", b"api.telegram.org")],
            path="/bot000000:HUMR_PLACEHOLDER/getUpdates",
        ))

    def test_telegram_path_without_placeholder_is_rejected(self) -> None:
        """Every Bot API path embeds a token, so telegram has no anonymous surface."""
        with self.assertRaises(tls_credential_injection.SecretSelectionError):
            self._classify(
                slug="telegram",
                headers=[(b"host", b"api.telegram.org")],
                path="/bot123456:BYO-TOKEN/sendMessage",
            )


class TestForwardHeaderNormalization(unittest.TestCase):

    def test_dechunked_request_gets_content_length_and_no_transfer_encoding(self) -> None:
        out = tls_http_message_relay._normalize_forward_headers(
            headers=[
                (b"Host", b"spoofed.example"),
                (b"host", b"also-spoofed.example"),
                (b"Transfer-Encoding", b"chunked"),
                (b"Connection", b"keep-alive"),
                (b"Content-Type", b"application/json"),
            ],
            body_length=11,
            upstream_host="github.com",
        )

        by_name = {name.lower(): value for name, value in out}
        self.assertNotIn(b"transfer-encoding", by_name)
        self.assertNotIn(b"connection", by_name)
        self.assertEqual(by_name[b"content-length"], b"11")
        self.assertEqual(by_name[b"host"], b"github.com")
        hosts = [value for name, value in out if name.lower() == b"host"]
        self.assertEqual(hosts, [b"github.com"])

    def test_existing_content_length_is_replaced(self) -> None:
        out = tls_http_message_relay._normalize_forward_headers(
            headers=[
                (b"Host", b"api.telegram.org"),
                (b"Content-Length", b"999"),
            ],
            body_length=4,
            upstream_host="api.telegram.org",
        )

        content_lengths = [value for name, value in out if name.lower() == b"content-length"]
        self.assertEqual(content_lengths, [b"4"])

    def test_authorization_is_preserved_while_proxy_headers_are_removed(self) -> None:
        out = tls_http_message_relay._normalize_forward_headers(
            headers=[
                (b"Host", b"api.openai.com"),
                (b"Authorization", b"Bearer broker-injected-token"),
                (b"Proxy-Connection", b"keep-alive"),
                (b"Proxy-Authorization", b"Basic sandbox-proxy-credential"),
            ],
            body_length=0,
            upstream_host="api.openai.com",
        )

        by_name = {name.lower(): value for name, value in out}
        self.assertEqual(by_name[b"authorization"], b"Bearer broker-injected-token")
        self.assertEqual(by_name[b"host"], b"api.openai.com")
        self.assertNotIn(b"proxy-connection", by_name)
        self.assertNotIn(b"proxy-authorization", by_name)


class TestHttpParsing(unittest.IsolatedAsyncioTestCase):

    async def test_read_body_reads_content_length(self) -> None:
        reader = _feed_upstream(b"helloNEXT")

        body = await tls_http_message_relay.read_body(
            reader=reader,
            headers=[(b"Content-Length", b"5")],
        )

        self.assertEqual(body, b"hello")
        self.assertEqual(await reader.readexactly(4), b"NEXT")

    async def test_read_body_prefers_chunked_over_content_length(self) -> None:
        reader = _feed_upstream(b"5\r\nhello\r\n0\r\n\r\nNEXT")

        body = await tls_http_message_relay.read_body(
            reader=reader,
            headers=[
                (b"Transfer-Encoding", b"chunked"),
                (b"Content-Length", b"999"),
            ],
        )

        self.assertEqual(body, b"hello")
        self.assertEqual(await reader.readexactly(4), b"NEXT")

    async def test_read_body_accepts_repeated_identical_content_length(self) -> None:
        reader = _feed_upstream(b"test")

        body = await tls_http_message_relay.read_body(
            reader=reader,
            headers=[
                (b"Content-Length", b"4"),
                (b"Content-Length", b"4"),
            ],
        )

        self.assertEqual(body, b"test")

    async def test_read_body_rejects_conflicting_content_length(self) -> None:
        reader = _feed_upstream(b"hello")

        with self.assertRaisesRegex(ValueError, "conflicting request Content-Length"):
            await tls_http_message_relay.read_body(
                reader=reader,
                headers=[
                    (b"Content-Length", b"4"),
                    (b"Content-Length", b"5"),
                ],
            )

    async def test_read_body_rejects_malformed_content_length(self) -> None:
        reader = _feed_upstream(b"")

        with self.assertRaisesRegex(ValueError, "invalid request Content-Length"):
            await tls_http_message_relay.read_body(
                reader=reader,
                headers=[(b"Content-Length", b"-1")],
            )

    async def test_read_body_rejects_repeated_transfer_encoding_chain(self) -> None:
        reader = _feed_upstream(b"0\r\n\r\n")

        with self.assertRaisesRegex(ValueError, "unsupported request Transfer-Encoding"):
            await tls_http_message_relay.read_body(
                reader=reader,
                headers=[
                    (b"Transfer-Encoding", b"chunked"),
                    (b"Transfer-Encoding", b"gzip"),
                ],
            )

    async def test_read_body_rejects_truncated_content_length(self) -> None:
        reader = _feed_upstream(b"short")

        with self.assertRaisesRegex(ValueError, "ended after 5 of 10 bytes"):
            await tls_http_message_relay.read_body(
                reader=reader,
                headers=[(b"Content-Length", b"10")],
            )

    async def test_read_chunked_consumes_trailers(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b"4\r\ntest\r\n0\r\nX-Trailer: one\r\nAnother: two\r\n\r\nNEXT")

        body = await tls_http_message_relay._read_chunked(reader=reader)

        self.assertEqual(body, b"test")
        self.assertEqual(await reader.readexactly(4), b"NEXT")

    async def test_read_chunked_rejects_truncated_payload(self) -> None:
        reader = _feed_upstream(b"a\r\nshort")

        with self.assertRaisesRegex(ValueError, "payload ended before"):
            await tls_http_message_relay._read_chunked(reader=reader)

    async def test_read_chunked_rejects_malformed_size(self) -> None:
        reader = _feed_upstream(b"-1\r\n")

        with self.assertRaisesRegex(ValueError, "invalid request chunk size"):
            await tls_http_message_relay._read_chunked(reader=reader)

    async def test_read_chunked_rejects_malformed_payload_delimiter(self) -> None:
        reader = _feed_upstream(b"4\r\ntestXX0\r\n\r\n")

        with self.assertRaisesRegex(ValueError, "not followed by CRLF"):
            await tls_http_message_relay._read_chunked(reader=reader)

    async def test_read_chunked_rejects_eof_inside_trailers(self) -> None:
        reader = _feed_upstream(b"0\r\nX-Trailer: incomplete\r\n")

        with self.assertRaisesRegex(ValueError, "ended inside trailers"):
            await tls_http_message_relay._read_chunked(reader=reader)


class _RecordingWriter:
    """asyncio.StreamWriter stand-in that records writes and drain boundaries.

    Each drain() snapshots everything written since the previous drain, so a
    streaming relay (one drain per upstream frame) is distinguishable from a
    buffered one (a single drain carrying the whole body).
    """

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.flushes: list[bytes] = []
        self._pending = bytearray()

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))
        self._pending.extend(data)

    async def drain(self) -> None:
        self.flushes.append(bytes(self._pending))
        self._pending = bytearray()

    def all_bytes(self) -> bytes:
        return b"".join(self.chunks)


class _NotifyingWriter(_RecordingWriter):
    """Recording writer whose drain boundaries can be awaited by staged tests."""

    def __init__(self) -> None:
        super().__init__()
        self._drained = asyncio.Condition()

    async def drain(self) -> None:
        await super().drain()
        async with self._drained:
            self._drained.notify_all()

    async def wait_for_bytes(self, expected: bytes, timeout_seconds: float) -> None:
        async with self._drained:
            await asyncio.wait_for(
                self._drained.wait_for(lambda: expected in self.all_bytes()),
                timeout=timeout_seconds,
            )


def _feed_upstream(raw: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()
    return reader


def _chunk(payload: bytes) -> bytes:
    """Frame a payload as one HTTP/1.1 chunk (hex size line + CRLF-delimited body)."""
    return f"{len(payload):x}".encode() + b"\r\n" + payload + b"\r\n"


class _StubUpstreamWriter:
    """Minimal writer for the upstream side of forward_to_upstream."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def all_bytes(self) -> bytes:
        return b"".join(self.chunks)


class _StubTransport:

    def __init__(self) -> None:
        self.protocol = object()

    def get_protocol(self) -> object:
        return self.protocol


class _TlsRecordingWriter(_RecordingWriter):
    """Writer stand-in that supports the transport and close API used by TLS interception."""

    def __init__(self) -> None:
        super().__init__()
        self.transport = _StubTransport()
        self.closed = False

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        if name == "peername":
            return ("test-client", 12345)
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _StubCertMinter:

    def __init__(self) -> None:
        self.hostnames: list[str] = []
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    def context_for(self, hostname: str) -> ssl.SSLContext:
        self.hostnames.append(hostname)
        return self.context


class _StubCredentialStateStore:

    def __init__(self, secrets: dict[str, str] | None) -> None:
        self.secrets = secrets
        self.invalidated_slugs: list[str] = []
        self.secret_slugs: list[str] = []

    async def credential_for_slug(self, slug: str) -> tls_token_store.ActiveCredential | None:
        self.secret_slugs.append(slug)
        if self.secrets is None:
            return None
        return tls_token_store.ActiveCredential(secrets=self.secrets, platform_shared=False)

    async def invalidate(self, slug: str) -> None:
        self.invalidated_slugs.append(slug)


class TestStreamingRelay(unittest.IsolatedAsyncioTestCase):
    """forward_to_upstream streams every response body live in its upstream framing."""

    async def _forward(self, upstream_response: bytes, *, host: str) -> tuple[int, bool, _RecordingWriter]:
        client_writer = _RecordingWriter()
        upstream_reader = _feed_upstream(upstream_response)

        async def _fake_open_connection(**kwargs) -> tuple[asyncio.StreamReader, _StubUpstreamWriter]:
            return upstream_reader, _StubUpstreamWriter()

        with patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection):
            status, keep_alive = await tls_http_message_relay.forward_to_upstream(
                host=host,
                port=443,
                method="POST",
                path_with_query="/v1/chat/completions",
                headers=[(b"host", host.encode())],
                body=b"{}",
                client_writer=client_writer,
                response_body_observer=None,
            )
        return status, keep_alive, client_writer

    async def _forward_from_reader(
        self,
        upstream_reader: asyncio.StreamReader,
        client_writer: _RecordingWriter,
        host: str,
    ) -> tuple[int, bool]:
        upstream_writer = _StubUpstreamWriter()

        async def _fake_open_connection(**kwargs: object) -> tuple[asyncio.StreamReader, _StubUpstreamWriter]:
            return upstream_reader, upstream_writer

        with patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection):
            return await tls_http_message_relay.forward_to_upstream(
                host=host,
                port=443,
                method="POST",
                path_with_query="/v1/chat/completions",
                headers=[(b"host", host.encode())],
                body=b"{}",
                client_writer=client_writer,
                response_body_observer=None,
            )

    async def test_chunked_sse_reaches_client_before_upstream_finishes(self) -> None:
        first_event = b'data: {"d":"first"}\n\n'
        second_event = b'data: {"d":"second"}\n\n'
        upstream_reader = asyncio.StreamReader()
        client_writer = _NotifyingWriter()
        upstream_reader.feed_data(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(first_event)
        )

        forward_task = asyncio.create_task(
            self._forward_from_reader(
                upstream_reader=upstream_reader,
                client_writer=client_writer,
                host="api.openai.com",
            )
        )
        await client_writer.wait_for_bytes(expected=first_event, timeout_seconds=1.0)

        self.assertFalse(forward_task.done())
        self.assertNotIn(second_event, client_writer.all_bytes())

        upstream_reader.feed_data(_chunk(second_event) + b"0\r\n\r\n")
        upstream_reader.feed_eof()
        status, keep_alive = await asyncio.wait_for(forward_task, timeout=1.0)

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertIn(second_event, client_writer.all_bytes())

    async def test_content_length_body_reaches_client_before_upstream_finishes(self) -> None:
        body = b"first-second"
        upstream_reader = asyncio.StreamReader()
        client_writer = _NotifyingWriter()
        upstream_reader.feed_data(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n"
            b"first-"
        )

        forward_task = asyncio.create_task(
            self._forward_from_reader(
                upstream_reader=upstream_reader,
                client_writer=client_writer,
                host="api.anthropic.com",
            )
        )
        await client_writer.wait_for_bytes(expected=b"first-", timeout_seconds=1.0)

        self.assertFalse(forward_task.done())
        self.assertNotIn(b"second", client_writer.all_bytes())

        upstream_reader.feed_data(b"second")
        status, keep_alive = await asyncio.wait_for(forward_task, timeout=1.0)

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertTrue(client_writer.all_bytes().endswith(body))

    async def test_eof_body_reaches_client_before_upstream_finishes(self) -> None:
        upstream_reader = asyncio.StreamReader()
        client_writer = _NotifyingWriter()
        upstream_reader.feed_data(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"\r\n"
            b"first-"
        )

        forward_task = asyncio.create_task(
            self._forward_from_reader(
                upstream_reader=upstream_reader,
                client_writer=client_writer,
                host="github.com",
            )
        )
        await client_writer.wait_for_bytes(expected=b"first-", timeout_seconds=1.0)

        self.assertFalse(forward_task.done())

        upstream_reader.feed_data(b"second")
        upstream_reader.feed_eof()
        status, keep_alive = await asyncio.wait_for(forward_task, timeout=1.0)

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        self.assertTrue(client_writer.all_bytes().endswith(b"first-second"))

    async def test_chunked_sse_is_relayed_frame_by_frame(self) -> None:
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(b"data: {\"d\":\"Hel\"}\n\n")
            + _chunk(b"data: {\"d\":\"lo!\"}\n\n")
            + _chunk(b"data: [DONE]\n\n")
            + b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        # Status/header preamble preserves the chunked framing verbatim — the
        # client's chunked decoder is what makes incremental delivery work.
        full = writer.all_bytes()
        self.assertIn(b"Content-Type: text/event-stream", full)
        self.assertIn(b"Transfer-Encoding: chunked", full)
        self.assertIn(b'data: {"d":"Hel"}', full)
        self.assertIn(b"data: [DONE]", full)
        # Each SSE frame flushes separately: head + 3 data frames + terminator.
        non_empty_flushes = [f for f in writer.flushes if f]
        self.assertGreaterEqual(len(non_empty_flushes), 4)

    async def test_chunked_without_content_type_is_relayed_frame_by_frame(self) -> None:
        # The ChatGPT Codex backend omits Content-Type on its SSE responses
        # (chatgpt.com/backend-api/codex/responses): chunked + no declared
        # content type must still take the streaming relay, not the buffered
        # path that would hold every token until the run completes.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(b"event: response.created\ndata: {}\n\n")
            + _chunk(b"event: response.output_text.delta\ndata: {\"d\":\"Hi\"}\n\n")
            + _chunk(b"event: response.completed\ndata: {}\n\n")
            + b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="chatgpt.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        full = writer.all_bytes()
        self.assertIn(b"Transfer-Encoding: chunked", full)
        self.assertNotIn(b"Content-Length:", full)
        self.assertIn(b"event: response.output_text.delta", full)
        # Each frame flushes separately: head + 3 events + terminator.
        non_empty_flushes = [f for f in writer.flushes if f]
        self.assertGreaterEqual(len(non_empty_flushes), 4)

    async def test_content_length_body_streams_after_head(self) -> None:
        body = b"{\"ok\":true}"
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertTrue(writer.all_bytes().endswith(body))
        # Streaming path: the head is committed first, then the body relays
        # in its own flush(es) — never held until the body completes.
        self.assertEqual(len([f for f in writer.flushes if f]), 2)

    async def test_content_length_sse_is_relayed(self) -> None:
        payload = b"data: {\"d\":\"hi\"}\n\ndata: [DONE]\n\n"
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"\r\n" + payload
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertIn(payload, writer.all_bytes())

    async def test_json_content_length_response_streams_with_framing_preserved(self) -> None:
        body = b"{\"ok\":true}"
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        full = writer.all_bytes()
        self.assertIn(b"Content-Length: 11", full)
        self.assertTrue(full.endswith(body))
        # Streaming path: head flush + body flush.
        self.assertEqual(len([f for f in writer.flushes if f]), 2)

    async def test_interim_1xx_head_is_relayed_before_final_response(self) -> None:
        body = b"ok"
        upstream = (
            b"HTTP/1.1 100 Continue\r\n"
            b"\r\n"
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        # The final status — not the interim one — drives the return value.
        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        full = writer.all_bytes()
        self.assertIn(b"HTTP/1.1 100 Continue", full)
        self.assertIn(b"HTTP/1.1 200 OK", full)
        self.assertTrue(full.endswith(body))

    async def test_transfer_encoding_wins_over_content_length_and_cl_is_stripped(self) -> None:
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Content-Length: 999\r\n"
            b"\r\n"
            + _chunk(b"payload")
            + b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        # Chunked framing terminated cleanly, so the connection is reusable —
        # which is only sound because the body was read by TE, not CL.
        self.assertTrue(keep_alive)
        full = writer.all_bytes()
        self.assertIn(b"Transfer-Encoding: chunked", full)
        # Forwarding both TE and CL would leave the client parser free to
        # pick the wrong delimiter — CL must not survive into the head.
        self.assertNotIn(b"Content-Length", full)
        self.assertIn(b"payload", full)

    async def test_invalid_content_length_fails_before_head_is_committed(self) -> None:
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: garbage\r\n"
            b"\r\n"
        )
        client_writer = _RecordingWriter()
        upstream_reader = _feed_upstream(upstream)

        async def _fake_open_connection(**kwargs) -> tuple[asyncio.StreamReader, _StubUpstreamWriter]:
            return upstream_reader, _StubUpstreamWriter()

        with patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection):
            with self.assertRaises(RuntimeError):
                await tls_http_message_relay.forward_to_upstream(
                    host="api.anthropic.com",
                    port=443,
                    method="GET",
                    path_with_query="/v1/models",
                    headers=[(b"host", b"api.anthropic.com")],
                    body=b"",
                    client_writer=client_writer,
                    response_body_observer=None,
                )
        # Nothing was written: the caller can still answer with a clean 502.
        self.assertEqual(client_writer.all_bytes(), b"")

    async def test_malformed_chunk_size_marks_connection_not_reusable(self) -> None:
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"-1\r\n"
            b"\r\n"
            b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        # The bogus size line is not forwarded — int(x, 16) would have
        # accepted "-1" but a client chunk parser must reject it.
        self.assertNotIn(b"-1\r\n", writer.all_bytes())

    async def test_huge_single_chunk_relays_in_bounded_subreads(self) -> None:
        payload = b"x" * 200_000
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(payload)
            + b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertIn(payload, writer.all_bytes())
        # One sender-controlled 200KB chunk must NOT arrive as one flush —
        # the relay reads it in <=64KB sub-reads (4 payload flushes here),
        # which is what bounds broker memory per in-flight response.
        payload_flushes = [f for f in writer.flushes if f and b"x" * 1024 in f]
        self.assertGreaterEqual(len(payload_flushes), 4)

    async def test_repeated_connection_close_field_disables_reuse(self) -> None:
        body = b"ok"
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Connection: keep-alive\r\n"
            b"Connection: close\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"\r\n" + body
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.anthropic.com")

        self.assertEqual(status, 200)
        # Connection may appear as multiple field lines; the `close` token in
        # ANY of them wins over an earlier keep-alive.
        self.assertFalse(keep_alive)
        self.assertTrue(writer.all_bytes().endswith(body))

    async def test_eof_framed_sse_marks_connection_not_reusable(self) -> None:
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"\r\n"
            b"data: {\"d\":\"x\"}\n\ndata: [DONE]\n\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        # No Content-Length / chunked framing: EOF is the only end-of-body
        # signal, so the client connection cannot be reused.
        self.assertFalse(keep_alive)
        self.assertIn(b"data: [DONE]", writer.all_bytes())

    async def test_truncated_content_length_sse_marks_connection_not_reusable(self) -> None:
        # Upstream advertises 40 bytes but EOFs after 15 — the client is left
        # waiting on the unfulfilled Content-Length, so the socket must not be
        # reused for a later response.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Content-Length: 40\r\n"
            b"\r\n"
            b"data: {\"d\":\"x\"}"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        # Bytes that did arrive are still forwarded.
        self.assertIn(b'data: {"d":"x"}', writer.all_bytes())
        self.assertNotIn(b"HTTP/1.1 502", writer.all_bytes())

    async def test_chunked_sse_without_terminator_marks_connection_not_reusable(self) -> None:
        # A well-formed chunk arrives, then upstream EOFs before the
        # terminating 0-chunk: the client's chunk decoder has no legal
        # end-of-body, so reuse would hang or misframe the next response.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(b"data: {\"d\":\"Hel\"}\n\n")
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        self.assertIn(b'data: {"d":"Hel"}', writer.all_bytes())

    async def test_chunked_sse_with_short_payload_marks_connection_not_reusable(self) -> None:
        # Size line declares 20 bytes but only 5 arrive before EOF: the partial
        # payload is forwarded, but the framing is broken so the connection is
        # not reusable.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"14\r\nhello"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        self.assertIn(b"hello", writer.all_bytes())

    async def test_chunked_sse_terminator_without_final_crlf_marks_not_reusable(self) -> None:
        # Upstream sends the 0-chunk size line but EOFs before the blank line
        # closing the trailer section: the chunked terminator is incomplete, so
        # the client can't reframe and the connection must not be reused.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(b"data: [DONE]\n\n")
            + b"0\r\n"  # last-chunk size line, then EOF — no closing CRLF
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertFalse(keep_alive)
        self.assertIn(b"data: [DONE]", writer.all_bytes())

    async def test_chunked_sse_complete_terminator_is_reusable(self) -> None:
        # The well-formed counterpart: 0-chunk followed by its closing blank
        # line. Clean terminator → connection reusable.
        upstream = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            + _chunk(b"data: [DONE]\n\n")
            + b"0\r\n\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertTrue(writer.all_bytes().endswith(b"0\r\n\r\n"))

    async def test_no_body_status_with_event_stream_type_is_buffered(self) -> None:
        # A 204 mislabeled text/event-stream must not enter the streaming relay
        # (which would block on EOF); it takes the buffered no-body path and
        # the connection stays reusable.
        upstream = (
            b"HTTP/1.1 204 No Content\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"\r\n"
        )
        status, keep_alive, writer = await self._forward(upstream, host="api.openai.com")

        self.assertEqual(status, 204)
        self.assertTrue(keep_alive)
        self.assertIn(b"204 No Content", writer.all_bytes())


class TestProxyConnectionStateMachine(unittest.IsolatedAsyncioTestCase):
    """Exercise CONNECT routing and the persistent intercepted-request loop."""

    async def _run_provider_intercept(
        self,
        client_bytes: bytes,
        upstream_responses: list[bytes],
        secrets: dict[str, str] | None,
        feed_client_eof: bool,
        provider_slug: str,
        host: str,
    ) -> tuple[_TlsRecordingWriter, list[_StubUpstreamWriter], _StubCredentialStateStore]:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS[provider_slug]
        credential_state_store = _StubCredentialStateStore(secrets=secrets)
        minter = _StubCertMinter()
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(client_bytes)
        if feed_client_eof:
            client_reader.feed_eof()
        client_writer = _TlsRecordingWriter()
        upstream_readers = [_feed_upstream(response) for response in upstream_responses]
        upstream_writers = [_StubUpstreamWriter() for _response in upstream_responses]

        async def _fake_open_connection(**kwargs: object) -> tuple[asyncio.StreamReader, _StubUpstreamWriter]:
            if not upstream_readers:
                raise AssertionError("intercept loop opened more upstream connections than expected")
            return upstream_readers.pop(0), upstream_writers[len(upstream_writers) - len(upstream_readers) - 1]

        loop = asyncio.get_running_loop()
        start_tls = AsyncMock(return_value=client_writer.transport)
        with (
            patch.object(loop, "start_tls", start_tls),
            patch.object(broker.tls_intercept.asyncio, "StreamWriter", return_value=client_writer),
            patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection),
        ):
            await asyncio.wait_for(
                broker.tls_intercept._intercept_and_forward(
                    client_reader=client_reader,
                    client_writer=client_writer,
                    host=host,
                    port=443,
                    provider=provider,
                    minter=minter,
                    credential_state_store=credential_state_store,
                    usage_reporter=None,
                ),
                timeout=1.0,
            )

        start_tls.assert_awaited_once()
        self.assertEqual(minter.hostnames, [host])
        return client_writer, upstream_writers, credential_state_store

    async def _run_intercept(
        self,
        client_bytes: bytes,
        upstream_responses: list[bytes],
        secrets: dict[str, str] | None,
        feed_client_eof: bool,
    ) -> tuple[_TlsRecordingWriter, list[_StubUpstreamWriter], _StubCredentialStateStore]:
        return await self._run_provider_intercept(
            client_bytes=client_bytes,
            upstream_responses=upstream_responses,
            secrets=secrets,
            feed_client_eof=feed_client_eof,
            provider_slug="openrouter",
            host="openrouter.ai",
        )

    async def _forward_one_request(
        self,
        client_bytes: bytes,
        secrets: dict[str, str],
        provider_slug: str,
        host: str,
    ) -> tuple[bytes, _StubCredentialStateStore]:
        _client_writer, upstream_writers, credential_state_store = await self._run_provider_intercept(
            client_bytes=client_bytes,
            upstream_responses=[b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"],
            secrets=secrets,
            feed_client_eof=True,
            provider_slug=provider_slug,
            host=host,
        )
        self.assertEqual(len(upstream_writers), 1)
        return upstream_writers[0].all_bytes(), credential_state_store

    async def test_connect_routes_known_host_to_tls_interceptor(self) -> None:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["openrouter"]
        credential_state_store = _StubCredentialStateStore(secrets={"api_key": "real-key"})
        minter = _StubCertMinter()
        client_reader = _feed_upstream(
            b"CONNECT OpenRouter.ai.:8443 HTTP/1.1\r\n"
            b"Host: OpenRouter.ai.:8443\r\n"
            b"\r\n"
        )
        client_writer = _TlsRecordingWriter()
        intercept = AsyncMock()

        with patch.object(broker.tls_intercept, "_intercept_and_forward", intercept):
            await broker.tls_intercept._handle_proxy_conn(
                reader=client_reader,
                writer=client_writer,
                minter=minter,
                providers=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS,
                host_to_provider=tls_provider_catalog.build_host_to_provider(
                    providers=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS,
                ),
                credential_state_store=credential_state_store,
                usage_reporter=None,
            )

        intercept.assert_awaited_once_with(
            client_reader=client_reader,
            client_writer=client_writer,
            host="openrouter.ai",
            port=8443,
            provider=provider,
            minter=minter,
            credential_state_store=credential_state_store,
            usage_reporter=None,
        )
        self.assertTrue(client_writer.closed)

    async def test_persistent_client_relays_chunked_sse_then_content_length_response(self) -> None:
        first_event = b'data: {"d":"first"}\n\n'
        client_bytes = (
            b"POST /api/v1/chat/completions HTTP/1.1\r\n"
            b"Host: openrouter.ai\r\n"
            b"Authorization: Bearer HUMR_PLACEHOLDER\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"2\r\n{}\r\n0\r\n\r\n"
            b"GET /api/v1/models HTTP/1.1\r\n"
            b"Host: openrouter.ai\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        upstream_responses = [
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
                + _chunk(first_event)
                + b"0\r\n\r\n"
            ),
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
        ]

        client_writer, upstream_writers, credential_state_store = await self._run_intercept(
            client_bytes=client_bytes,
            upstream_responses=upstream_responses,
            secrets={"api_key": "real-openrouter-key"},
            feed_client_eof=False,
        )

        downstream = client_writer.all_bytes()
        self.assertIn(first_event, downstream)
        self.assertTrue(downstream.endswith(b"{}"))
        self.assertEqual(downstream.count(b"HTTP/1.1 200 OK"), 2)
        first_request = upstream_writers[0].all_bytes()
        self.assertIn(b"POST /api/v1/chat/completions HTTP/1.1", first_request)
        self.assertIn(b"Authorization: Bearer real-openrouter-key", first_request)
        self.assertIn(b"Content-Length: 2", first_request)
        self.assertNotIn(b"Transfer-Encoding", first_request)
        second_request = upstream_writers[1].all_bytes()
        self.assertIn(b"GET /api/v1/models HTTP/1.1", second_request)
        self.assertNotIn(b"Authorization", second_request)
        self.assertEqual(credential_state_store.secret_slugs, ["openrouter"])
        self.assertTrue(client_writer.closed)

    async def test_anonymous_request_normalizes_host_and_removes_proxy_headers(self) -> None:
        upstream_request, credential_state_store = await self._forward_one_request(
            client_bytes=(
                b"GET /api/v1/models HTTP/1.1\r\n"
                b"Host: attacker.example\r\n"
                b"Proxy-Connection: keep-alive\r\n"
                b"Proxy-Authorization: Basic must-not-leak\r\n"
                b"User-Agent: sandbox-client\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            ),
            secrets={"api_key": "unused"},
            provider_slug="openrouter",
            host="openrouter.ai",
        )

        self.assertIn(b"Host: openrouter.ai\r\n", upstream_request)
        self.assertIn(b"user-agent: sandbox-client\r\n", upstream_request)
        self.assertNotIn(b"attacker.example", upstream_request)
        self.assertNotIn(b"proxy-", upstream_request.lower())
        self.assertNotIn(b"authorization", upstream_request.lower())
        self.assertEqual(credential_state_store.secret_slugs, [])

    async def test_oauth_bearer_injection_normalizes_final_upstream_request(self) -> None:
        upstream_request, credential_state_store = await self._forward_one_request(
            client_bytes=(
                b"GET /gmail/v1/users/me/profile HTTP/1.1\r\n"
                b"Host: attacker.example\r\n"
                b"Proxy-Authorization: Basic must-not-leak\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            ),
            secrets={"access_token": "ya29-real"},
            provider_slug="google",
            host="gmail.googleapis.com",
        )

        self.assertIn(b"Host: gmail.googleapis.com\r\n", upstream_request)
        self.assertIn(b"Authorization: Bearer ya29-real\r\n", upstream_request)
        self.assertNotIn(b"attacker.example", upstream_request)
        self.assertNotIn(b"proxy-", upstream_request.lower())
        self.assertEqual(credential_state_store.secret_slugs, ["google"])

    async def test_oauth_multi_header_injection_replaces_every_broker_owned_value(self) -> None:
        upstream_request, credential_state_store = await self._forward_one_request(
            client_bytes=(
                b"POST /backend-api/codex/responses HTTP/1.1\r\n"
                b"Host: attacker.example\r\n"
                b"Authorization: Bearer sandbox-placeholder\r\n"
                b"ChatGPT-Account-ID: spoofed-account\r\n"
                b"Originator: codex_cli_rs\r\n"
                b"Proxy-Connection: keep-alive\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"{}"
            ),
            secrets={"access_token": "codex-real", "chatgpt_account_id": "account-real"},
            provider_slug="openai-codex",
            host="chatgpt.com",
        )

        self.assertIn(b"Host: chatgpt.com\r\n", upstream_request)
        self.assertIn(b"Authorization: Bearer codex-real\r\n", upstream_request)
        self.assertIn(b"ChatGPT-Account-ID: account-real\r\n", upstream_request)
        self.assertIn(b"originator: codex_cli_rs\r\n", upstream_request)
        self.assertNotIn(b"spoofed-account", upstream_request)
        self.assertNotIn(b"proxy-", upstream_request.lower())
        self.assertEqual(credential_state_store.secret_slugs, ["openai-codex"])

    async def test_url_credential_rewrite_removes_authorization_before_forwarding(self) -> None:
        upstream_request, credential_state_store = await self._forward_one_request(
            client_bytes=(
                b"POST /bot000000:HUMR_PLACEHOLDER/sendMessage HTTP/1.1\r\n"
                b"Host: attacker.example\r\n"
                b"Authorization: Bearer must-not-leak\r\n"
                b"Proxy-Authorization: Basic must-not-leak\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"{}"
            ),
            secrets={"bot_token": "123456:real"},
            provider_slug="telegram",
            host="api.telegram.org",
        )

        self.assertIn(b"POST /bot123456:real/sendMessage HTTP/1.1\r\n", upstream_request)
        self.assertIn(b"Host: api.telegram.org\r\n", upstream_request)
        self.assertIn(b"content-type: application/json\r\n", upstream_request)
        self.assertNotIn(b"attacker.example", upstream_request)
        self.assertNotIn(b"authorization", upstream_request.lower())
        self.assertNotIn(b"proxy-", upstream_request.lower())
        self.assertEqual(credential_state_store.secret_slugs, ["telegram"])

    async def test_custom_api_key_rewrite_removes_authorization_and_preserves_provider_headers(self) -> None:
        upstream_request, credential_state_store = await self._forward_one_request(
            client_bytes=(
                b"POST /v1/messages HTTP/1.1\r\n"
                b"Host: attacker.example\r\n"
                b"X-Api-Key: HUMR_PLACEHOLDER\r\n"
                b"Authorization: Bearer must-not-leak\r\n"
                b"Anthropic-Version: 2023-06-01\r\n"
                b"Proxy-Connection: keep-alive\r\n"
                b"Content-Length: 2\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"{}"
            ),
            secrets={"api_key": "sk-ant-real"},
            provider_slug="anthropic",
            host="api.anthropic.com",
        )

        self.assertIn(b"Host: api.anthropic.com\r\n", upstream_request)
        self.assertIn(b"x-api-key: sk-ant-real\r\n", upstream_request)
        self.assertIn(b"anthropic-version: 2023-06-01\r\n", upstream_request)
        self.assertNotIn(b"attacker.example", upstream_request)
        self.assertNotIn(b"authorization", upstream_request.lower())
        self.assertNotIn(b"proxy-", upstream_request.lower())
        self.assertEqual(credential_state_store.secret_slugs, ["anthropic"])

    async def test_upstream_eof_framing_ends_client_loop_without_waiting_for_another_request(self) -> None:
        client_writer, upstream_writers, credential_state_store = await self._run_intercept(
            client_bytes=b"GET /api/v1/models HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n",
            upstream_responses=[b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{}"],
            secrets={"api_key": "unused"},
            feed_client_eof=False,
        )

        self.assertTrue(client_writer.all_bytes().endswith(b"{}"))
        self.assertEqual(len(upstream_writers), 1)
        self.assertEqual(credential_state_store.secret_slugs, [])

    async def test_upstream_connection_close_ends_client_loop_after_framed_body(self) -> None:
        client_writer, _upstream_writers, _credential_state_store = await self._run_intercept(
            client_bytes=b"GET /api/v1/models HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n",
            upstream_responses=[b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}"],
            secrets={"api_key": "unused"},
            feed_client_eof=False,
        )

        self.assertTrue(client_writer.all_bytes().endswith(b"{}"))

    async def test_upstream_failure_before_head_returns_clean_502(self) -> None:
        with patch.object(broker.tls_intercept.logger, "exception") as log_exception:
            client_writer, _upstream_writers, _credential_state_store = await self._run_intercept(
                client_bytes=b"GET /api/v1/models HTTP/1.1\r\nHost: openrouter.ai\r\n\r\n",
                upstream_responses=[b"HTTP/1.1 200 OK\r\nContent-Length: garbage\r\n\r\n"],
                secrets={"api_key": "unused"},
                feed_client_eof=False,
            )

        response = client_writer.all_bytes()
        self.assertIn(b"HTTP/1.1 502 Bad Gateway", response)
        self.assertNotIn(b"HTTP/1.1 200 OK", response)
        log_exception.assert_called_once()

    async def test_only_credentialed_401_invalidates_provider_cache(self) -> None:
        client_bytes = (
            b"GET /api/v1/authenticated HTTP/1.1\r\n"
            b"Host: openrouter.ai\r\n"
            b"Authorization: Bearer HUMR_PLACEHOLDER\r\n"
            b"\r\n"
            b"GET /api/v1/anonymous HTTP/1.1\r\n"
            b"Host: openrouter.ai\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        unauthorized = b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n"

        _client_writer, _upstream_writers, credential_state_store = await self._run_intercept(
            client_bytes=client_bytes,
            upstream_responses=[unauthorized, unauthorized],
            secrets={"api_key": "real-openrouter-key"},
            feed_client_eof=False,
        )

        self.assertEqual(credential_state_store.secret_slugs, ["openrouter"])
        self.assertEqual(credential_state_store.invalidated_slugs, ["openrouter"])

    async def test_bad_request_framing_returns_400_without_opening_upstream(self) -> None:
        client_writer, upstream_writers, credential_state_store = await self._run_intercept(
            client_bytes=(
                b"POST /api/v1/chat/completions HTTP/1.1\r\n"
                b"Host: openrouter.ai\r\n"
                b"Content-Length: 4\r\n"
                b"Content-Length: 5\r\n"
                b"\r\n"
            ),
            upstream_responses=[],
            secrets={"api_key": "unused"},
            feed_client_eof=False,
        )

        response = client_writer.all_bytes()
        self.assertIn(b"HTTP/1.1 400 Bad Request", response)
        self.assertIn(b"conflicting request Content-Length", response)
        self.assertEqual(upstream_writers, [])
        self.assertEqual(credential_state_store.secret_slugs, [])


class TestCertMinter(unittest.TestCase):

    def test_bootstrap_writes_bundle_and_leaf_mint_returns_ssl_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ca_dir = pathlib.Path(tmp) / "ca"
            private_dir = pathlib.Path(tmp) / "private"
            minter = tls_certificate_authority.CertMinter(ca_dir=ca_dir, private_dir=private_dir)
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
            minter = tls_certificate_authority.CertMinter(ca_dir=pathlib.Path(tmp) / "ca", private_dir=private_dir)
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
    org_slug: str,
) -> types.SimpleNamespace:
    """Wire a control app + CredentialsService the way the broker does at startup."""
    client = _make_humr_client()
    service = credentials_service.CredentialsService(
        humr_client=client,
        tls_intercept_runtime=tls_intercept_runtime,
        mcp_aggregator=aggregator,
        providers=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS,
        gateway_env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url="http://127.0.0.1:9999",
        webui_python=pathlib.Path("/nonexistent/webui-python"),
        runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
        hermes_home=pathlib.Path("/nonexistent/hermes-home"),
    )
    device_stub = _StubDeviceFlow()
    app = broker.control_api.build_control_app(
        mcp_aggregator=aggregator,
        tls_intercept_runtime=tls_intercept_runtime,
        oauth_device_flow=device_stub,
        credentials_service=service,
        humr_client=client,
        env_slug="default",
        org_slug=org_slug,
    )
    return types.SimpleNamespace(app=app, service=service, humr_client=client, device_flow=device_stub)


def _make_credentials_service(
    tls_intercept_runtime: "broker.tls_intercept.TlsInterceptRuntime",
    gateway_env_path: pathlib.Path,
    webui_state_dir: pathlib.Path,
) -> credentials_service.CredentialsService:
    """Build a CredentialsService over a stub aggregator for choreography tests."""
    return credentials_service.CredentialsService(
        humr_client=_make_humr_client(),
        tls_intercept_runtime=tls_intercept_runtime,
        mcp_aggregator=_ready_stub_aggregator(),
        providers=tls_provider_catalog.TLS_INTERCEPT_PROVIDERS,
        gateway_env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url="http://127.0.0.1:9999",
        webui_python=pathlib.Path("/nonexistent/webui-python"),
        runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
        hermes_home=pathlib.Path("/nonexistent/hermes-home"),
    )


class TestControlIntegrations(unittest.IsolatedAsyncioTestCase):
    """/integrations renders from cache; it MUST NOT call HUMR on the status path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = pathlib.Path(self.tmp.name)
        self.tls_intercept_runtime = _make_tls_intercept_runtime(ca_dir=root / "ca", private_dir=root / "private")
        self.gateway_env_path = root / "hermes.env"
        self.webui_state_dir = root / "webui-state"

    def _control_parts(self, aggregator: _StubAggregator, org_slug: str) -> types.SimpleNamespace:
        return _make_control_parts(
            tls_intercept_runtime=self.tls_intercept_runtime,
            aggregator=aggregator,
            gateway_env_path=self.gateway_env_path,
            webui_state_dir=self.webui_state_dir,
            org_slug=org_slug,
        )

    async def test_get_integrations_reads_cache_without_calling_humr(self) -> None:
        """Status reads never call HUMR; connected items come from the pre-warmed cache."""
        from starlette.testclient import TestClient

        app = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules").app

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
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
        # Three back-to-back GETs add zero HUMR calls beyond the explicit pre-warm.
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(resp2.json(), resp.json())
        self.assertEqual(resp3.json(), resp.json())
        self.assertEqual(
            (await self.tls_intercept_runtime._credential_state_store.credential_for_slug(slug="google")).secrets,
            {"access_token": "fresh-token"},
        )

    async def test_customer_org_hides_x_and_platform_served_codex(self) -> None:
        """Customer orgs never see the x card, and codex hides when the platform credential serves it."""
        from starlette.testclient import TestClient

        app = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="acme").app

        with TestClient(app) as client:
            before_by_slug = {item["slug"]: item for item in client.get("/integrations").json()["items"]}
        # Disconnected codex is hidden too: the card name alone would leak the platform default.
        self.assertNotIn("openai-codex", before_by_slug)
        self.assertNotIn("x", before_by_slug)

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openai-codex", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "platform-token"},
                expires_in=3600,
                config={},
                metadata={"platform_shared": True},
            )),
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="openai-codex")
        with TestClient(app) as client:
            items_by_slug = {item["slug"]: item for item in client.get("/integrations").json()["items"]}

        self.assertNotIn("openai-codex", items_by_slug)
        self.assertNotIn("x", items_by_slug)
        # Everything else, including the BYOK model-provider cards, stays visible.
        self.assertIn("google", items_by_slug)
        self.assertIn("openai-api", items_by_slug)
        self.assertIn("anthropic", items_by_slug)

    async def test_customer_org_sees_codex_connected_via_own_credential(self) -> None:
        """Codex shows for a customer org when their own (org-shared or personal) credential backs it."""
        from starlette.testclient import TestClient

        app = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="acme").app

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openai-codex", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "customer-token"},
                expires_in=3600,
                config={},
                metadata={"org_shared": True, "org_shared_scope": "everyone"},
            )),
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="openai-codex")
        with TestClient(app) as client:
            items_by_slug = {item["slug"]: item for item in client.get("/integrations").json()["items"]}

        self.assertEqual(items_by_slug["openai-codex"]["status"], "connected")
        self.assertNotIn("x", items_by_slug)

    async def test_platform_owner_org_sees_every_card(self) -> None:
        """The platform-owner org bypasses the visibility policy entirely."""
        from starlette.testclient import TestClient

        app = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules").app

        with TestClient(app) as client:
            items_by_slug = {item["slug"]: item for item in client.get("/integrations").json()["items"]}

        self.assertIn("x", items_by_slug)
        self.assertIn("openai-codex", items_by_slug)

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
        """An `absent` outcome from HUMR must remove (not store) the cache entry."""
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_intercept_runtime._credential_state_store._cache)
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")

    async def test_refresh_slug_refetches_even_when_cache_is_fresh(self) -> None:
        """Explicit refresh must hit HUMR even when the cached token is still fresh."""
        responses = [
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"}, expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T2"}, expires_in=3600, config={}, metadata={},
            )),
        ]
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", side_effect=responses) as fetch_mock:
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertEqual(fetch_mock.call_count, 2)
        self.assertEqual(self.tls_intercept_runtime._credential_state_store._cache["google"].secrets, {"access_token": "T2"})

    async def test_transient_after_eviction_does_not_fabricate_entry(self) -> None:
        """A transient refresh outcome must not write a sentinel into an empty cache."""
        responses = [
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"}, expires_in=3600, config={}, metadata={},
            )),
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ]
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", side_effect=responses):
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            await self.tls_intercept_runtime._credential_state_store.invalidate(slug="google")
            await self.tls_intercept_runtime.refresh_slug(slug="google")

        self.assertNotIn("google", self.tls_intercept_runtime._credential_state_store._cache)

    async def test_transient_during_lead_window_keeps_serving_cached_token(self) -> None:
        """Refresh-ahead transient failure must NOT make the proxy say "not connected"
        when the cached token is unfresh (inside the lead window) but still un-expired.

        Without this, a HUMR hiccup during the final `refresh_lead_seconds` of
        an access_token's life would surface as "not connected" to the
        sandbox even though we hold a usable token. The proxy should keep
        serving the cached token for the rest of its expires_at window.
        """
        import time
        store = self.tls_intercept_runtime._credential_state_store
        # Seed an entry inside the lead window (lead is 300s; this has 120s left).
        store._cache["google"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "STILL-VALID"},
            expires_at=time.monotonic() + 120,
        )
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            secrets = (await store.credential_for_slug(slug="google")).secrets

        self.assertEqual(secrets, {"access_token": "STILL-VALID"})
        # And the cache entry survives the failed refresh-ahead.
        self.assertEqual(store._cache["google"].secrets, {"access_token": "STILL-VALID"})

    async def test_invalidate_races_with_inflight_refresh(self) -> None:
        """Invalidate must serialize behind an in-flight refresh for the same slug.

        Without the store lock around fetch+apply, this sequence used to
        silently lose the invalidate:
          1. Proxy hot path's `_ensure_fresh` starts the HUMR refresh
             fetch (slow).
          2. User clicks Disconnect → `invalidate(slug)` clears the cache.
          3. Proxy's in-flight fetch resolves and writes a (now stale)
             entry back into the cache.
          4. The service's post-invalidate `refresh_slug` → `_ensure_fresh`
             reads the fresh-looking stale entry and returns without
             refetching.

        Correct behavior: invalidate waits for the in-flight refresh, the
        stale write lands, invalidate pops it, and the service's refresh
        starts from an empty cache and re-asks HUMR.
        """
        fetch_calls: list[str] = []
        started = asyncio.Event()
        delayed = asyncio.Event()

        async def first_stale(humr_client: object, slugs: list[str]) -> object:
            fetch_calls.append("first")
            started.set()
            await asyncio.wait_for(delayed.wait(), timeout=5)
            return _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "STALE-IN-FLIGHT"}, expires_in=3600,
                config={}, metadata={},
            ))

        async def second_absent(humr_client: object, slugs: list[str]) -> object:
            fetch_calls.append("second")
            return _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            ))

        fetches = [first_stale, second_absent]
        idx = 0
        async def dispatch(*args: object, **kwargs: object) -> object:
            nonlocal idx
            fn = fetches[min(idx, len(fetches) - 1)]
            idx += 1
            return await fn(*args, **kwargs)

        store = self.tls_intercept_runtime._credential_state_store
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", side_effect=dispatch):
            proxy_task = asyncio.create_task(store.credential_for_slug(slug="google"))
            await asyncio.wait_for(started.wait(), timeout=5)
            invalidate_task = asyncio.create_task(store.invalidate(slug="google"))
            await asyncio.sleep(0)  # let invalidate queue on the store lock
            delayed.set()  # release the proxy's in-flight refresh
            await proxy_task
            await invalidate_task

            # Service step: after a disconnect the service calls refresh. It
            # must see an empty cache and re-ask HUMR (second_absent fires here).
            await store.refresh(slug="google")

        self.assertEqual(fetch_calls, ["first", "second"])
        self.assertNotIn("google", store._cache)

    async def test_parked_refresh_all_cannot_overwrite_concurrent_invalidate(self) -> None:
        """A parked refresh_all() must not resurrect a token across a concurrent invalidate.

        Pre-single-lock repro (fetch happens outside any lock, then per-slug
        locks taken to apply):
          1. refresh_all() fetches outside the per-slug lock and parks at HUMR.
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

        async def parked_has_token(humr_client: object, slugs: list[str]) -> object:
            started.set()
            await asyncio.wait_for(delayed.wait(), timeout=5)
            return {
                slug: tls_token_store.RefreshResult(
                    outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                    secrets={"access_token": f"STALE-{slug}"}, expires_in=3600,
                    config={}, metadata={},
                )
                for slug in slugs
            }

        store = self.tls_intercept_runtime._credential_state_store
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", side_effect=parked_has_token):
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
        store = self.tls_intercept_runtime._credential_state_store
        store._cache["google"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
        )
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            credential = await store.credential_for_slug(slug="google")

        self.assertIsNone(credential)

    async def test_status_items_reflect_connection_state_not_token_expiry(self) -> None:
        """A connected provider whose injection token expired still renders connected.

        Status reads cache-independent connection state, not cache presence, and
        do NOT prune by token expiry — so an idle provider past its access-token
        TTL keeps a green card instead of flipping to not_connected.
        """
        import time
        store = self.tls_intercept_runtime._credential_state_store
        # An expired injection-cache entry (would be pruned on the injection path)...
        store._cache["google"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
        )
        # ...but cache-independent connection state says connected.
        store._connection_states["google"] = tls_token_store.ProviderConnectionState(
            connected=True,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={}, metadata={},
        )

        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "connected")
        self.assertEqual(items_by_slug["google"]["last_refreshed_at"], "2026-05-25T22:00:00+00:00")
        # Status reads are a pure projection: they do NOT prune the injection cache.
        self.assertIn("google", store._cache)

    async def test_gateway_env_snapshot_projects_connection_state(self) -> None:
        """Env snapshot lists connected providers + last-known config, independent of token expiry.

        A connected vault provider whose injection token expired must still appear
        (with its config) so the managed env block isn't stripped and the gateway
        isn't restarted without the integration; a disconnected provider is excluded.
        """
        import time
        store = self.tls_intercept_runtime._credential_state_store
        # Token cache expired, but connection state is connected with config.
        store._cache["telegram"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
        )
        store._connection_states["telegram"] = tls_token_store.ProviderConnectionState(
            connected=True,
            last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={"allowed_users": ["123"]},
            metadata={},
        )
        # A disconnected provider must be excluded.
        store._connection_states["slack"] = tls_token_store.ProviderConnectionState(
            connected=False, last_refreshed_at=None, config={}, metadata={},
        )

        snapshot = await self.tls_intercept_runtime.gateway_env_snapshot()
        by_slug = {provider.slug: config for provider, config in snapshot}
        self.assertEqual(by_slug["telegram"], {"allowed_users": ["123"]})
        self.assertNotIn("slack", by_slug)

    async def test_proxy_hot_path_prunes_expired_entry_after_transient(self) -> None:
        """Transient refresh on an expired entry must leave the cache empty."""
        import time
        store = self.tls_intercept_runtime._credential_state_store
        store._cache["google"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "EXPIRED"},
            expires_at=time.monotonic() - 10,
        )

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_TRANSIENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            credential = await store.credential_for_slug(slug="google")

        self.assertIsNone(credential)
        self.assertNotIn("google", store._cache)

    async def test_hot_path_has_token_marks_connection_connected(self) -> None:
        """A proxy hot-path refresh returning has_token marks the card connected (E2).

        Self-healing: a provider that was idle/never-refreshed turns green on the
        first real request, because the shared refresh path updates connection state too.
        """
        store = self.tls_intercept_runtime._credential_state_store
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "fresh"}, expires_in=3600, config={}, metadata={},
            )),
        ):
            secrets = (await store.credential_for_slug(slug="google")).secrets

        self.assertEqual(secrets, {"access_token": "fresh"})
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "connected")

    async def test_hot_path_absent_marks_connection_not_connected(self) -> None:
        """A proxy hot-path refresh returning absent flips a previously-connected card off (E2).

        A provider revoked/disconnected elsewhere stops showing connected the
        moment the proxy next tries to use it.
        """
        store = self.tls_intercept_runtime._credential_state_store
        store._connection_states["google"] = tls_token_store.ProviderConnectionState(
            connected=True, last_refreshed_at="2026-05-25T22:00:00+00:00", config={}, metadata={},
        )
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ):
            credential = await store.credential_for_slug(slug="google")

        self.assertIsNone(credential)
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "not_connected")

    async def test_evict_then_transient_keeps_card_connected_but_token_unavailable(self) -> None:
        """After a 401 evict + transient refresh the card stays connected while the token is gone (E3).

        `invalidate` (the 401-evict path) drops only the cache, not connection
        state; a transient follow-up leaves that state untouched. The card reads
        "connected" (last known good) while a proxy request would 503 — the
        deliberate trade for not eagerly disconnecting on a recoverable 401.
        """
        store = self.tls_intercept_runtime._credential_state_store
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"}, expires_in=3600, config={}, metadata={},
            )),
        ):
            await store.refresh(slug="google")
        # Upstream 401 evicts the token cache (but not connection state)...
        await store.invalidate(slug="google")
        # ...and the next refresh fails transiently.
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store._transient_result()),
        ):
            credential = await store.credential_for_slug(slug="google")

        self.assertIsNone(credential)  # a proxy request would 503
        items_by_slug = {item["slug"]: item for item in await self.tls_intercept_runtime.status_items()}
        self.assertEqual(items_by_slug["google"]["status"], "connected")  # card still connected

    async def test_refresh_endpoint_reloads_catalog_and_drops_tls_cache(self) -> None:
        """POST /integrations/refresh_all fans out catalog reload + all-providers TLS invalidate."""
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(refresh_payload={"ok": True, "tools": 12, "connectors": 3})
        parts = self._control_parts(aggregator=aggregator, org_slug="humanity-rules")

        google_connected = _batched(slug="google", result=tls_token_store.RefreshResult(
            outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"access_token": "fresh-token"},
            expires_in=3600,
            config={},
            metadata={},
        ))
        absent_for_every_slug = {
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }
        # First call pre-warms google; the route's invalidate-all then refreshes
        # every provider from HUMR, which reports them all disconnected.
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            side_effect=[google_connected, absent_for_every_slug],
        ), patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ):
            await self.tls_intercept_runtime.refresh_slug(slug="google")
            self.assertIn("google", self.tls_intercept_runtime._credential_state_store._cache)
            with TestClient(parts.app) as client:
                resp = client.post("/integrations/refresh_all")
                # A successful refresh arms the service-owned cooldown: an
                # immediate second press is rejected without touching the
                # catalog or the TLS cache again.
                resp_on_cooldown = client.post("/integrations/refresh_all")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "tools": 12, "connectors": 3})
        self.assertEqual(aggregator.refresh_calls, 1)
        self.assertEqual(self.tls_intercept_runtime._credential_state_store._cache, {})
        self.assertEqual(resp_on_cooldown.status_code, 429)
        self.assertEqual(resp_on_cooldown.json()["error"], "refresh_cooldown")
        self.assertEqual(aggregator.refresh_calls, 1)

    async def test_refresh_endpoint_cooldown_skips_tls_invalidate(self) -> None:
        """Cooldown 429 must short-circuit before invalidate_all fires (would kick the gateway)."""
        import time
        from starlette.testclient import TestClient

        aggregator = _StubAggregator(refresh_payload={"ok": True, "tools": 0, "connectors": 0})
        parts = self._control_parts(aggregator=aggregator, org_slug="humanity-rules")
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
        parts = self._control_parts(aggregator=aggregator, org_slug="humanity-rules")

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

        parts = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules")

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

        parts = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules")
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

    async def test_vault_setup_session_requests_submit_token_from_humr(self) -> None:
        """POST /integrations/tls_intercept/{provider}/setup-session asks HUMR for a submit token.

        The owner/app identity rides inside HumrClient.post_json (see
        TestHumrClient), so the service only supplies the provider fields.
        """
        from starlette.testclient import TestClient

        parts = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules")

        with patch.object(
            parts.humr_client,
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

        parts = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules")

        with patch.object(parts.humr_client, "post_json", return_value=(200, {"ok": True})) as post_mock:
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

        parts = self._control_parts(aggregator=_ready_stub_aggregator(), org_slug="humanity-rules")

        with patch.object(parts.humr_client, "post_json", return_value=(200, {"ok": True})) as post_mock:
            with patch.object(parts.service, "credentials_invalidate", new_callable=AsyncMock) as invalidate_mock:
                with TestClient(parts.app) as client:
                    resp = client.post("/integrations/tls_intercept/github/disconnect")

        self.assertEqual(resp.status_code, 200)
        # OAuth and vault disconnects now share one broker path and one HUMR
        # endpoint; HUMR resolves the provider kind and revokes upstream for
        # OAuth providers server-side.
        post_mock.assert_called_once_with(
            path="/api/integrations/credentials/disconnect",
            payload={"provider": "github"},
            timeout_seconds=30,
        )
        invalidate_mock.assert_awaited_once_with(slug="github")


class TestCredentialStateStore(unittest.IsolatedAsyncioTestCase):
    """The credential-state store fetches lazily and reuses secrets until near-expiry."""

    def setUp(self) -> None:
        self.credential_state_store = _make_credential_state_store()

    async def test_first_call_fetches_subsequent_calls_use_cache(self) -> None:
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"},
                expires_in=3600,
                config={},
                metadata={},
            )),
        ) as fetch_mock:
            first_secrets = (await self.credential_state_store.credential_for_slug(slug="google")).secrets
            self.assertEqual(first_secrets, {"access_token": "T1"})
            assert first_secrets is not None
            first_secrets["access_token"] = "caller-mutated"
            self.assertEqual((await self.credential_state_store.credential_for_slug(slug="google")).secrets, {"access_token": "T1"})
            fetch_mock.assert_called_once()

    async def test_refresh_when_within_lead_window(self) -> None:
        responses = [
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"},
                expires_in=3600,
                config={},
                metadata={},
            )),
            _batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T2"},
                expires_in=3600,
                config={},
                metadata={},
            )),
        ]
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", side_effect=responses):
            self.assertEqual((await self.credential_state_store.credential_for_slug(slug="google")).secrets, {"access_token": "T1"})
            # Backdate the cached entry past the lead window to force a refresh.
            self.credential_state_store._cache["google"].expires_at = self.credential_state_store._cache["google"].expires_at - 3600
            self.assertEqual((await self.credential_state_store.credential_for_slug(slug="google")).secrets, {"access_token": "T2"})

    async def test_unknown_slug_returns_none_without_fetching(self) -> None:
        with patch.object(tls_token_store, "fetch_provider_tokens_batch") as fetch_mock:
            self.assertIsNone(await self.credential_state_store.credential_for_slug(slug="unknown"))
            fetch_mock.assert_not_called()

    async def test_absent_outcome_leaves_cache_empty(self) -> None:
        """An `absent` outcome from HUMR yields no cache entry — disconnected = absent, not a sentinel."""
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ):
            self.assertIsNone(await self.credential_state_store.credential_for_slug(slug="google"))
        self.assertNotIn("google", self.credential_state_store._cache)

    async def test_connection_snapshot_deep_copies_config_and_metadata(self) -> None:
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "T1"},
                expires_in=3600,
                config={"nested": {"enabled": True}},
                metadata={"scopes": ["mail"]},
            )),
        ):
            await self.credential_state_store.refresh(slug="google")

        snapshot = await self.credential_state_store.connection_snapshot()
        snapshot["google"].config["nested"]["enabled"] = False
        snapshot["google"].metadata["scopes"].append("drive")

        fresh_snapshot = await self.credential_state_store.connection_snapshot()
        self.assertEqual(fresh_snapshot["google"].config, {"nested": {"enabled": True}})
        self.assertEqual(fresh_snapshot["google"].metadata, {"scopes": ["mail"]})


def _patched_humr_httpx_client(handler: Callable[[httpx.Request], httpx.Response], timeouts: list[int]) -> AbstractContextManager:
    """Patch humr_client's httpx.AsyncClient with a MockTransport-backed factory; records each client timeout."""
    # `humr_client.httpx` is the global httpx module, so the factory must hold
    # the real class — referencing `httpx.AsyncClient` inside it would resolve
    # to the patched attribute (itself).
    real_async_client = httpx.AsyncClient

    def make_client(timeout: int) -> httpx.AsyncClient:
        timeouts.append(timeout)
        return real_async_client(transport=httpx.MockTransport(handler), timeout=timeout)

    return patch.object(humr_client.httpx, "AsyncClient", make_client)


class TestHumrClient(unittest.IsolatedAsyncioTestCase):
    """HumrClient owns the bearer and merges the owner/app identity into every payload."""

    async def test_post_json_merges_identity_and_sends_bearer(self) -> None:
        import json as _json
        captured: dict = {}
        timeouts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(status_code=200, json={"ok": True})

        with _patched_humr_httpx_client(handler=handler, timeouts=timeouts):
            status, payload = await _make_humr_client().post_json(
                path="/api/integrations/credentials/disconnect",
                payload={"provider": "telegram"},
                timeout_seconds=30,
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(timeouts, [30])
        request = captured["request"]
        self.assertEqual(str(request.url), "https://humr.example/api/integrations/credentials/disconnect")
        self.assertEqual(request.headers["Authorization"], "Bearer env-bearer")
        self.assertEqual(
            _json.loads(request.content.decode("utf-8")),
            {"owner_username": "vmendi", "app_slug": "hermes", "provider": "telegram"},
        )

    async def test_network_error_maps_to_synthetic_502(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_humr_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 502)
        self.assertIn("error", payload)

    async def test_unparseable_success_body_maps_to_synthetic_502(self) -> None:
        """A 2xx with a non-JSON body degrades to the synthetic 502 — callers never see a parse error."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, content=b"<html>not json</html>")

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_humr_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 502)
        self.assertEqual(payload, {"error": "control plane request failed"})

    async def test_unparseable_error_body_keeps_real_status(self) -> None:
        """A non-2xx with a non-JSON body keeps its real status so callers can branch on it."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=503, content=b"<html>maintenance</html>")

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            status, payload = await _make_humr_client().post_json(path="/api/x", payload={}, timeout_seconds=30)
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"error": "control plane returned HTTP 503"})


class TestFetchProviderTokensBatch(unittest.IsolatedAsyncioTestCase):
    """Parse HUMR's `/api/integrations/tokens` response into a slug→RefreshResult map."""

    async def _run_with_response(self, payload: dict) -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=200, json=payload)

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            return await tls_token_store.fetch_provider_tokens_batch(
                humr_client=_make_humr_client(),
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

        self.assertEqual(results["google"].outcome, tls_token_store.REFRESH_OUTCOME_HAS_TOKEN)
        self.assertEqual(results["google"].secrets, {"access_token": "g-abc"})
        self.assertEqual(results["google"].expires_in, 3600)
        self.assertEqual(results["github"].outcome, tls_token_store.REFRESH_OUTCOME_ABSENT)
        self.assertEqual(results["telegram"].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)

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
                    "metadata": {"bot_username": "humr_bot"},
                },
            },
        })
        self.assertEqual(results["telegram"].config, {"allowed_users": ["42", "7"]})
        self.assertEqual(results["telegram"].metadata, {"bot_username": "humr_bot"})

    async def test_has_token_with_non_dict_config_or_metadata_is_coerced_empty(self) -> None:
        """Malformed config/metadata must not poison cache-independent connection state."""
        results = await self._run_with_response(payload={
            "results": {
                "google": {
                    "outcome": "has_token",
                    "secrets": {"access_token": "tok"},
                    "expires_in": 3600,
                    "config": "nope",
                    "metadata": ["nope"],
                },
                "github": {"outcome": "absent"},
                "telegram": {"outcome": "absent"},
            },
        })
        self.assertEqual(results["google"].outcome, tls_token_store.REFRESH_OUTCOME_HAS_TOKEN)
        self.assertEqual(results["google"].config, {})
        self.assertEqual(results["google"].metadata, {})

    async def test_google_grants_metadata_passes_through(self) -> None:
        """The scope-picker capability projection rides metadata to connection state and the card."""
        grants = {"products": {"gmail": "write"}, "raw_scopes": ["x"], "google_email": "v@x.com"}
        results = await self._run_with_response(payload={
            "results": {
                "google": {
                    "outcome": "has_token",
                    "secrets": {"access_token": "tok"},
                    "expires_in": 3600,
                    "metadata": {"google_grants": grants},
                },
                "github": {"outcome": "absent"},
                "telegram": {"outcome": "absent"},
            },
        })
        self.assertEqual(results["google"].metadata, {"google_grants": grants})

    async def test_slug_missing_from_response_is_transient(self) -> None:
        """A partial server response must NOT clear the broker's cache for the missing slug."""
        results = await self._run_with_response(payload={"results": {"google": {"outcome": "absent"}}})
        self.assertEqual(results["github"].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)
        self.assertEqual(results["telegram"].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)

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
                tls_token_store.REFRESH_OUTCOME_TRANSIENT,
                msg=f"bad_secrets={bad_secrets!r} should be transient",
            )
            self.assertIsNone(results["google"].secrets)

    async def test_network_error_returns_transient_for_every_slug(self) -> None:
        """Any transport failure must surface as transient across the board, preserving the cache."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            results = await tls_token_store.fetch_provider_tokens_batch(
                humr_client=_make_humr_client(),
                slugs=["google", "github", "telegram"],
            )
        for slug in ("google", "github", "telegram"):
            self.assertEqual(results[slug].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)

    async def test_http_error_returns_transient_for_every_slug(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code=500, content=b"x")

        with _patched_humr_httpx_client(handler=handler, timeouts=[]):
            results = await tls_token_store.fetch_provider_tokens_batch(
                humr_client=_make_humr_client(),
                slugs=["google", "github"],
            )
        self.assertEqual(results["google"].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)
        self.assertEqual(results["github"].outcome, tls_token_store.REFRESH_OUTCOME_TRANSIENT)


class TestRefreshAllBatchedApply(unittest.IsolatedAsyncioTestCase):
    """`CredentialStateStore.refresh_all` applies every outcome under its single lock."""

    async def test_has_token_writes_absent_drops_transient_leaves(self) -> None:
        """One batched call covers all three cache and connection-state outcomes."""
        import time
        store = _make_credential_state_store()
        store._cache["github"] = tls_token_store._TokenCacheEntry(
            secrets={"access_token": "PRIOR-GITHUB"},
            expires_at=time.monotonic() + 600,
        )
        # GitHub is the transient slug: its prior connected state must survive.
        store._connection_states["github"] = tls_token_store.ProviderConnectionState(
            connected=True, last_refreshed_at="2026-05-25T22:00:00+00:00", config={}, metadata={},
        )

        # refresh_all() batches every configured provider, so the mock must
        # return a result for each one. Default every slug to ABSENT, then
        # override the three the test actually exercises. Building from the
        # registry keeps this robust when new TLS-intercept providers are added.
        batched_results = {
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }
        batched_results["google"] = tls_token_store.RefreshResult(
            outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"access_token": "G"}, expires_in=3600, config={}, metadata={},
        )
        batched_results["github"] = tls_token_store.RefreshResult(
            outcome=tls_token_store.REFRESH_OUTCOME_TRANSIENT,
            secrets=None, expires_in=None, config={}, metadata={},
        )
        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=batched_results,
        ) as batch_mock:
            await store.refresh_all()

        self.assertEqual(batch_mock.call_count, 1)
        self.assertEqual(store._cache["google"].secrets, {"access_token": "G"})
        self.assertEqual(store._cache["github"].secrets, {"access_token": "PRIOR-GITHUB"})  # transient ⇒ preserved
        self.assertNotIn("telegram", store._cache)
        # Connection state tracks the same three outcomes: has_token ⇒ connected,
        # absent ⇒ not-connected, transient ⇒ prior state preserved.
        self.assertTrue(store._connection_states["google"].connected)
        self.assertTrue(store._connection_states["github"].connected)  # transient ⇒ prior connected state preserved
        self.assertFalse(store._connection_states["telegram"].connected)


class TestGatewayEnvRender(unittest.TestCase):
    """Render the HUMR-managed block from a TLS-runtime snapshot."""

    def _telegram_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]

    def _google_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["google"]

    def _github_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["github"]

    def _slack_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["slack"]

    def _openrouter_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["openrouter"]

    def _openai_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["openai-api"]

    def _anthropic_provider(self) -> "tls_provider_catalog.TlsProviderSpec":
        return tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["anthropic"]

    def test_slack_vault_header_provider_renders_both_placeholders(self) -> None:
        """Env bindings render static placeholders plus list config."""
        snapshot = [(self._slack_provider(), {"allowed_users": ["U1", "U2"], "home_channel": "DOWNER"})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("SLACK_APP_TOKEN=xapp-HUMR_PLACEHOLDER", block)
        self.assertIn("SLACK_BOT_TOKEN=xoxb-HUMR_PLACEHOLDER", block)
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
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER", block)
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
        self.assertIn("GITHUB_TOKEN=HUMR_PLACEHOLDER", block)
        self.assertNotIn("COPILOT_GITHUB_TOKEN", block)

    def test_connected_openrouter_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._openrouter_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("OPENROUTER_API_KEY=HUMR_PLACEHOLDER", block)

    def test_connected_openai_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._openai_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("OPENAI_API_KEY=HUMR_PLACEHOLDER", block)

    def test_connected_anthropic_renders_api_key_placeholder(self) -> None:
        snapshot = [(self._anthropic_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("ANTHROPIC_API_KEY=HUMR_PLACEHOLDER", block)

    def test_missing_list_config_skips_binding(self) -> None:
        """A connected provider without the optional list field omits its env var."""
        snapshot = [(self._telegram_provider(), {})]
        block = credentials_service._render_managed_block(snapshot=snapshot)
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER", block)
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
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER", text)
        self.assertIn("TELEGRAM_ALLOWED_USERS=1", text)

    def test_write_empty_block_strips_sentinels_entirely(self) -> None:
        """Disconnect path: empty managed block leaves no HUMR-managed sentinels."""
        with tempfile.TemporaryDirectory() as tmp:
            env_path = pathlib.Path(tmp) / ".env"
            env_path.write_text(
                "USER_KEY=keep-me\n"
                f"{credentials_service.GATEWAY_ENV_BLOCK_BEGIN}\n"
                "TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER\n"
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

        Credential-change choreography (HUMR refresh, env render, process
        restarts) belongs to CredentialsService. Both the runtime's public
        invalidate and the proxy 401-eviction path (inner credential-state store) only
        touch the cache, so a 401-eviction during normal traffic can never
        trigger a gateway restart.
        """
        runtime = self._make_runtime()
        with patch.object(tls_token_store, "fetch_provider_tokens_batch") as fetch_mock:
            await runtime.invalidate(slug="telegram")
            await runtime.invalidate_all()
            await runtime._credential_state_store.invalidate(slug="telegram")
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

    async def test_apply_auth_marker_follows_provider_spec(self) -> None:
        """Auth-marker orchestration reads provider policy rather than a separate slug registry."""
        service = self._make_service()
        service._providers = {
            **service._providers,
            "openai-codex": dataclasses.replace(service._providers["openai-codex"], sync_auth_marker=False),
        }

        with patch.object(credentials_service, "_run_provider_auth_marker", return_value=True) as auth_marker_mock:
            await service._apply_auth_marker(provider="openai-codex", action="connect")
            await service._apply_auth_marker(provider="nous", action="connect")

        auth_marker_mock.assert_called_once_with(
            provider="nous",
            action="connect",
            webui_python=pathlib.Path("/nonexistent/webui-python"),
            runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
            hermes_home=pathlib.Path("/nonexistent/hermes-home"),
        )

    async def test_per_slug_invalidate_refreshes_only_that_slug(self) -> None:
        """Slug-targeted invalidate must NOT fan out to disconnected providers.

        Connecting one provider used to spam HUMR with `no integration row`
        404s for every other (still disconnected) provider. The service
        narrows to `refresh_slug(slug)` when a slug is named, so HUMR only
        hears about the one that actually changed.
        """
        service = self._make_service()

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="google", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "t"}, expires_in=3600, config={}, metadata={},
            )),
        ) as fetch_mock:
            await service.credentials_invalidate(slug="google")

        # Exactly one HUMR round-trip and only for the named slug, not one per provider.
        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(fetch_mock.call_args.kwargs["slugs"], ["google"])

    async def test_github_invalidate_rewrites_env_and_restarts_webui(self) -> None:
        """GitHub connect/disconnect reloads WebUI so provider env is re-read."""
        service = self._make_service()
        self.webui_state_dir.mkdir(parents=True)
        models_cache = self.webui_state_dir / "models_cache.json"
        models_cache.write_text("stale", encoding="utf-8")

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="github", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "ghu_token"}, expires_in=3600, config={}, metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="github")

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("GITHUB_TOKEN=HUMR_PLACEHOLDER", text)
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
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openrouter", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"api_key": "sk-or-v1-real"}, expires_in=3600, config={}, metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            await service.credentials_invalidate(slug="openrouter")

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("OPENROUTER_API_KEY=HUMR_PLACEHOLDER", text)
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
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openai-codex", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
                secrets={"access_token": "codex-access", "chatgpt_account_id": "account-id"},
                expires_in=3600,
                config={},
                metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock, patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ) as auth_marker_mock:
            await service.credentials_invalidate(slug="openai-codex")

        self.assertFalse(models_cache.exists())
        restart_mock.assert_not_called()
        auth_marker_mock.assert_called_once_with(
            provider="openai-codex",
            action="connect",
            webui_python=pathlib.Path("/nonexistent/webui-python"),
            runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
            hermes_home=pathlib.Path("/nonexistent/hermes-home"),
        )

    async def test_codex_invalidate_disconnects_auth_marker_when_absent(self) -> None:
        """A successful refresh that says disconnected must clear WebUI's local marker."""
        service = self._make_service()
        self.webui_state_dir.mkdir(parents=True)
        models_cache = self.webui_state_dir / "models_cache.json"
        models_cache.write_text("stale", encoding="utf-8")

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="openai-codex", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None,
                expires_in=None,
                config={},
                metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock, patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ) as auth_marker_mock:
            await service.credentials_invalidate(slug="openai-codex")

        self.assertFalse(models_cache.exists())
        restart_mock.assert_not_called()
        auth_marker_mock.assert_called_once_with(
            provider="openai-codex",
            action="disconnect",
            webui_python=pathlib.Path("/nonexistent/webui-python"),
            runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
            hermes_home=pathlib.Path("/nonexistent/hermes-home"),
        )

    async def test_disconnect_marks_card_disconnected_even_when_refresh_is_transient(self) -> None:
        """A confirmed disconnect flips the card off even if the follow-up refresh is transient (E4).

        HUMR authoritatively deleted the row, so the broker marks connection state
        disconnected directly; a transient `/tokens` refresh (which leaves
        connection state untouched) must not leave the card showing connected.
        """
        runtime = self._make_runtime()
        service = _make_credentials_service(
            tls_intercept_runtime=runtime,
            gateway_env_path=self.env_path,
            webui_state_dir=self.webui_state_dir,
        )
        # Telegram starts connected.
        runtime._credential_state_store._connection_states["telegram"] = tls_token_store.ProviderConnectionState(
            connected=True, last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={"allowed_users": ["123"]}, metadata={},
        )

        with patch.object(service._humr_client, "post_json", return_value=(200, {"ok": True})), patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="telegram", result=tls_token_store._transient_result()),
        ):
            status, _payload = await service.credentials_disconnect(provider="telegram")

        self.assertEqual(status, 200)
        items_by_slug = {item["slug"]: item for item in await runtime.status_items()}
        self.assertEqual(items_by_slug["telegram"]["status"], "not_connected")

    async def test_disconnect_removes_env_block_and_restarts_on_normal_absent_refresh(self) -> None:
        """The normal disconnect (HUMR 200 + absent refresh) still strips the env block and restarts.

        Proves the env-render/restart choreography runs through `credentials_disconnect`, not just the
        lower-level `credentials_invalidate` path the other tests exercise.
        """
        runtime = self._make_runtime()
        service = _make_credentials_service(
            tls_intercept_runtime=runtime,
            gateway_env_path=self.env_path,
            webui_state_dir=self.webui_state_dir,
        )
        # Telegram starts connected, with its managed env block already on disk.
        runtime._credential_state_store._connection_states["telegram"] = tls_token_store.ProviderConnectionState(
            connected=True, last_refreshed_at="2026-05-25T22:00:00+00:00",
            config={"allowed_users": ["123"]}, metadata={},
        )
        spec = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]
        seeded = credentials_service._render_managed_block(snapshot=[(spec, {"allowed_users": ["123"]})])
        credentials_service._write_gateway_env_file(env_path=self.env_path, managed_block=seeded)
        self.assertIn("TELEGRAM_BOT_TOKEN", self.env_path.read_text(encoding="utf-8"))

        with patch.object(service._humr_client, "post_json", return_value=(200, {"ok": True})), patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="telegram", result=tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )),
        ), patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ) as restart_mock:
            status, _payload = await service.credentials_disconnect(provider="telegram")

        self.assertEqual(status, 200)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", self.env_path.read_text(encoding="utf-8"))
        restart_mock.assert_called_once_with(
            process_compose_url="http://127.0.0.1:9999",
            process_name=credentials_service.GATEWAY_PROCESS_NAME,
        )
        items_by_slug = {item["slug"]: item for item in await runtime.status_items()}
        self.assertEqual(items_by_slug["telegram"]["status"], "not_connected")

    async def test_invalidate_all_uses_single_batched_call(self) -> None:
        """Explicit Refresh-all collapses to one HUMR round-trip across every provider.

        The previous per-slug fan-out emitted one `INFO no integration row`
        Django log line per disconnected provider on every Refresh-all.
        Coalescing into a single POST (where `absent` is a normal entry,
        not a 4xx) makes that log line disappear.
        """
        service = self._make_service()

        absent_for_every_slug = {
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=absent_for_every_slug,
        ) as batch_mock, patch.object(
            credentials_service,
            "_post_process_compose_restart",
            return_value=(200, "ok"),
        ), patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ) as auth_marker_mock:
            await service.refresh_all_integrations()

        self.assertEqual(batch_mock.call_count, 1)
        called_slugs = batch_mock.call_args.kwargs["slugs"]
        self.assertEqual(set(called_slugs), set(tls_provider_catalog.TLS_INTERCEPT_PROVIDERS))
        self.assertEqual(
            auth_marker_mock.call_args_list,
            [
                call(
                    provider="nous",
                    action="disconnect",
                    webui_python=pathlib.Path("/nonexistent/webui-python"),
                    runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
                    hermes_home=pathlib.Path("/nonexistent/hermes-home"),
                ),
                call(
                    provider="openai-codex",
                    action="disconnect",
                    webui_python=pathlib.Path("/nonexistent/webui-python"),
                    runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
                    hermes_home=pathlib.Path("/nonexistent/hermes-home"),
                ),
            ],
        )


class TestTransientRefreshGuards(unittest.IsolatedAsyncioTestCase):
    """An all-transient HUMR refresh must never clobber a good managed env block.

    Regression: the broker booted while HUMR returned 503, the bootstrap
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
        spec = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS["telegram"]
        block = credentials_service._render_managed_block(snapshot=[(spec, {"allowed_users": ["123"]})])
        credentials_service._write_gateway_env_file(env_path=self.env_path, managed_block=block)
        return self.env_path.read_text(encoding="utf-8")

    def _transient_for_every_slug(self) -> dict:
        return {
            slug: tls_token_store._transient_result()
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }

    async def test_refresh_all_reports_humr_reachability(self) -> None:
        tls_intercept_runtime = self._make_runtime()
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", return_value=self._transient_for_every_slug()):
            self.assertFalse(await tls_intercept_runtime.refresh_all())
        absent_for_every_slug = {
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }
        with patch.object(tls_token_store, "fetch_provider_tokens_batch", return_value=absent_for_every_slug):
            self.assertTrue(await tls_intercept_runtime.refresh_all())

    async def test_transient_invalidate_keeps_env_file_and_skips_restart(self) -> None:
        original = self._seed_telegram_env_block()
        service = self._make_service()

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=_batched(slug="telegram", result=tls_token_store._transient_result()),
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
            tls_token_store,
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
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }
        refreshed_results["telegram"] = tls_token_store.RefreshResult(
            outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"bot_token": "123:abc"}, expires_in=3600, config={"allowed_users": ["123"]}, metadata={},
        )

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=refreshed_results,
        ), patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ):
            await service.bootstrap()

        text = self.env_path.read_text(encoding="utf-8")
        self.assertIn("TELEGRAM_BOT_TOKEN=000000:HUMR_PLACEHOLDER", text)
        self.assertIn("TELEGRAM_ALLOWED_USERS=123", text)

    async def test_bootstrap_success_applies_connected_codex_auth_marker(self) -> None:
        """A pre-existing HUMR-side Codex connection must appear in WebUI's picker after boot."""
        service = self._make_service()
        refreshed_results = {
            slug: tls_token_store.RefreshResult(
                outcome=tls_token_store.REFRESH_OUTCOME_ABSENT,
                secrets=None, expires_in=None, config={}, metadata={},
            )
            for slug in tls_provider_catalog.TLS_INTERCEPT_PROVIDERS
        }
        refreshed_results["openai-codex"] = tls_token_store.RefreshResult(
            outcome=tls_token_store.REFRESH_OUTCOME_HAS_TOKEN,
            secrets={"access_token": "codex-access", "chatgpt_account_id": "account-id"},
            expires_in=3600,
            config={},
            metadata={},
        )

        with patch.object(
            tls_token_store,
            "fetch_provider_tokens_batch",
            return_value=refreshed_results,
        ), patch.object(
            credentials_service,
            "_run_provider_auth_marker",
            return_value=True,
        ) as auth_marker_mock:
            await service.bootstrap()

        self.assertEqual(
            auth_marker_mock.call_args_list,
            [
                call(
                    provider="nous",
                    action="disconnect",
                    webui_python=pathlib.Path("/nonexistent/webui-python"),
                    runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
                    hermes_home=pathlib.Path("/nonexistent/hermes-home"),
                ),
                call(
                    provider="openai-codex",
                    action="connect",
                    webui_python=pathlib.Path("/nonexistent/webui-python"),
                    runtime_dir=pathlib.Path("/nonexistent/humr-runtime"),
                    hermes_home=pathlib.Path("/nonexistent/hermes-home"),
                ),
            ],
        )
