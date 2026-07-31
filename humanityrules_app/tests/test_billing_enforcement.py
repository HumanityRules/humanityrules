"""Tests for the broker's credit enforcement (billing_entitlement + the 402 path).

Three layers, kept apart:

- `EntitlementCache` as a pure state machine over control-plane answers: which
  triggers refresh it, what it does when the control plane will not answer, and
  the fail-open floor when it has never been told anything.
- the 402 body, which the agent reads out loud in chat, including the renewal
  sentence that must not appear on a plan that never renews.
- the intercept wiring: exhaustion refuses exactly the requests that would have
  been metered, and nothing else — customer-funded model calls and connector
  traffic consume no credits and are never blocked.
"""

import asyncio
import json
import pathlib
import ssl
import sys
import unittest
from unittest.mock import AsyncMock, patch

_INTEGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "template_repos" / "hermes_agent" / "humr_runtime" / "integrations"
if str(_INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS_DIR))

import billing_entitlement  # noqa: E402
import humr_client  # noqa: E402
import tls_http_message_relay  # noqa: E402
import tls_intercept  # noqa: E402
import tls_provider_catalog  # noqa: E402
import tls_token_store  # noqa: E402
import tls_usage_metering  # noqa: E402


def _snapshot(credits_remaining: int, exhausted: bool, renewal_date: str | None) -> dict:
    return {
        "credits_remaining": credits_remaining,
        "monthly_grant": 2000,
        "renewal_date": renewal_date,
        "plan": "operator",
        "exhausted": exhausted,
    }


