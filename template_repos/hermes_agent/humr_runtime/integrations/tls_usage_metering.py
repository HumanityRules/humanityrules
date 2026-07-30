"""Billing usage metering at the TLS-intercept proxy.

This module owns the metering decision: a request is metered iff HUMR's own
platform-tier credential was injected into it (`platform_shared` provenance
from the token refresh — org-shared and personal credentials are
customer-funded and consume no credits) AND the provider speaks a dialect
this module can parse. `tap_for_request` is that decision; the intercept
just attaches whatever it returns. Today the only parsed dialect is the
OpenAI Responses API (Codex): other model providers are expected to move
behind a HUMR-owned gateway before they are ever billed, so the
intercept-side parser never grows beyond it.

The pieces, all observe-only:

- `UsageTap` — a per-response `ResponseBodyObserver` (see
  `tls_http_message_relay`) that pulls token usage out of the Responses
  terminal event (`response.completed` / `response.failed` /
  `response.incomplete`), or out of a plain JSON body for non-streamed calls.
- `UsageReporter` — a process-wide buffer + flush loop that posts usage
  events to HUMR through `HumrClient` (which stamps owner/app attribution
  into every payload). No spool: if HUMR is unreachable the batch is dropped
  with a log line — the failure mode is undercharging, never a broken agent.

Events are source-generic: the billable units ride in a per-source
`quantities` dict and `subkey` carries the source's sub-dimension (for
`llm`: the observed model id). The llm token buckets are disjoint:
`input_tokens` excludes cache reads (`cache_read_tokens` carries those),
and `reasoning_tokens` is informational — OpenAI already folds reasoning
into `output_tokens`, so rating must not add it again.

Memory is bounded per in-flight response: only terminal-event payloads are
accumulated (capped), non-captured over-long lines are discarded, and a
response that exceeds a cap is logged as unmetered — never buffered further.
"""

import asyncio
import datetime
import json
import logging
import re
import uuid
from collections.abc import Callable

from humr_client import HumrClient


logger = logging.getLogger("tls_usage_metering")


REPORT_PATH = "/api/runtime/billing-usage-events"

_FLUSH_INTERVAL_SECONDS = 10
_FLUSH_BATCH_SIZE = 20
# Must stay at or below the CP ingest cap (MAX_EVENTS_PER_REPORT in views/billing_usage.py):
# flush posts the whole buffer in one request, and an oversized batch is rejected outright.
_MAX_BUFFERED_EVENTS = 500
_POST_TIMEOUT_SECONDS = 15

# A terminal event's data line carries the full response object (all output
# items), so the capture cap must comfortably exceed the largest plausible
# completion; past it the response is unmetered, never buffered further.
_MAX_CAPTURE_BYTES = 2 * 1024 * 1024
# How deep into a nameless block's first data line to look for the terminal
# type marker; OpenAI serializes "type" first, so this is generous.
_MARKER_SNIFF_BYTES = 256

_TERMINAL_EVENT_NAMES = frozenset({b"response.completed", b"response.failed", b"response.incomplete"})
_TERMINAL_TYPE_MARKER_RE = re.compile(rb'"type"\s*:\s*"response\.(completed|failed|incomplete)"')

# The provider slugs whose response dialect UsageTap can extract usage from.
_PARSED_PROVIDER_SLUGS = frozenset({"openai-codex"})


def tap_for_request(provider_slug: str, platform_shared: bool, record_usage: Callable[[dict], None]) -> "UsageTap | None":
    """Return a tap when this request's response is metered, else None.

    The metering decision in one place: HUMR funded the injected credential
    (platform_shared — customer-funded credentials consume no credits) and
    the provider's dialect is one the tap can parse. Callers strip the
    request's Accept-Encoding exactly when a tap is returned, so the
    response stays readable iff someone is reading it.
    """
    if not platform_shared:
        return None
    if provider_slug not in _PARSED_PROVIDER_SLUGS:
        return None
    return UsageTap(provider_slug=provider_slug, record_usage=record_usage)


