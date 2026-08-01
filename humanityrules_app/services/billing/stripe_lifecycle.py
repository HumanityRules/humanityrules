"""The boundary between Stripe's subscription lifecycle and HumR billing.

Checkout starts an Operator subscription and stamps the organization id on
both the Checkout Session and the future subscription. Stripe then drives all
state through signed webhooks. Subscription events own the local
``BillingSubscription`` mirror's status, while invoice events own its paid
period. One transition function turns the last subscription status into
``Organization.plan``; paid invoices atomically expire a positive prior-period
remainder before granting the new period's credits.

This is the only module that imports Stripe. Checkout and portal callers get
plain URL strings, the webhook view gets a plain event dict, and models plus
other billing services know only HumR values. Keeping the SDK at this boundary
also makes the mirror's writer rule explicit: only the handlers below mutate a
``BillingSubscription`` row.
"""

import datetime
import logging
from uuid import UUID

from django.conf import settings

import stripe

from humanityrules_app import models
from humanityrules_app.services.billing import grants, plans

logger = logging.getLogger(__name__)

ORGANIZATION_METADATA_KEY = "organization_id"
STRIPE_MANAGED_PLANS = frozenset({plans.TRIAL, plans.OPERATOR})
SUBSCRIPTION_EVENT_TYPES = frozenset({
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})
HANDLED_EVENT_TYPES = frozenset({
    *SUBSCRIPTION_EVENT_TYPES,
    "invoice.paid",
    "invoice.payment_failed",
})


class WebhookVerificationError(ValueError):
    """The Stripe signature or signed event payload could not be verified."""


def _session_url(session: object) -> str:
    """Return the plain hosted URL from a Stripe session object."""
    url = getattr(session, "url", None)
    if not isinstance(url, str) or not url:
        raise RuntimeError("Stripe created a session without a hosted URL")
    return url