def _make_cache(get_json: AsyncMock) -> billing_entitlement.EntitlementCache:
    """An entitlement cache over a HumrClient whose GET is under the test's control."""
    client = humr_client.HumrClient(
        control_plane_url="https://humr.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )
    client.get_json = get_json
    return billing_entitlement.EntitlementCache(humr_client=client)


class TestSnapshotParsing(unittest.TestCase):
    """One parser serves both refresh triggers; a partial snapshot is not a snapshot."""

    def test_reads_the_snapshot_from_the_entitlement_key(self) -> None:
        parsed = billing_entitlement.parse_snapshot(body={"ok": True, "entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date="2026-08-15",
        )})

        self.assertEqual(parsed, {
            "credits_remaining": 1200,
            "monthly_grant": 2000,
            "renewal_date": "2026-08-15",
            "plan": "operator",
            "exhausted": False,
        })

    def test_negative_balances_parse(self) -> None:
        """Enforcement stops at −10% of the grant, so remaining is legitimately negative."""
        parsed = billing_entitlement.parse_snapshot(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        self.assertEqual(parsed["credits_remaining"], -200)

    def test_bodies_without_a_usable_snapshot_parse_to_none(self) -> None:
        for body in (
            None,
            {},
            {"entitlement": None},
            {"entitlement": {}},
            {"entitlement": {**_snapshot(credits_remaining=1, exhausted=False, renewal_date=None), "exhausted": "no"}},
            {"entitlement": {**_snapshot(credits_remaining=1, exhausted=False, renewal_date=None), "credits_remaining": True}},
            {"entitlement": {**_snapshot(credits_remaining=1, exhausted=False, renewal_date=None), "monthly_grant": "2000"}},
            {"entitlement": {**_snapshot(credits_remaining=1, exhausted=False, renewal_date=None), "renewal_date": 20260815}},
            {"entitlement": {**_snapshot(credits_remaining=1, exhausted=False, renewal_date=None), "plan": None}},
        ):
            with self.subTest(body=body):
                self.assertIsNone(billing_entitlement.parse_snapshot(body=body))


class TestRefusalBody(unittest.TestCase):
    """The 402 the agent relays into the conversation."""

    def test_trial_exhaustion_offers_upgrade_only(self) -> None:
        body = billing_entitlement.refusal_body(
            snapshot=_snapshot(credits_remaining=-50, exhausted=True, renewal_date=None),
            upgrade_url="https://humr.example/settings/billing/",
        )

        self.assertEqual(body["error"]["type"], "insufficient_credits")
        self.assertEqual(body["error"]["code"], "credits_exhausted")
        self.assertEqual(body["upgrade_url"], "https://humr.example/settings/billing/")
        self.assertEqual(body["entitlement"]["credits_remaining"], -50)
        message = body["error"]["message"]
        self.assertIn("out of credits", message)
        self.assertIn("https://humr.example/settings/billing/", message)
        # A one-time trial grant never renews, so telling the user to wait would lie.
        self.assertNotIn("renew", message)

    def test_renewing_plan_also_offers_waiting(self) -> None:
        body = billing_entitlement.refusal_body(
            snapshot=_snapshot(credits_remaining=-200, exhausted=True, renewal_date="2026-08-15"),
            upgrade_url="https://humr.example/settings/billing/",
        )

        self.assertIn("renew on 2026-08-15", body["error"]["message"])


class TestEntitlementCache(unittest.IsolatedAsyncioTestCase):

    async def test_no_snapshot_fails_open_without_calling_the_control_plane(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        cache = _make_cache(get_json=get_json)

        self.assertIsNone(await cache.refusal_for_metered_request())
        get_json.assert_not_awaited()

    async def test_report_response_replaces_the_cache(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        cache = _make_cache(get_json=get_json)

        cache.absorb_report_response(body={"ok": True, "entitlement": _snapshot(
            credits_remaining=1500, exhausted=False, renewal_date=None,
        )})

        self.assertIsNone(await cache.refusal_for_metered_request())
        # A healthy balance is answered from cache; the control plane is untouched.
        get_json.assert_not_awaited()
        self.assertEqual((await cache.snapshot_for_display())["credits_remaining"], 1500)

    async def test_unusable_report_response_keeps_the_previous_snapshot(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=900, exhausted=False, renewal_date=None,
        )})

        cache.absorb_report_response(body={"ok": True})

        self.assertEqual((await cache.snapshot_for_display())["credits_remaining"], 900)

    async def test_exhaustion_rechecks_the_control_plane_and_refuses(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        refusal = await cache.refusal_for_metered_request()

        get_json.assert_awaited_once()
        self.assertEqual(get_json.await_args.kwargs["path"], billing_entitlement.ENTITLEMENT_PATH)
        self.assertEqual(refusal["error"]["code"], "credits_exhausted")

    async def test_upgrade_unblocks_on_the_very_next_request(self) -> None:
        """Exhaustion never trusts the cache — the recheck is what makes upgrades instant."""
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=2000, exhausted=False, renewal_date="2026-08-15",
        )}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        self.assertIsNone(await cache.refusal_for_metered_request())
        get_json.assert_awaited_once()
        # The fresh answer replaced the cache, so the next request needs no call.
        self.assertIsNone(await cache.refusal_for_metered_request())
        get_json.assert_awaited_once()

    async def test_unreachable_control_plane_keeps_refusing_on_last_known_state(self) -> None:
        for failure in ((502, {"error": "control plane request failed"}), (200, {"ok": True})):
            with self.subTest(failure=failure):
                get_json = AsyncMock(return_value=failure)
                cache = _make_cache(get_json=get_json)
                cache.absorb_report_response(body={"entitlement": _snapshot(
                    credits_remaining=-200, exhausted=True, renewal_date=None,
                )})

                refusal = await cache.refusal_for_metered_request()

                get_json.assert_awaited_once()
                self.assertEqual(refusal["error"]["code"], "credits_exhausted")

    async def test_concurrent_exhausted_requests_share_one_round_trip(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_get(path: str, timeout_seconds: int) -> tuple[int, dict]:
            started.set()
            await release.wait()
            return 200, {"entitlement": _snapshot(credits_remaining=-200, exhausted=True, renewal_date=None)}

        get_json = AsyncMock(side_effect=_slow_get)
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        pending = [asyncio.create_task(cache.refusal_for_metered_request()) for _ in range(5)]
        await started.wait()
        release.set()
        refusals = await asyncio.gather(*pending)

        self.assertEqual(get_json.await_count, 1)
        self.assertTrue(all(refusal is not None for refusal in refusals))

    async def test_display_fetches_for_an_organization_that_has_never_spent(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=500, exhausted=False, renewal_date=None,
        )}))
        cache = _make_cache(get_json=get_json)

        snapshot = await cache.snapshot_for_display()

        get_json.assert_awaited_once()
        self.assertEqual(snapshot["credits_remaining"], 500)

    async def test_display_serves_a_fresh_cache_without_calling_the_control_plane(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        self.assertEqual((await cache.snapshot_for_display())["credits_remaining"], 1200)
        get_json.assert_not_awaited()

    async def test_display_refreshes_a_stale_cache(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=300, exhausted=False, renewal_date=None,
        )}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        with patch.object(billing_entitlement, "_DISPLAY_STALE_SECONDS", 0):
            snapshot = await cache.snapshot_for_display()

        get_json.assert_awaited_once()
        self.assertEqual(snapshot["credits_remaining"], 300)

    async def test_display_serves_the_stale_copy_when_the_refresh_fails(self) -> None:
        get_json = AsyncMock(return_value=(502, {"error": "control plane request failed"}))
        cache = _make_cache(get_json=get_json)
        cache.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        with patch.object(billing_entitlement, "_DISPLAY_STALE_SECONDS", 0):
            snapshot = await cache.snapshot_for_display()

        self.assertEqual(snapshot["credits_remaining"], 1200)

    async def test_display_is_none_when_humr_has_never_answered(self) -> None:
        cache = _make_cache(get_json=AsyncMock(return_value=(502, {"error": "control plane request failed"})))

        self.assertIsNone(await cache.snapshot_for_display())

    async def test_upgrade_url_points_at_the_control_plane_billing_page(self) -> None:
        cache = _make_cache(get_json=AsyncMock(return_value=(200, {})))

        self.assertEqual(cache.upgrade_url(), "https://humr.example/settings/billing/")


