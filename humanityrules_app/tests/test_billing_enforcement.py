"""Tests for the broker's billing facade, entitlement state, and 402 path."""

import asyncio
import json
import pathlib
import ssl
import sys
import unittest
from collections.abc import Callable
from unittest.mock import AsyncMock, patch

_INTEGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "template_repos" / "hermes_agent" / "humr_runtime" / "integrations"
if str(_INTEGRATIONS_DIR) not in sys.path:
    sys.path.insert(0, str(_INTEGRATIONS_DIR))

import billing_entitlement  # noqa: E402
import billing_service  # noqa: E402
import billing_usage_metering  # noqa: E402
import humr_client  # noqa: E402
import tls_http_message_relay  # noqa: E402
import tls_intercept  # noqa: E402
import tls_provider_catalog  # noqa: E402
import tls_credential_state  # noqa: E402


def _snapshot(credits_remaining: int, exhausted: bool, renewal_date: str | None) -> dict:
    return {
        "credits_remaining": credits_remaining,
        "monthly_grant": 2000,
        "renewal_date": renewal_date,
        "plan": "operator",
        "exhausted": exhausted,
    }


def _make_entitlement(get_json: AsyncMock) -> billing_entitlement.BillingEntitlement:
    """Build entitlement state over a HumrClient whose GET is test-controlled."""
    client = humr_client.HumrClient(
        control_plane_url="https://humr.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )
    client.get_json = get_json
    return billing_entitlement.BillingEntitlement(humr_client=client)


class TestBillingEntitlement(unittest.IsolatedAsyncioTestCase):

    async def test_no_snapshot_fails_open_without_calling_the_control_plane(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        entitlement = _make_entitlement(get_json=get_json)

        self.assertIsNone(await entitlement.refusal_for_metered_request())
        get_json.assert_not_awaited()

    async def test_report_response_replaces_the_entitlement_snapshot(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        entitlement = _make_entitlement(get_json=get_json)

        entitlement.absorb_report_response(body={"ok": True, "entitlement": _snapshot(
            credits_remaining=1500, exhausted=False, renewal_date="2026-08-15",
        )})

        self.assertIsNone(await entitlement.refusal_for_metered_request())
        get_json.assert_not_awaited()
        self.assertEqual(await entitlement.entitlement_snapshot_for_display(), {
            "credits_remaining": 1500,
            "monthly_grant": 2000,
            "renewal_date": "2026-08-15",
            "plan": "operator",
            "exhausted": False,
        })

    async def test_unusable_report_response_keeps_the_previous_snapshot(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=900, exhausted=False, renewal_date=None,
        )})

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
                entitlement.absorb_report_response(body=body)

        self.assertEqual((await entitlement.entitlement_snapshot_for_display())["credits_remaining"], 900)

    async def test_exhaustion_rechecks_the_control_plane_and_refuses(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        refusal = await entitlement.refusal_for_metered_request()

        get_json.assert_awaited_once()
        self.assertEqual(get_json.await_args.kwargs["path"], billing_entitlement.ENTITLEMENT_PATH)
        self.assertEqual(refusal["error"]["code"], "credits_exhausted")

    async def test_upgrade_unblocks_on_the_very_next_request(self) -> None:
        """Exhaustion never trusts the cache — the recheck is what makes upgrades instant."""
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=2000, exhausted=False, renewal_date="2026-08-15",
        )}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        self.assertIsNone(await entitlement.refusal_for_metered_request())
        get_json.assert_awaited_once()
        self.assertIsNone(await entitlement.refusal_for_metered_request())
        get_json.assert_awaited_once()

    async def test_unreachable_control_plane_keeps_refusing_on_last_known_state(self) -> None:
        for failure in ((502, {"error": "control plane request failed"}), (200, {"ok": True})):
            with self.subTest(failure=failure):
                get_json = AsyncMock(return_value=failure)
                entitlement = _make_entitlement(get_json=get_json)
                entitlement.absorb_report_response(body={"entitlement": _snapshot(
                    credits_remaining=-200, exhausted=True, renewal_date=None,
                )})

                refusal = await entitlement.refusal_for_metered_request()

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
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date=None,
        )})

        pending = [asyncio.create_task(entitlement.refusal_for_metered_request()) for _ in range(5)]
        await started.wait()
        release.set()
        refusals = await asyncio.gather(*pending)

        self.assertEqual(get_json.await_count, 1)
        self.assertTrue(all(refusal is not None for refusal in refusals))

    async def test_report_response_wins_over_an_older_refresh_still_in_flight(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_get(path: str, timeout_seconds: int) -> tuple[int, dict]:
            started.set()
            await release.wait()
            return 200, {"entitlement": _snapshot(credits_remaining=1200, exhausted=False, renewal_date=None)}

        entitlement = _make_entitlement(get_json=AsyncMock(side_effect=_slow_get))
        pending_display = asyncio.create_task(entitlement.entitlement_snapshot_for_display())
        await started.wait()
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200,
            exhausted=True,
            renewal_date=None,
        )})
        release.set()

        entitlement_snapshot = await pending_display

        self.assertEqual(entitlement_snapshot["credits_remaining"], -200)
        self.assertTrue(entitlement_snapshot["exhausted"])

    async def test_display_fetches_for_an_organization_that_has_never_spent(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=500, exhausted=False, renewal_date=None,
        )}))
        entitlement = _make_entitlement(get_json=get_json)

        entitlement_snapshot = await entitlement.entitlement_snapshot_for_display()

        get_json.assert_awaited_once()
        self.assertEqual(entitlement_snapshot["credits_remaining"], 500)

    async def test_display_serves_a_fresh_cache_without_calling_the_control_plane(self) -> None:
        get_json = AsyncMock(return_value=(200, {}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        self.assertEqual((await entitlement.entitlement_snapshot_for_display())["credits_remaining"], 1200)
        get_json.assert_not_awaited()

    async def test_display_refreshes_a_stale_cache(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=300, exhausted=False, renewal_date=None,
        )}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        with patch.object(billing_entitlement, "_DISPLAY_STALE_SECONDS", 0):
            entitlement_snapshot = await entitlement.entitlement_snapshot_for_display()

        get_json.assert_awaited_once()
        self.assertEqual(entitlement_snapshot["credits_remaining"], 300)

    async def test_display_serves_the_stale_copy_when_the_refresh_fails(self) -> None:
        get_json = AsyncMock(return_value=(502, {"error": "control plane request failed"}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=1200, exhausted=False, renewal_date=None,
        )})

        with patch.object(billing_entitlement, "_DISPLAY_STALE_SECONDS", 0):
            entitlement_snapshot = await entitlement.entitlement_snapshot_for_display()

        self.assertEqual(entitlement_snapshot["credits_remaining"], 1200)

    async def test_display_is_none_when_humr_has_never_answered(self) -> None:
        entitlement = _make_entitlement(get_json=AsyncMock(return_value=(502, {"error": "control plane request failed"})))

        self.assertIsNone(await entitlement.entitlement_snapshot_for_display())

    async def test_upgrade_url_points_at_the_control_plane_billing_page(self) -> None:
        entitlement = _make_entitlement(get_json=AsyncMock(return_value=(200, {})))

        self.assertEqual(entitlement.upgrade_url(), "https://humr.example/settings/billing/")

    async def test_trial_refusal_body_offers_upgrade_only(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=-50, exhausted=True, renewal_date=None,
        )}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-50, exhausted=True, renewal_date=None,
        )})

        body = await entitlement.refusal_for_metered_request()

        self.assertEqual(body["error"]["type"], "insufficient_credits")
        self.assertEqual(body["error"]["code"], "credits_exhausted")
        self.assertEqual(body["upgrade_url"], "https://humr.example/settings/billing/")
        self.assertEqual(body["entitlement"]["credits_remaining"], -50)
        self.assertIn("out of credits", body["error"]["message"])
        self.assertIn("https://humr.example/settings/billing/", body["error"]["message"])
        self.assertNotIn("renew", body["error"]["message"])

    async def test_renewing_plan_refusal_body_also_offers_waiting(self) -> None:
        get_json = AsyncMock(return_value=(200, {"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date="2026-08-15",
        )}))
        entitlement = _make_entitlement(get_json=get_json)
        entitlement.absorb_report_response(body={"entitlement": _snapshot(
            credits_remaining=-200, exhausted=True, renewal_date="2026-08-15",
        )})

        body = await entitlement.refusal_for_metered_request()

        self.assertIn("renew on 2026-08-15", body["error"]["message"])