def create_operator_checkout_url(organization: models.Organization, success_url: str, cancel_url: str) -> str:
    """Create the hosted Checkout Session for one Operator subscription."""
    metadata = {ORGANIZATION_METADATA_KEY: str(organization.id)}
    checkout_params: dict[str, object] = {
        "mode": "subscription",
        "line_items": [{"price": settings.STRIPE_OPERATOR_PRICE_ID, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": metadata,
        "subscription_data": {"metadata": metadata},
    }
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if subscription is not None and subscription.stripe_customer_id:
        checkout_params["customer"] = subscription.stripe_customer_id

    stripe_client = stripe.StripeClient(api_key=settings.STRIPE_SECRET_KEY)
    session = stripe_client.v1.checkout.sessions.create(params=checkout_params, options=None)
    return _session_url(session=session)


def create_portal_url(organization: models.Organization, return_url: str) -> str:
    """Create the hosted billing portal for an organization's Stripe customer."""
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if subscription is None or not subscription.stripe_customer_id:
        raise ValueError(f"organization {organization.id} has no Stripe customer for a billing portal session")

    stripe_client = stripe.StripeClient(api_key=settings.STRIPE_SECRET_KEY)
    session = stripe_client.v1.billing_portal.sessions.create(
        params={"customer": subscription.stripe_customer_id, "return_url": return_url},
        options=None,
    )
    return _session_url(session=session)


def verify_and_parse_webhook(payload: bytes, signature_header: str) -> dict:
    """Verify Stripe's signature and return the event as recursive plain dictionaries."""
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
        if hasattr(event, "to_dict_recursive"):
            parsed_event = event.to_dict_recursive()
        else:
            parsed_event = dict(event)
    except Exception as error:
        raise WebhookVerificationError("invalid Stripe webhook signature or payload") from error

    if not isinstance(parsed_event, dict):
        raise WebhookVerificationError("Stripe webhook did not contain an event object")
    return parsed_event


def _string_id(value: object) -> str | None:
    """Return a non-empty Stripe id string and reject every other value shape."""
    return value if isinstance(value, str) and value else None


def _datetime_from_timestamp(value: object) -> datetime.datetime | None:
    """Convert a Stripe Unix timestamp to an aware UTC datetime when present."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return datetime.datetime.fromtimestamp(value, tz=datetime.UTC)


def _organization_from_metadata(metadata: object) -> models.Organization | None:
    """Resolve the organization id stamped onto Checkout's subscription metadata."""
    if not isinstance(metadata, dict):
        return None
    organization_id = metadata.get(ORGANIZATION_METADATA_KEY)
    if not isinstance(organization_id, str):
        return None
    try:
        parsed_organization_id = UUID(hex=organization_id)
    except ValueError:
        return None
    return models.Organization.objects.filter(id=parsed_organization_id).first()


def _resolve_organization(metadata: object, stripe_subscription_id: str | None) -> models.Organization | None:
    """Resolve metadata first, then use an existing mirror when an older event lacks it."""
    organization = _organization_from_metadata(metadata=metadata)
    if organization is not None:
        return organization
    if stripe_subscription_id is None:
        return None
    subscription = models.BillingSubscription.objects.select_related("organization").filter(
        stripe_subscription_id=stripe_subscription_id,
    ).first()
    return subscription.organization if subscription is not None else None


def _subscription_period(subscription_object: dict) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Read the single Operator item's billing period from Stripe's current Subscription shape."""
    items = subscription_object.get("items")
    if not isinstance(items, dict):
        return None, None
    item_data = items.get("data")
    if not isinstance(item_data, list) or not item_data or not isinstance(item_data[0], dict):
        return None, None
    item = item_data[0]
    return (
        _datetime_from_timestamp(value=item.get("current_period_start")),
        _datetime_from_timestamp(value=item.get("current_period_end")),
    )


def _invoice_subscription_identity(invoice: dict) -> tuple[str | None, object, str | None]:
    """Read subscription identity, metadata, and an expanded status from the current Invoice parent shape."""
    parent = invoice.get("parent")
    if not isinstance(parent, dict) or parent.get("type") != "subscription_details":
        return None, None, None
    subscription_details = parent.get("subscription_details")
    if not isinstance(subscription_details, dict):
        return None, None, None
    subscription_reference = subscription_details.get("subscription")
    if isinstance(subscription_reference, dict):
        stripe_subscription_id = _string_id(value=subscription_reference.get("id"))
        subscription_status = _string_id(value=subscription_reference.get("status"))
    else:
        stripe_subscription_id = _string_id(value=subscription_reference)
        subscription_status = None
    return stripe_subscription_id, subscription_details.get("metadata"), subscription_status


def _invoice_subscription_period(invoice: dict) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """Read the paid period from the first invoice subscription line that carries one."""
    lines = invoice.get("lines")
    if not isinstance(lines, dict):
        return None, None
    line_data = lines.get("data")
    if not isinstance(line_data, list):
        return None, None
    for line in line_data:
        if not isinstance(line, dict):
            continue
        period = line.get("period")
        if not isinstance(period, dict):
            continue
        return (
            _datetime_from_timestamp(value=period.get("start")),
            _datetime_from_timestamp(value=period.get("end")),
        )
    return None, None


def _upsert_subscription_mirror(
    organization: models.Organization,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    status: str | None,
    current_period_start: datetime.datetime | None,
    current_period_end: datetime.datetime | None,
) -> models.BillingSubscription | None:
    """Apply plain Stripe values to the organization's one mirror row."""
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if subscription is None:
        if stripe_customer_id is None or stripe_subscription_id is None or status is None:
            logger.error(
                f"cannot create BillingSubscription for organization {organization.id} without customer id, "
                f"subscription id, and status"
            )
            return None
        return models.BillingSubscription.objects.create(
            organization=organization,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            status=status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )

    updated_fields: list[str] = []
    for field_name, value in (
        ("stripe_customer_id", stripe_customer_id),
        ("stripe_subscription_id", stripe_subscription_id),
        ("status", status),
        ("current_period_start", current_period_start),
        ("current_period_end", current_period_end),
    ):
        if value is None:
            continue
        setattr(subscription, field_name, value)
        updated_fields.append(field_name)
    if updated_fields:
        subscription.save(update_fields=[*updated_fields, "updated_at"])
    return subscription


def transition_organization_plan(subscription: models.BillingSubscription) -> None:
    """Derive Trial or Operator from subscription status without touching hand-managed plans."""
    organization = subscription.organization
    derived_plan = plans.OPERATOR if subscription.status in models.BillingSubscription.OPERATOR_STATUSES else plans.TRIAL
    if organization.plan not in STRIPE_MANAGED_PLANS:
        logger.error(
            f"Stripe subscription {subscription.stripe_subscription_id} implies plan {derived_plan} for hand-managed "
            f"organization {organization.id} on plan {organization.plan}; plan unchanged"
        )
        return
    if organization.plan == derived_plan:
        return
    organization.plan = derived_plan
    organization.save(update_fields=["plan", "updated_at"])


def _apply_subscription_event(event_type: str, subscription_object: dict) -> None:
    """Mirror one subscription event and apply its plan transition."""
    stripe_subscription_id = _string_id(value=subscription_object.get("id"))
    organization = _resolve_organization(
        metadata=subscription_object.get("metadata"),
        stripe_subscription_id=stripe_subscription_id,
    )
    if organization is None:
        logger.error(f"cannot resolve organization for Stripe subscription event {event_type} ({stripe_subscription_id})")
        return

    status = "canceled" if event_type == "customer.subscription.deleted" else _string_id(
        value=subscription_object.get("status"),
    )
    current_period_start, current_period_end = _subscription_period(subscription_object=subscription_object)
    subscription = _upsert_subscription_mirror(
        organization=organization,
        stripe_customer_id=_string_id(value=subscription_object.get("customer")),
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )
    if subscription is not None:
        transition_organization_plan(subscription=subscription)


def _apply_paid_invoice(invoice: dict) -> None:
    """Mirror a paid period, transition the plan, then post expiry and grant."""
    stripe_subscription_id, metadata, _subscription_status = _invoice_subscription_identity(invoice=invoice)
    organization = _resolve_organization(metadata=metadata, stripe_subscription_id=stripe_subscription_id)
    if organization is None:
        logger.error(f"cannot resolve organization for paid Stripe invoice subscription {stripe_subscription_id}")
        return

    current_period_start, current_period_end = _invoice_subscription_period(invoice=invoice)
    mirror_exists = models.BillingSubscription.objects.filter(organization=organization).exists()
    subscription = _upsert_subscription_mirror(
        organization=organization,
        stripe_customer_id=_string_id(value=invoice.get("customer")),
        stripe_subscription_id=stripe_subscription_id,
        status=None if mirror_exists else "active",
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )
    if subscription is None:
        return

    transition_organization_plan(subscription=subscription)
    if current_period_start is None or current_period_end is None:
        logger.error(
            f"paid Stripe invoice for subscription {stripe_subscription_id} has no subscription line-item period; "
            f"credits not granted"
        )
        return
    grants.grant_operator_period_credits(
        organization=organization,
        stripe_subscription_id=subscription.stripe_subscription_id,
        period_start=current_period_start.date(),
    )


def _apply_failed_invoice(invoice: dict) -> None:
    """Update a carried subscription status without changing the plan during retries."""
    stripe_subscription_id, metadata, subscription_status = _invoice_subscription_identity(invoice=invoice)
    organization = _resolve_organization(metadata=metadata, stripe_subscription_id=stripe_subscription_id)
    if organization is None:
        logger.error(f"cannot resolve organization for failed Stripe invoice subscription {stripe_subscription_id}")
        return

    if subscription_status is None:
        logger.error(
            f"failed Stripe invoice for subscription {stripe_subscription_id} carries no subscription status; "
            f"mirror unchanged"
        )
        return
    _upsert_subscription_mirror(
        organization=organization,
        stripe_customer_id=_string_id(value=invoice.get("customer")),
        stripe_subscription_id=stripe_subscription_id,
        status=subscription_status,
        current_period_start=None,
        current_period_end=None,
    )


def apply_webhook_event(event: dict) -> None:
    """Apply one supported Stripe event; unrelated event types are silent no-ops."""
    event_type = event.get("type")
    if event_type not in HANDLED_EVENT_TYPES:
        return
    event_data = event.get("data")
    stripe_object = event_data.get("object") if isinstance(event_data, dict) else None
    if not isinstance(stripe_object, dict):
        logger.error(f"Stripe event {event_type} has no data.object")
        return

    if event_type in SUBSCRIPTION_EVENT_TYPES:
        _apply_subscription_event(event_type=event_type, subscription_object=stripe_object)
    elif event_type == "invoice.paid":
        _apply_paid_invoice(invoice=stripe_object)
    else:
        _apply_failed_invoice(invoice=stripe_object)