class TestReporterFeedsTheCache(unittest.IsolatedAsyncioTestCase):
    """The report response is the refresh trigger that keeps a spending agent current."""

    async def test_accepted_report_hands_its_entitlement_on(self) -> None:
        absorbed: list[dict] = []
        client = humr_client.HumrClient(
            control_plane_url="https://humr.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        )
        payload = {"ok": True, "entitlement": _snapshot(credits_remaining=140, exhausted=False, renewal_date=None)}
        client.post_json = AsyncMock(return_value=(200, payload))
        reporter = tls_usage_metering.UsageReporter(humr_client=client, record_entitlement=absorbed.append)
        reporter.record({"idempotency_key": "a"})

        await reporter.flush()

        self.assertEqual(absorbed, [payload])

    async def test_rejected_report_hands_nothing_on(self) -> None:
        absorbed: list[dict] = []
        client = humr_client.HumrClient(
            control_plane_url="https://humr.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        )
        client.post_json = AsyncMock(return_value=(502, {"error": "control plane request failed"}))
        reporter = tls_usage_metering.UsageReporter(humr_client=client, record_entitlement=absorbed.append)
        reporter.record({"idempotency_key": "a"})

        await reporter.flush()

        self.assertEqual(absorbed, [])


class TestMeteredPredicate(unittest.TestCase):
    """Enforcement refuses exactly what metering would have charged for."""

    def test_platform_funded_parsed_provider_is_metered(self) -> None:
        self.assertTrue(tls_usage_metering.request_is_metered(provider_slug="openai-codex", platform_shared=True))

    def test_customer_funded_model_call_is_not_metered(self) -> None:
        self.assertFalse(tls_usage_metering.request_is_metered(provider_slug="openai-codex", platform_shared=False))

    def test_connector_traffic_is_not_metered(self) -> None:
        for slug in ("tavily", "github", "slack"):
            with self.subTest(slug=slug):
                self.assertFalse(tls_usage_metering.request_is_metered(provider_slug=slug, platform_shared=True))


class _StubTransport:

    def __init__(self) -> None:
        self.protocol = object()

    def get_protocol(self) -> object:
        return self.protocol


class _RecordingWriter:

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.transport = _StubTransport()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("test-client", 12345) if name == "peername" else None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def all_bytes(self) -> bytes:
        return b"".join(self.chunks)


class _StubProviderWriter:

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    def all_bytes(self) -> bytes:
        return b"".join(self.chunks)


class _StubCertMinter:

    def context_for(self, hostname: str) -> ssl.SSLContext:
        return ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)


class _StubCredentialStateStore:

    def __init__(self, secrets: dict[str, str], platform_shared: bool) -> None:
        self._secrets = secrets
        self._platform_shared = platform_shared

    async def credential_for_slug(self, slug: str) -> tls_token_store.ActiveCredential:
        return tls_token_store.ActiveCredential(secrets=self._secrets, platform_shared=self._platform_shared)

    async def invalidate(self, slug: str) -> None:
        return None


class _AlwaysRefusing:
    """Entitlement cache stand-in that refuses every metered request."""

    def __init__(self) -> None:
        self.asked = 0

    async def refusal_for_metered_request(self) -> dict:
        self.asked += 1
        return billing_entitlement.refusal_body(
            snapshot=_snapshot(credits_remaining=-200, exhausted=True, renewal_date=None),
            upgrade_url="https://humr.example/settings/billing/",
        )


class _Exploding:
    """Entitlement cache stand-in whose check raises — the proxy must not care."""

    async def refusal_for_metered_request(self) -> dict:
        raise RuntimeError("billing boom")


class TestExhaustedInterceptWiring(unittest.IsolatedAsyncioTestCase):
    """Exhaustion at the proxy: what gets a 402, what sails through untouched."""

    async def _intercept(
        self,
        provider_slug: str,
        host: str,
        secrets: dict[str, str],
        platform_shared: bool,
        request_headers: bytes,
        entitlement_cache: object | None,
    ) -> tuple[_RecordingWriter, _StubProviderWriter]:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS[provider_slug]
        sandbox_reader = asyncio.StreamReader()
        sandbox_reader.feed_data(
            b"POST /backend-api/codex/responses HTTP/1.1\r\n"
            b"Host: " + host.encode() + b"\r\n"
            + request_headers +
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"{}"
        )
        sandbox_reader.feed_eof()
        sandbox_writer = _RecordingWriter()
        provider_reader = asyncio.StreamReader()
        provider_reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        provider_reader.feed_eof()
        provider_writer = _StubProviderWriter()

        async def _fake_open_connection(**kwargs: object) -> tuple[asyncio.StreamReader, _StubProviderWriter]:
            return provider_reader, provider_writer

        loop = asyncio.get_running_loop()
        with (
            patch.object(loop, "start_tls", AsyncMock(return_value=sandbox_writer.transport)),
            patch.object(tls_intercept.asyncio, "StreamWriter", return_value=sandbox_writer),
            patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection),
        ):
            await asyncio.wait_for(
                tls_intercept._serve_intercepted_connection(
                    sandbox_reader=sandbox_reader,
                    sandbox_writer=sandbox_writer,
                    provider_host=host,
                    provider_port=443,
                    provider=provider,
                    minter=_StubCertMinter(),
                    credential_state_store=_StubCredentialStateStore(secrets=secrets, platform_shared=platform_shared),
                    usage_reporter=None,
                    entitlement_cache=entitlement_cache,
                ),
                timeout=1.0,
            )
        return sandbox_writer, provider_writer

    async def test_metered_request_is_refused_with_a_structured_402(self) -> None:
        cache = _AlwaysRefusing()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=True,
            request_headers=b"",
            entitlement_cache=cache,
        )

        # The CONNECT acknowledgement precedes the refusal on the same writer.
        _connect_ack, _, refusal_response = sandbox_writer.all_bytes().partition(b"\r\n\r\n")
        self.assertTrue(refusal_response.startswith(b"HTTP/1.1 402 Payment Required\r\n"))
        payload = json.loads(refusal_response.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(payload["error"]["code"], "credits_exhausted")
        self.assertEqual(payload["entitlement"]["exhausted"], True)
        self.assertEqual(payload["upgrade_url"], "https://humr.example/settings/billing/")
        # Refused before forwarding: the provider never saw the request.
        self.assertEqual(provider_writer.all_bytes(), b"")

    async def test_customer_funded_model_call_is_never_refused(self) -> None:
        """An org or personal Codex credential spends no credits, so exhaustion must not touch it."""
        cache = _AlwaysRefusing()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=False,
            request_headers=b"",
            entitlement_cache=cache,
        )

        self.assertEqual(cache.asked, 0)
        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())

    async def test_connector_traffic_is_never_refused(self) -> None:
        cache = _AlwaysRefusing()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="tavily",
            host="api.tavily.com",
            secrets={"api_key": "tvly-real"},
            platform_shared=True,
            request_headers=b"Authorization: Bearer tvly-HUMR_PLACEHOLDER\r\n",
            entitlement_cache=cache,
        )

        self.assertEqual(cache.asked, 0)
        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())

    async def test_a_broken_billing_check_lets_the_request_through(self) -> None:
        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=True,
            request_headers=b"",
            entitlement_cache=_Exploding(),
        )

        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())


if __name__ == "__main__":
    unittest.main()
