"""Tests for the broker's billing usage tap (tls_usage_metering).

Covers the three layers separately: `UsageTap` as a pure parser over decoded
body bytes, the relay's observer seam (decoded bytes reach the tap, a broken
tap never disturbs the byte relay), and the intercept wiring (metered
providers get Accept-Encoding stripped and a tap attached; unmetered
providers relay byte-identically with no tap).
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

import humr_client  # noqa: E402
import tls_http_message_relay  # noqa: E402
import tls_intercept  # noqa: E402
import tls_provider_catalog  # noqa: E402
import tls_token_store  # noqa: E402
import tls_usage_metering  # noqa: E402


_USAGE = {
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 1000},
    "output_tokens": 350,
    "output_tokens_details": {"reasoning_tokens": 80},
    "total_tokens": 1550,
}


def _completed_payload(model: str) -> dict:
    return {"type": "response.completed", "response": {"id": "resp_1", "model": model, "usage": _USAGE}}


def _sse_stream(model: str) -> bytes:
    """A Codex-style Responses SSE stream ending in response.completed."""
    return (
        b"event: response.created\ndata: {\"type\":\"response.created\"}\n\n"
        b"event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"Hi\"}\n\n"
        b"event: response.completed\ndata: " + json.dumps(_completed_payload(model=model)).encode() + b"\n\n"
    )


class _RecordingReporter:
    """Stands in for UsageReporter.record — collects reported events."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record(self, event: dict) -> None:
        self.events.append(event)


def _fed_tap(reporter: _RecordingReporter) -> tls_usage_metering.UsageTap:
    return tls_usage_metering.UsageTap(provider_slug="openai-codex", record_usage=reporter.record)


def _run_tap(body: bytes, status: int, headers: list[tuple[bytes, bytes]], chunk_size: int) -> list[dict]:
    """Drive one tap through head/body/end and return the recorded events."""
    reporter = _RecordingReporter()
    tap = _fed_tap(reporter=reporter)
    tap.on_head(status, headers)
    for start in range(0, len(body), chunk_size):
        tap.on_body(body[start:start + chunk_size])
    tap.on_end()
    return reporter.events


class TestUsageTapSse(unittest.TestCase):

    def test_completed_event_reports_disjoint_buckets(self) -> None:
        events = _run_tap(
            body=_sse_stream(model="gpt-5.2-codex"),
            status=200,
            headers=[(b"Content-Type", b"text/event-stream")],
            chunk_size=4096,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["source"], "llm")
        self.assertEqual(event["subkey"], "gpt-5.2-codex")
        # input excludes cache reads; reasoning is informational (inside output).
        self.assertEqual(event["quantities"], {
            "input_tokens": 200,
            "output_tokens": 350,
            "cache_read_tokens": 1000,
            "cache_write_tokens": 0,
            "reasoning_tokens": 80,
        })
        self.assertTrue(event["idempotency_key"])
        self.assertIn("+00:00", event["occurred_at"])

    def test_byte_by_byte_delivery_parses_identically(self) -> None:
        events = _run_tap(
            body=_sse_stream(model="gpt-5.2-codex"),
            status=200,
            headers=[],  # Codex omits Content-Type — the sniffer decides
            chunk_size=1,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["quantities"]["input_tokens"], 200)

    def test_nameless_events_are_recognized_by_type_marker(self) -> None:
        body = (
            b"data: {\"type\": \"response.output_text.delta\", \"delta\": \"Hi\"}\n\n"
            b"data: " + json.dumps(_completed_payload(model="gpt-5.2-codex")).encode() + b"\n\n"
        )
        events = _run_tap(body=body, status=200, headers=[], chunk_size=7)

        self.assertEqual(len(events), 1)

    def test_terminal_failed_event_with_usage_is_metered(self) -> None:
        payload = {"type": "response.failed", "response": {"model": "gpt-5.2-codex", "usage": _USAGE}}
        body = b"event: response.failed\ndata: " + json.dumps(payload).encode() + b"\n\n"
        events = _run_tap(body=body, status=200, headers=[], chunk_size=64)

        self.assertEqual(len(events), 1)

    def test_stream_eof_right_after_data_line_still_meters(self) -> None:
        body = b"event: response.completed\ndata: " + json.dumps(_completed_payload(model="m")).encode()
        events = _run_tap(body=body, status=200, headers=[], chunk_size=4096)

        self.assertEqual(len(events), 1)

    def test_stream_without_terminal_event_reports_nothing(self) -> None:
        body = b"event: response.created\ndata: {}\n\ndata: {\"delta\":\"x\"}\n\n"
        self.assertEqual(_run_tap(body=body, status=200, headers=[], chunk_size=16), [])

    def test_non_200_response_reports_nothing(self) -> None:
        self.assertEqual(_run_tap(body=_sse_stream(model="m"), status=429, headers=[], chunk_size=64), [])

    def test_content_coded_response_reports_nothing(self) -> None:
        events = _run_tap(
            body=b"\x1f\x8b garbage",
            status=200,
            headers=[(b"Content-Encoding", b"gzip")],
            chunk_size=64,
        )
        self.assertEqual(events, [])

    def test_oversized_terminal_event_is_unmetered(self) -> None:
        huge = json.dumps({"type": "response.completed", "response": {"pad": "x" * 4096, "usage": _USAGE}})
        body = b"event: response.completed\ndata: " + huge.encode() + b"\n\n"
        with patch.object(tls_usage_metering, "_MAX_CAPTURE_BYTES", 1024):
            self.assertEqual(_run_tap(body=body, status=200, headers=[], chunk_size=128), [])

    def test_overlong_uncaptured_line_is_skipped_without_losing_the_terminal_event(self) -> None:
        body = (
            b"event: response.output_text.delta\ndata: " + b"x" * 4096 + b"\n\n"
            + b"event: response.completed\ndata: " + json.dumps(_completed_payload(model="m")).encode() + b"\n\n"
        )
        with patch.object(tls_usage_metering, "_MAX_CAPTURE_BYTES", 1024):
            events = _run_tap(body=body, status=200, headers=[], chunk_size=100)

        self.assertEqual(len(events), 1)

    def test_garbage_bytes_never_raise(self) -> None:
        self.assertEqual(_run_tap(body=b"\x00\xff\xfe not an sse stream \r\n\r\n{", status=200, headers=[], chunk_size=3), [])