class _FacadeEntitlement:

    def __init__(self, refusal: dict | None) -> None:
        self.refusal = refusal
        self.refusal_requests = 0
        self.absorbed: list[object] = []
        self.entitlement_snapshot = _snapshot(credits_remaining=700, exhausted=False, renewal_date=None)

    def absorb_report_response(self, body: object) -> None:
        self.absorbed.append(body)

    async def refusal_for_metered_request(self) -> dict | None:
        self.refusal_requests += 1
        return self.refusal

    async def entitlement_snapshot_for_display(self) -> dict:
        return self.entitlement_snapshot

    def upgrade_url(self) -> str:
        return "https://humr.example/settings/billing/"


class _FacadeReporter:

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.on_report_response: Callable[[dict], None] | None = None
        self.run_calls = 0

    def record(self, event: dict) -> None:
        self.events.append(event)

    async def run(self) -> None:
        self.run_calls += 1


def _make_facade(refusal: dict | None) -> tuple[billing_service.BillingService, _FacadeEntitlement, _FacadeReporter]:
    client = humr_client.HumrClient(
        control_plane_url="https://humr.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )
    entitlement = _FacadeEntitlement(refusal=refusal)
    reporter = _FacadeReporter()

    def build_reporter(
        humr_client: humr_client.HumrClient,
        on_report_response: Callable[[dict], None],
    ) -> _FacadeReporter:
        reporter.on_report_response = on_report_response
        return reporter

    with (
        patch.object(billing_service.billing_entitlement, "BillingEntitlement", return_value=entitlement),
        patch.object(billing_service.billing_usage_metering, "UsageReporter", side_effect=build_reporter),
    ):
        facade = billing_service.BillingService(humr_client=client)
    return facade, entitlement, reporter


class TestBillingService(unittest.IsolatedAsyncioTestCase):

    async def test_unmetered_request_forwards_untouched(self) -> None:
        facade, entitlement, _reporter = _make_facade(refusal={"would": "refuse"})

        for provider_slug, platform_shared in (("openai-codex", False), ("tavily", True)):
            with self.subTest(provider_slug=provider_slug, platform_shared=platform_shared):
                decision = await facade.decision_for_request(
                    provider_slug=provider_slug,
                    platform_shared=platform_shared,
                )
                self.assertEqual(decision, billing_service.BillingDecision(refusal=None, usage_tap=None))

        self.assertEqual(entitlement.refusal_requests, 0)

    async def test_exhausted_metered_request_returns_refusal_without_a_tap(self) -> None:
        refusal = {"error": {"code": "credits_exhausted"}}
        facade, entitlement, _reporter = _make_facade(refusal=refusal)

        decision = await facade.decision_for_request(provider_slug="openai-codex", platform_shared=True)

        self.assertIs(decision.refusal, refusal)
        self.assertIsNone(decision.usage_tap)
        self.assertEqual(entitlement.refusal_requests, 1)

    async def test_allowed_metered_request_returns_a_tap_wired_to_the_reporter(self) -> None:
        facade, entitlement, reporter = _make_facade(refusal=None)

        decision = await facade.decision_for_request(provider_slug="openai-codex", platform_shared=True)

        self.assertIsNone(decision.refusal)
        self.assertIsInstance(decision.usage_tap, billing_usage_metering.UsageTap)
        decision.usage_tap.on_head(status=200, headers=[(b"content-type", b"application/json")])
        decision.usage_tap.on_body(json.dumps({
            "model": "gpt-5.2-codex",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }).encode())
        decision.usage_tap.on_end()
        self.assertEqual(len(reporter.events), 1)
        self.assertEqual(reporter.events[0]["subkey"], "gpt-5.2-codex")
        self.assertEqual(entitlement.refusal_requests, 1)
        with self.assertRaises(ValueError):
            billing_service.BillingDecision(refusal={"error": {}}, usage_tap=decision.usage_tap)

    async def test_report_callback_and_public_delegates_stay_inside_the_facade(self) -> None:
        facade, entitlement, reporter = _make_facade(refusal=None)
        payload = {"entitlement": _snapshot(credits_remaining=700, exhausted=False, renewal_date=None)}

        reporter.on_report_response(payload)
        await facade.run_usage_flush_loop()

        self.assertEqual(entitlement.absorbed, [payload])
        self.assertEqual(await facade.entitlement_snapshot_for_display(), entitlement.entitlement_snapshot)
        self.assertEqual(facade.upgrade_url(), "https://humr.example/settings/billing/")
        self.assertEqual(reporter.run_calls, 1)


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

    async def credential_for_slug(self, slug: str) -> tls_credential_state.ActiveCredential:
        return tls_credential_state.ActiveCredential(secrets=self._secrets, platform_shared=self._platform_shared)

    async def drop_cached_token(self, slug: str) -> None:
        return None


async def _exhausted_billing_service() -> tuple[billing_service.BillingService, AsyncMock]:
    client = humr_client.HumrClient(
        control_plane_url="https://humr.example",
        bearer="env-bearer",
        owner_username="vmendi",
        app_slug="hermes",
    )
    entitlement_payload = {"entitlement": _snapshot(
        credits_remaining=-200,
        exhausted=True,
        renewal_date=None,
    )}
    get_json = AsyncMock(return_value=(200, entitlement_payload))
    client.get_json = get_json
    facade = billing_service.BillingService(humr_client=client)
    await facade.entitlement_snapshot_for_display()
    get_json.reset_mock()
    return facade, get_json


class TestExhaustedInterceptWiring(unittest.IsolatedAsyncioTestCase):
    """Exhaustion at the proxy: what gets a 402, what sails through untouched."""

    async def _intercept(
        self,
        provider_slug: str,
        host: str,
        secrets: dict[str, str],
        platform_shared: bool,
        request_headers: bytes,
        billing_service_instance: object | None,
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
                    billing_service=billing_service_instance,
                ),
                timeout=1.0,
            )
        return sandbox_writer, provider_writer

    async def test_metered_request_is_refused_with_a_structured_402(self) -> None:
        facade, get_json = await _exhausted_billing_service()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=True,
            request_headers=b"",
            billing_service_instance=facade,
        )

        # The CONNECT acknowledgement precedes the refusal on the same writer.
        _connect_ack, _, refusal_response = sandbox_writer.all_bytes().partition(b"\r\n\r\n")
        self.assertTrue(refusal_response.startswith(b"HTTP/1.1 402 Payment Required\r\n"))
        payload = json.loads(refusal_response.split(b"\r\n\r\n", 1)[1])
        self.assertEqual(payload["error"]["code"], "credits_exhausted")
        self.assertEqual(payload["entitlement"]["exhausted"], True)
        self.assertEqual(payload["upgrade_url"], "https://humr.example/settings/billing/")
        self.assertEqual(provider_writer.all_bytes(), b"")
        get_json.assert_awaited_once()

    async def test_customer_funded_model_call_is_never_refused(self) -> None:
        """An org or personal Codex credential spends no credits, so exhaustion must not touch it."""
        facade, get_json = await _exhausted_billing_service()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=False,
            request_headers=b"",
            billing_service_instance=facade,
        )

        get_json.assert_not_awaited()
        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())

    async def test_connector_traffic_is_never_refused(self) -> None:
        facade, get_json = await _exhausted_billing_service()

        sandbox_writer, provider_writer = await self._intercept(
            provider_slug="tavily",
            host="api.tavily.com",
            secrets={"api_key": "tvly-real"},
            platform_shared=True,
            request_headers=b"Authorization: Bearer tvly-HUMR_PLACEHOLDER\r\n",
            billing_service_instance=facade,
        )

        get_json.assert_not_awaited()
        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())

    async def test_a_broken_billing_check_lets_the_request_through(self) -> None:
        facade, entitlement, _reporter = _make_facade(refusal=None)

        with patch.object(entitlement, "refusal_for_metered_request", AsyncMock(side_effect=RuntimeError("billing boom"))):
            sandbox_writer, provider_writer = await self._intercept(
                provider_slug="openai-codex",
                host="chatgpt.com",
                secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
                platform_shared=True,
                request_headers=b"Accept-Encoding: gzip, br\r\n",
                billing_service_instance=facade,
            )

        self.assertNotIn(b"402", sandbox_writer.all_bytes())
        self.assertIn(b"POST /backend-api/codex/responses", provider_writer.all_bytes())
        self.assertIn(b"accept-encoding", provider_writer.all_bytes().lower())


if __name__ == "__main__":
    unittest.main()
