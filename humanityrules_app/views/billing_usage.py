"""Billing usage-event ingestion from each Hermes agent's integrations broker.

The broker is a per-app sidecar (one per HA container; an environment hosts
many), so one env bearer can report for several apps — the bearer names only
the environment, and the payload's app_slug picks the app within it. Each
broker batches BillingUsageEvents and posts them here (see
`tls_usage_metering` in the hermes_agent template). Inserts are
idempotent on the broker-minted key — retried or duplicated batches are
no-ops.

A malformed event rejects the whole batch: the reporter is trusted platform
code, so malformed means a bug, not customer input. Version skew is not a
bug and never rejects. Brokers ship inside HA images and redeploy long after
the CP does, so an old broker reporting fewer quantity keys, or a new one
reporting a source this CP predates, is the normal steady state — the
missing keys read as 0 and the unknown-source events are skipped. The
reverse skew (a broker ahead of the CP, i.e. a template shipped before the
CP that understands it) does reject, because the CP cannot store a quantity
rating will need.
"""

import datetime
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils import dateparse, timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app import models
from . import env_bearer_auth

logger = logging.getLogger(__name__)

MAX_EVENTS_PER_REPORT = 500
MAX_FUTURE_CLOCK_SKEW = datetime.timedelta(minutes=5)

# Per-source schema for the `quantities` dict: these keys, each a non-negative
# int. Absent keys read as 0 (an older broker had nothing to say about them);
# keys outside the set are a broker bug or a CP that is behind its fleet. The
# order is the stored order, so it is also the order the admin displays.
_QUANTITY_KEYS_BY_SOURCE = {
    "llm": ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"),
}


def _parse_event(event: dict, now: datetime.datetime) -> dict | str | None:
    """Validate one reported event into model kwargs, an error string, or None to skip its unknown source."""
    idempotency_key = event.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        return "idempotency_key must be a string of 1-128 characters"
    source = event.get("source")
    if source not in _QUANTITY_KEYS_BY_SOURCE:
        return None
    subkey = event.get("subkey")
    if not isinstance(subkey, str) or len(subkey) > 255:
        return "subkey must be a string of at most 255 characters"
    reported_quantities = event.get("quantities")
    if not isinstance(reported_quantities, dict):
        return "quantities must be a JSON object"
    expected_keys = _QUANTITY_KEYS_BY_SOURCE[source]
    unknown_keys = set(reported_quantities) - set(expected_keys)
    if unknown_keys:
        return f"quantities keys for source {source!r} are not recognized: {sorted(unknown_keys)}"
    quantities: dict[str, int] = {}
    for quantity_name in expected_keys:
        quantity = reported_quantities.get(quantity_name, 0)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            return f"quantities.{quantity_name} must be a non-negative integer"
        quantities[quantity_name] = quantity
    occurred_at_value = event.get("occurred_at")
    if not isinstance(occurred_at_value, str):
        return "occurred_at is required"
    occurred_at = dateparse.parse_datetime(occurred_at_value)
    if occurred_at is None or timezone.is_naive(occurred_at):
        return "occurred_at must be an ISO 8601 timestamp with a timezone"
    if occurred_at > now + MAX_FUTURE_CLOCK_SKEW:
        return "occurred_at is too far in the future"
    return {
        "idempotency_key": idempotency_key,
        "source": source,
        "subkey": subkey,
        "quantities": quantities,
        "occurred_at": occurred_at,
    }


@csrf_exempt
@require_POST
def billing_usage_events(request: HttpRequest) -> JsonResponse:
    """Insert a batch of broker-reported usage events, ignoring duplicates."""
    raw_token = env_bearer_auth.extract_bearer_token(request=request)
    if raw_token is None:
        return JsonResponse({"error": "missing bearer token"}, status=401)
    environment = env_bearer_auth.resolve_env_from_token(raw_token=raw_token)
    if environment is None:
        return JsonResponse({"error": "invalid bearer token"}, status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid JSON body"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "JSON object body is required"}, status=400)

    owner_username = payload.get("owner_username")
    app_slug = payload.get("app_slug")
    events = payload.get("events")
    if not isinstance(owner_username, str) or not owner_username:
        return JsonResponse({"error": "owner_username is required"}, status=400)
    if not isinstance(app_slug, str) or not app_slug:
        return JsonResponse({"error": "app_slug is required"}, status=400)
    if not isinstance(events, list):
        return JsonResponse({"error": "events must be a list"}, status=400)
    if len(events) > MAX_EVENTS_PER_REPORT:
        return JsonResponse({"error": f"at most {MAX_EVENTS_PER_REPORT} events per report"}, status=400)

    organization = environment.aws_account.organization
    app = models.App.objects.filter(
        organization=organization,
        slug=app_slug,
        environment=environment,
    ).first()
    if app is None:
        return JsonResponse({"error": "app not found in environment"}, status=404)

    now = timezone.now()
    rows: list[models.BillingUsageEvent] = []
    skipped_sources: list[object] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return JsonResponse({"error": f"events[{index}]: event must be a JSON object"}, status=400)
        parsed = _parse_event(event=event, now=now)
        if isinstance(parsed, str):
            return JsonResponse({"error": f"events[{index}]: {parsed}"}, status=400)
        if parsed is None:
            skipped_sources.append(event.get("source"))
            continue
        rows.append(models.BillingUsageEvent(
            organization=organization,
            app_id=app.id,
            app_slug=app.slug,
            owner_username=owner_username,
            **parsed,
        ))

    if skipped_sources:
        logger.error(
            "skipped %d usage events from app %s reporting sources this control plane does not know: %s",
            len(skipped_sources), app.slug, sorted({str(source) for source in skipped_sources}),
        )
    models.BillingUsageEvent.objects.bulk_create(rows, ignore_conflicts=True)
    return JsonResponse({"ok": True, "skipped": len(skipped_sources)})