class TestUsageTapJson(unittest.TestCase):

    def test_non_streamed_response_object_is_metered_at_end(self) -> None:
        body = json.dumps({"id": "resp_1", "model": "gpt-5.2-codex", "usage": _USAGE}).encode()
        events = _run_tap(
            body=body,
            status=200,
            headers=[(b"Content-Type", b"application/json")],
            chunk_size=11,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["subkey"], "gpt-5.2-codex")
        self.assertEqual(events[0]["quantities"]["input_tokens"], 200)

    def test_json_without_usage_reports_nothing(self) -> None:
        body = json.dumps({"object": "list", "data": []}).encode()
        self.assertEqual(_run_tap(body=body, status=200, headers=[], chunk_size=64), [])

    def test_unparseable_body_reports_nothing(self) -> None:
        self.assertEqual(_run_tap(body=b"{truncated", status=200, headers=[], chunk_size=64), [])


class _RaisingObserver:
    """Observer whose every method raises — the relay guard must absorb it."""

    def on_head(self, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        raise RuntimeError("observer head boom")

    def on_body(self, data: bytes) -> None:
        raise RuntimeError("observer body boom")

    def on_end(self) -> None:
        raise RuntimeError("observer end boom")


class _RelayRecordingWriter:
    """Minimal StreamWriter stand-in recording all bytes."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

    async def drain(self) -> None:
        return None

    def all_bytes(self) -> bytes:
        return b"".join(self.chunks)


class _StubUpstreamWriter:

    def write(self, data: bytes) -> None:
        return None

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _chunk(payload: bytes) -> bytes:
    return f"{len(payload):x}".encode() + b"\r\n" + payload + b"\r\n"


def _chunked_sse_response(model: str) -> bytes:
    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
    body = b"".join(_chunk(part) for part in _sse_parts(model=model)) + b"0\r\n\r\n"
    return head + body


def _sse_parts(model: str) -> list[bytes]:
    stream = _sse_stream(model=model)
    # Split mid-event so chunk boundaries do not align with SSE boundaries.
    third = len(stream) // 3
    return [stream[:third], stream[third:2 * third], stream[2 * third:]]


async def _forward(upstream_response: bytes, observer: object | None) -> tuple[int, bool, _RelayRecordingWriter]:
    client_writer = _RelayRecordingWriter()
    upstream_reader = asyncio.StreamReader()
    upstream_reader.feed_data(upstream_response)
    upstream_reader.feed_eof()

    async def _fake_open_connection(**kwargs: object) -> tuple[asyncio.StreamReader, _StubUpstreamWriter]:
        return upstream_reader, _StubUpstreamWriter()

    with patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection):
        status, keep_alive = await tls_http_message_relay.forward_to_upstream(
            host="chatgpt.com",
            port=443,
            method="POST",
            path_with_query="/backend-api/codex/responses",
            headers=[(b"host", b"chatgpt.com")],
            body=b"{}",
            client_writer=client_writer,
            response_body_observer=observer,
        )
    return status, keep_alive, client_writer


class TestRelayObserverSeam(unittest.IsolatedAsyncioTestCase):

    async def test_tap_receives_dechunked_bytes_and_meters(self) -> None:
        reporter = _RecordingReporter()
        tap = tls_usage_metering.UsageTap(provider_slug="openai-codex", record_usage=reporter.record)

        status, keep_alive, writer = await _forward(
            upstream_response=_chunked_sse_response(model="gpt-5.2-codex"), observer=tap,
        )

        self.assertEqual(status, 200)
        self.assertTrue(keep_alive)
        self.assertEqual(len(reporter.events), 1)
        self.assertEqual(reporter.events[0]["subkey"], "gpt-5.2-codex")
        # The client still sees the chunked framing verbatim.
        self.assertIn(b"Transfer-Encoding: chunked", writer.all_bytes())
        self.assertIn(b"event: response.completed", writer.all_bytes())

    async def test_raising_observer_never_disturbs_the_relay(self) -> None:
        with_observer = await _forward(
            upstream_response=_chunked_sse_response(model="m"), observer=_RaisingObserver(),
        )
        without_observer = await _forward(
            upstream_response=_chunked_sse_response(model="m"), observer=None,
        )

        self.assertEqual(with_observer[0], without_observer[0])
        self.assertEqual(with_observer[1], without_observer[1])
        self.assertEqual(with_observer[2].all_bytes(), without_observer[2].all_bytes())


class TestUsageReporter(unittest.IsolatedAsyncioTestCase):

    def _make_reporter(self) -> tuple[tls_usage_metering.UsageReporter, AsyncMock]:
        client = humr_client.HumrClient(
            control_plane_url="https://humr.example",
            bearer="env-bearer",
            owner_username="vmendi",
            app_slug="hermes",
        )
        post_json = AsyncMock(return_value=(200, {"ok": True}))
        client.post_json = post_json
        return tls_usage_metering.UsageReporter(humr_client=client), post_json

    async def test_flush_posts_buffered_events_in_one_batch(self) -> None:
        reporter, post_json = self._make_reporter()
        reporter.record({"idempotency_key": "a"})
        reporter.record({"idempotency_key": "b"})

        await reporter.flush()

        post_json.assert_awaited_once()
        kwargs = post_json.await_args.kwargs
        self.assertEqual(kwargs["path"], tls_usage_metering.REPORT_PATH)
        self.assertEqual([e["idempotency_key"] for e in kwargs["payload"]["events"]], ["a", "b"])

        post_json.reset_mock()
        await reporter.flush()
        post_json.assert_not_awaited()  # buffer was drained

    async def test_failed_post_drops_the_batch(self) -> None:
        reporter, post_json = self._make_reporter()
        post_json.return_value = (502, {"error": "control plane request failed"})
        reporter.record({"idempotency_key": "a"})

        await reporter.flush()
        post_json.reset_mock()
        await reporter.flush()

        post_json.assert_not_awaited()  # dropped, not retried

    async def test_full_buffer_drops_new_events(self) -> None:
        reporter, _post_json = self._make_reporter()
        with patch.object(tls_usage_metering, "_MAX_BUFFERED_EVENTS", 3):
            for index in range(5):
                reporter.record({"idempotency_key": str(index)})

        self.assertEqual(len(reporter._events), 3)


class _StubTransport:

    def __init__(self) -> None:
        self.protocol = object()

    def get_protocol(self) -> object:
        return self.protocol


class _TlsRecordingWriter(_RelayRecordingWriter):
    """Adds the transport/close API the TLS interception path uses."""

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


class _RecordingUpstreamWriter(_StubUpstreamWriter):

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.chunks.append(bytes(data))

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


class TestTapForRequest(unittest.TestCase):
    """tap_for_request is the whole metering decision: HUMR-funded + parseable dialect."""

    def test_platform_shared_codex_gets_a_tap(self) -> None:
        tap = tls_usage_metering.tap_for_request(
            provider_slug="openai-codex", platform_shared=True, record_usage=_RecordingReporter().record,
        )
        self.assertIsInstance(tap, tls_usage_metering.UsageTap)

    def test_customer_funded_codex_is_not_metered(self) -> None:
        tap = tls_usage_metering.tap_for_request(
            provider_slug="openai-codex", platform_shared=False, record_usage=_RecordingReporter().record,
        )
        self.assertIsNone(tap)

    def test_platform_shared_unparsed_provider_is_not_metered(self) -> None:
        tap = tls_usage_metering.tap_for_request(
            provider_slug="tavily", platform_shared=True, record_usage=_RecordingReporter().record,
        )
        self.assertIsNone(tap)


class TestMeteredInterceptWiring(unittest.IsolatedAsyncioTestCase):
    """_intercept_and_forward attaches the tap and strips Accept-Encoding for metered requests only."""

    async def _intercept(
        self,
        provider_slug: str,
        host: str,
        secrets: dict[str, str],
        platform_shared: bool,
        request_headers: bytes,
        upstream_response: bytes,
        usage_reporter: object | None,
    ) -> tuple[_TlsRecordingWriter, _RecordingUpstreamWriter]:
        provider = tls_provider_catalog.TLS_INTERCEPT_PROVIDERS[provider_slug]
        client_reader = asyncio.StreamReader()
        client_reader.feed_data(
            b"POST /backend-api/codex/responses HTTP/1.1\r\n"
            b"Host: " + host.encode() + b"\r\n"
            + request_headers +
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"{}"
        )
        client_reader.feed_eof()
        client_writer = _TlsRecordingWriter()
        upstream_reader = asyncio.StreamReader()
        upstream_reader.feed_data(upstream_response)
        upstream_reader.feed_eof()
        upstream_writer = _RecordingUpstreamWriter()

        async def _fake_open_connection(**kwargs: object) -> tuple[asyncio.StreamReader, _RecordingUpstreamWriter]:
            return upstream_reader, upstream_writer

        loop = asyncio.get_running_loop()
        with (
            patch.object(loop, "start_tls", AsyncMock(return_value=client_writer.transport)),
            patch.object(tls_intercept.asyncio, "StreamWriter", return_value=client_writer),
            patch.object(tls_http_message_relay.asyncio, "open_connection", _fake_open_connection),
        ):
            await asyncio.wait_for(
                tls_intercept._intercept_and_forward(
                    client_reader=client_reader,
                    client_writer=client_writer,
                    host=host,
                    port=443,
                    provider=provider,
                    minter=_StubCertMinter(),
                    credential_state_store=_StubCredentialStateStore(secrets=secrets, platform_shared=platform_shared),
                    usage_reporter=usage_reporter,
                ),
                timeout=1.0,
            )
        return client_writer, upstream_writer

    async def test_platform_funded_codex_is_metered_and_accept_encoding_stripped(self) -> None:
        reporter = _RecordingReporter()

        client_writer, upstream_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=True,
            request_headers=b"Accept-Encoding: gzip, br\r\n",
            upstream_response=_chunked_sse_response(model="gpt-5.2-codex"),
            usage_reporter=reporter,
        )

        self.assertNotIn(b"accept-encoding", upstream_writer.all_bytes().lower())
        self.assertEqual(len(reporter.events), 1)
        self.assertEqual(reporter.events[0]["subkey"], "gpt-5.2-codex")
        self.assertIn(b"event: response.completed", client_writer.all_bytes())

    async def test_customer_funded_codex_is_not_metered(self) -> None:
        """An org-shared or personal Codex credential consumes no credits — no tap, no header strip."""
        reporter = _RecordingReporter()

        _client_writer, upstream_writer = await self._intercept(
            provider_slug="openai-codex",
            host="chatgpt.com",
            secrets={"access_token": "tok", "chatgpt_account_id": "acct"},
            platform_shared=False,
            request_headers=b"Accept-Encoding: gzip, br\r\n",
            upstream_response=_chunked_sse_response(model="gpt-5.2-codex"),
            usage_reporter=reporter,
        )

        self.assertIn(b"accept-encoding", upstream_writer.all_bytes().lower())
        self.assertEqual(reporter.events, [])

    async def test_platform_funded_unparsed_provider_keeps_accept_encoding_and_reports_nothing(self) -> None:
        reporter = _RecordingReporter()
        response_body = json.dumps({"model": "gpt-5.2", "usage": _USAGE}).encode()
        upstream_response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
            b"\r\n" + response_body
        )

        _client_writer, upstream_writer = await self._intercept(
            provider_slug="openrouter",
            host="openrouter.ai",
            secrets={"api_key": "sk-or-real"},
            platform_shared=True,
            request_headers=b"Accept-Encoding: gzip\r\nAuthorization: Bearer HUMR_PLACEHOLDER\r\n",
            upstream_response=upstream_response,
            usage_reporter=reporter,
        )

        self.assertIn(b"accept-encoding", upstream_writer.all_bytes().lower())
        self.assertEqual(reporter.events, [])


if __name__ == "__main__":
    unittest.main()
