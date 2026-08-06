"""The public Stripe webhook boundary for HumR's subscription lifecycle.

Stripe posts the raw signed body here. The billing lifecycle service owns the
SDK, signature parsing, event shapes, mirror writes, plan transitions, and
credit movements. This view owns only HTTP behavior: POST enforcement,
configuration availability, signature failure status, and acknowledgement.
"""

import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from humanityrules_app.services.billing import stripe_lifecycle

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> JsonResponse:
    """Verify and apply one Stripe webhook event."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET is unset; Stripe webhook is unavailable")
        return JsonResponse({"error": "Stripe webhook is not configured"}, status=503)

    signature_header = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe_lifecycle.verify_and_parse_webhook(
            payload=request.body,
            signature_header=signature_header,
        )
    except stripe_lifecycle.WebhookVerificationError as error:
        cause = error.__cause__
        cause_detail = f" ({type(cause).__name__}: {cause})" if cause is not None else ""
        logger.error(f"Stripe webhook rejected: {error}{cause_detail}")
        return JsonResponse({"error": "invalid Stripe signature"}, status=400)

    stripe_lifecycle.apply_webhook_event(event=event)
    return JsonResponse({"ok": True})