def _is_count(value: object) -> bool:
    """True for a non-negative int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _counts_from_usage(usage: object) -> dict[str, int] | None:
    """Map a Responses-API usage object to disjoint token buckets, or None."""
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not _is_count(input_tokens) or not _is_count(output_tokens):
        return None
    input_details = usage.get("input_tokens_details")
    cached_tokens = input_details.get("cached_tokens") if isinstance(input_details, dict) else 0
    if not _is_count(cached_tokens):
        cached_tokens = 0
    output_details = usage.get("output_tokens_details")
    reasoning_tokens = output_details.get("reasoning_tokens") if isinstance(output_details, dict) else 0
    if not _is_count(reasoning_tokens):
        reasoning_tokens = 0
    return {
        "input_tokens": max(input_tokens - cached_tokens, 0),
        "output_tokens": output_tokens,
        "cache_read_tokens": cached_tokens,
        "cache_write_tokens": 0,
        "reasoning_tokens": reasoning_tokens,
    }


class UsageTap:
    """Extract token usage from one metered model-provider response.

    Implements the relay's `ResponseBodyObserver` protocol. Fed decoded body
    bytes; framing is already removed by the relay. Detects SSE vs plain JSON
    itself — the Codex backend omits Content-Type on its SSE responses, so
    the first body bytes, not the head, are what decide.
    """

    def __init__(self, provider_slug: str, record_usage: Callable[[dict], None]) -> None:
        self._provider_slug = provider_slug
        self._record_usage = record_usage
        self._inert = False
        self._mode: str | None = None  # "sse" | "json", decided on first body bytes
        self._prelude = bytearray()  # bytes held until the mode is decided
        self._content_type = b""
        # SSE state: partial line, current event block, captured terminal payload.
        self._line_buf = bytearray()
        self._skipping_line = False
        self._event_name: bytes | None = None
        self._block_wanted: bool | None = None  # None = undecided (nameless block)
        self._captured_data: list[bytes] = []
        self._captured_bytes = 0
        # JSON state.
        self._json_buf = bytearray()

    def on_head(self, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        if status != 200:
            self._inert = True  # error responses carry no usage
            return
        for name, value in headers:
            lowered_name = name.lower()
            if lowered_name == b"content-type":
                self._content_type = value.lower()
            elif lowered_name == b"content-encoding" and value.strip().lower() not in (b"", b"identity"):
                # The intercept strips Accept-Encoding for metered providers,
                # so a coded response here is an anomaly worth a log line.
                logger.error("%s: unmetered response (content-encoding %r)", self._provider_slug, value)
                self._inert = True
                return

    def on_body(self, data: bytes) -> None:
        if self._inert:
            return
        if self._mode is None:
            self._prelude.extend(data)
            if b"text/event-stream" in self._content_type:
                self._mode = "sse"
            elif len(self._prelude) < 6 and b"\n" not in self._prelude:
                return  # not enough bytes to sniff yet
            else:
                first_byte = bytes(self._prelude).lstrip()[:1]
                self._mode = "json" if first_byte in (b"{", b"[") else "sse"
            data = bytes(self._prelude)
            self._prelude.clear()
        if self._mode == "json":
            self._json_buf.extend(data)
            if len(self._json_buf) > _MAX_CAPTURE_BYTES:
                logger.error("%s: unmetered response (JSON body exceeded %d bytes)", self._provider_slug, _MAX_CAPTURE_BYTES)
                self._inert = True
            return
        self._feed_sse(data=data)

    def on_end(self) -> None:
        if self._inert:
            return
        if self._mode == "json":
            self._inert = True
            self._finish_json_body()
            return
        if self._mode == "sse":
            # A stream may EOF right after the terminal event's data line,
            # without the blank line or trailing newline that would normally
            # close the block — treat EOF as both.
            if self._line_buf and not self._skipping_line:
                line = bytes(self._line_buf)
                self._line_buf.clear()
                self._handle_sse_line(line=line)
            if not self._inert and self._captured_data:
                self._finish_sse_block()
        self._inert = True

    def _feed_sse(self, data: bytes) -> None:
        start = 0
        while not self._inert:
            newline_index = data.find(b"\n", start)
            if newline_index == -1:
                tail = data[start:]
                if self._skipping_line or not tail:
                    return
                self._line_buf.extend(tail)
                if len(self._line_buf) > _MAX_CAPTURE_BYTES:
                    if self._partial_line_is_wanted():
                        logger.error("%s: unmetered response (terminal event exceeded %d bytes)", self._provider_slug, _MAX_CAPTURE_BYTES)
                        self._inert = True
                    else:
                        self._line_buf.clear()
                        self._skipping_line = True
                return
            segment = data[start:newline_index]
            start = newline_index + 1
            if self._skipping_line:
                self._skipping_line = False
                continue
            if self._line_buf:
                line = bytes(self._line_buf) + segment
                self._line_buf.clear()
            else:
                line = segment
            self._handle_sse_line(line=line)

    def _partial_line_is_wanted(self) -> bool:
        """Whether an over-long partial line could still be a terminal event's data."""
        if self._block_wanted is not None:
            return self._block_wanted
        if not self._line_buf.startswith(b"data:"):
            return False
        return _TERMINAL_TYPE_MARKER_RE.search(bytes(self._line_buf[:_MARKER_SNIFF_BYTES])) is not None

    def _handle_sse_line(self, line: bytes) -> None:
        if line.endswith(b"\r"):
            line = line[:-1]
        if line == b"":
            if self._captured_data:
                self._finish_sse_block()
            self._event_name = None
            self._block_wanted = None
            return
        if line.startswith(b"event:"):
            self._event_name = line[len(b"event:"):].strip()
            self._block_wanted = self._event_name in _TERMINAL_EVENT_NAMES
            return
        if not line.startswith(b"data:"):
            return  # comments, id:, retry:
        value = line[len(b"data:"):]
        if value.startswith(b" "):
            value = value[1:]
        if self._block_wanted is None:
            # Nameless block: the first data line's own type field decides.
            self._block_wanted = _TERMINAL_TYPE_MARKER_RE.search(value[:_MARKER_SNIFF_BYTES]) is not None
        if not self._block_wanted:
            return
        self._captured_bytes += len(value)
        if self._captured_bytes > _MAX_CAPTURE_BYTES:
            logger.error("%s: unmetered response (terminal event exceeded %d bytes)", self._provider_slug, _MAX_CAPTURE_BYTES)
            self._inert = True
            return
        self._captured_data.append(value)

    def _finish_sse_block(self) -> None:
        payload = b"\n".join(self._captured_data)
        self._captured_data = []
        self._captured_bytes = 0
        try:
            parsed = json.loads(payload)
        except ValueError:
            logger.error("%s: unmetered response (terminal event was not valid JSON)", self._provider_slug)
            self._inert = True
            return
        response = parsed.get("response") if isinstance(parsed, dict) else None
        if not isinstance(response, dict):
            return
        counts = _counts_from_usage(usage=response.get("usage"))
        if counts is None:
            logger.info("%s: terminal event without usable usage; not metered", self._provider_slug)
            return
        self._report(model=response.get("model"), counts=counts)
        self._inert = True

    def _finish_json_body(self) -> None:
        """Meter a non-streamed call: the body is the response object itself."""
        if not self._json_buf:
            return
        try:
            parsed = json.loads(bytes(self._json_buf))
        except ValueError:
            return  # not every 200 from the provider host is a model response
        if not isinstance(parsed, dict):
            return
        counts = _counts_from_usage(usage=parsed.get("usage"))
        if counts is None:
            return
        self._report(model=parsed.get("model"), counts=counts)

    def _report(self, model: object, counts: dict[str, int]) -> None:
        event = {
            "idempotency_key": uuid.uuid4().hex,
            "occurred_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "source": "llm",
            "subkey": model[:255] if isinstance(model, str) else "",
            "quantities": counts,
        }
        self._record_usage(event)


