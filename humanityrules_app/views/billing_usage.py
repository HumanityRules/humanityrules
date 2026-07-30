"""Billing usage-event ingestion from each Hermes agent's integrations broker.

The broker is a per-app sidecar (one per HA container; an environment hosts
many), so one env bearer can report for several apps — the bearer names only
the environment, and the payload's app_slug picks the app within it. Each
broker batches BillingUsageEvents and posts them here (see
`tls_usage_metering` in the hermes_agent template). Inserts are
idempotent on the broker-minted key — retried or duplicated batches are
no-ops — and the whole batch is rejected on the first malformed event, since
the reporter is trusted platform code and a malformed event means a bug, not
customer input.
"""

import datetime
import json

from django.http import HttpRequest, JsonResponse
from django.utils import dateparse, timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app import models
from . import env_bearer_auth

MAX_EVENTS_PER_REPORT = 500
MAX_FUTURE_CLOCK_SKEW = datetime.timedelta(minutes=5)

# Per-source schema for the `quantities` dict: exactly these keys, each a
# non-negative int. A key set mismatch means a broker bug — reject loudly.
_QUANTITY_KEYS_BY_SOURCE = {
    "llm": frozenset({"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"}),
}


def _parse_event(event: object, now: datetime.datetime) -> dict | str:
    """Validate one reported event into model kwargs, or return an error string."""
    if not isinstance(event, dict):
        return "event must be a JSON object"
    idempotency_key = event.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        return "idempotency_key must be a string of 1-128 characters"
    source = event.get("source")
    if source not in _QUANTITY_KEYS_BY_SOURCE:
        return f"unknown source: {source!r}"
    subkey = event.get("subkey")
    if not isinstance(subkey, str) or len(subkey) > 255:
        return "subkey must be a string of at most 255 characters"
    quantities = event.get("quantities")
    if not isinstance(quantities, dict):
        return "quantities must be a JSON object"
    expected_keys = _QUANTITY_KEYS_BY_SOURCE[source]
    if set(quantities) != expected_keys:
        return f"quantities keys for source {source!r} must be exactly {sorted(expected_keys)}"
    for quantity_name, quantity in quantities.items():
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            return f"quantities.{quantity_name} must be a non-negative integer"
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
    for index, event in enumerate(events):
        parsed = _parse_event(event=event, now=now)
        if isinstance(parsed, str):
            return JsonResponse({"error": f"events[{index}]: {parsed}"}, status=400)
        rows.append(models.BillingUsageEvent(
            organization=organization,
            app_id=app.id,
            app_slug=app.slug,
            owner_username=owner_username,
            **parsed,
        ))

    models.BillingUsageEvent.objects.bulk_create(rows, ignore_conflicts=True)
    return JsonResponse({"ok": True})