class UsageReporter:
    """Buffers usage events and posts them to HUMR in batches.

    Fire-and-forget by design: a full buffer or a failed post drops events
    with a log line. Idempotency keys are minted at event creation, so a
    durable spool can replace the drop later without double-charge risk.
    """

    def __init__(self, humr_client: HumrClient) -> None:
        self._humr_client = humr_client
        self._events: list[dict] = []
        self._wake = asyncio.Event()

    def record(self, event: dict) -> None:
        """Queue one usage event for the next flush."""
        if len(self._events) >= _MAX_BUFFERED_EVENTS:
            logger.error("usage buffer full; dropped event %s", event.get("idempotency_key"))
            return
        self._events.append(event)
        if len(self._events) >= _FLUSH_BATCH_SIZE:
            self._wake.set()

    async def run(self) -> None:
        """Flush on a timer, or immediately once a batch fills. Never returns."""
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_FLUSH_INTERVAL_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.flush()
            except Exception:
                logger.exception("usage flush failed; events dropped")

    async def flush(self) -> None:
        """Post everything buffered in one request; drop the batch on failure."""
        if not self._events:
            return
        batch = self._events
        self._events = []
        status, _body = await self._humr_client.post_json(
            path=REPORT_PATH,
            payload={"events": batch},
            timeout_seconds=_POST_TIMEOUT_SECONDS,
        )
        if not (200 <= status < 300):
            logger.error("dropped %d usage events after HTTP %d from control plane", len(batch), status)
