"""The boundary between Stripe and HumR billing: everything Stripe lives here.

The story starts at Checkout: a customer buys an Operator subscription, and we
stamp our organization id into the session's (and the subscription's)
metadata. From then on, Stripe tells us what is happening through signed
webhooks. Stripe delivers webhooks at least once and in no particular order,
so this module is built around two problems: never applying the same event
twice, and never letting an old event overwrite newer state.

Applying exactly once: every verified event is stored in
``StripeWebhookEvent``, keyed on Stripe's own event id, in the same database
transaction that applies its effects. A stored row therefore means "fully
applied": redeliveries of it are skipped, and if applying raises, the row
rolls back with everything else and Stripe retries later. That is also why
handlers raise on malformed events (no ``data.object`` dict, a snapshot
missing its ids or status) instead of ignoring them — raising turns a broken
delivery into a retry instead of silently counting it as done.

The mirror: each organization has one ``BillingSubscription`` row that mirrors
what Stripe last told us. Handlers only copy facts into that row; they never
decide the plan or grant credits themselves. Each event type owns specific
fields:

- ``customer.subscription.*`` events carry a full snapshot and own almost
  everything: status, ids, billing periods, cancellation timestamps. The
  snapshot is written verbatim, ``None`` included, so a newer event can clear
  stale fields.
- ``invoice.paid`` owns one fact: the start of the newest period someone
  actually paid for. It never touches status. Stripe advances the period
  fields even on a failed renewal, so the period alone is not proof of
  payment — the paid invoice is.
- ``invoice.payment_failed`` writes nothing; it is kept for the audit trail.
  Its consequence arrives on the ``customer.subscription.updated`` event
  Stripe sends alongside it.

Ordering: each applied subscription event stamps its envelope timestamp on
the mirror, and any subscription event older than that stamp is dropped — it
stays in the audit trail but cannot move the mirror backward. Timestamps only
have one-second resolution, so ties happen. A tied event that would flip a
canceled subscription back to life is refused, because canceled is terminal:
a dead subscription emits no further events, so a wrong resurrection would
never be corrected. A tie under a *different* subscription id is handled
normally — a genuinely live subscription keeps sending events that will set
the mirror right regardless of which one landed first.

Consequences: after every handled event, one reconciler reads the whole
mirror and acts on it. It derives ``Organization.plan`` from the mirrored
status, resets credits when an Operator drops back to Trial, and grants the
period's credits when the plan is Operator and the paid-period fact matches
the current period (see ``grants.py``). Because it reads accumulated state
rather than the single event that woke it, delivery order between
``invoice.paid`` and the subscription events doesn't matter: whichever event
completes the picture triggers the grant, and the other's pass finds nothing
left to do.

This is the only module that imports Stripe. Checkout and portal callers get
plain URL strings, the webhook view hands over a plain event dict, and the
rest of billing sees only HumR values. Only the handlers in this file write
to ``BillingSubscription``.
"""

import datetime
import json
import logging
from uuid import UUID

from django.conf import settings
from django.db import transaction

import stripe

from humanityrules_app import models
from humanityrules_app.services.billing import grants, plans

logger = logging.getLogger(__name__)

_ORGANIZATION_METADATA_KEY = "organization_id"

_STRIPE_MANAGED_PLANS = frozenset({plans.TRIAL, plans.OPERATOR})

_SUBSCRIPTION_EVENT_TYPES = frozenset({
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
})

_HANDLED_EVENT_TYPES = frozenset({
    *_SUBSCRIPTION_EVENT_TYPES,
    "invoice.paid",
    "invoice.payment_failed",
})


class WebhookVerificationError(ValueError):
    """The Stripe signature or signed event payload could not be verified."""


def create_stripe_operator_checkout_url(organization: models.Organization, success_url: str, cancel_url: str) -> str:
    """Create the hosted Checkout Session for one Operator subscription."""
    metadata = {_ORGANIZATION_METADATA_KEY: str(organization.id)}
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


def create_stripe_portal_url(organization: models.Organization, return_url: str) -> str:
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
    """Verify Stripe's signature and return the event as plain dictionaries.

    The SDK is used only for the signature check. The event itself is the
    signed payload's own JSON parsed into plain dicts, because that is what
    every handler downstream consumes. All failures raise the same error
    type; the message says whether the signature or the payload shape was the
    problem, so the webhook view's log tells them apart.
    """
    try:
        payload_text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WebhookVerificationError("Stripe webhook payload is not UTF-8") from error

    try:
        stripe.WebhookSignature.verify_header(
            payload=payload_text,
            header=signature_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
            tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
        )
    except Exception as error:
        raise WebhookVerificationError("invalid Stripe webhook signature") from error

    try:
        parsed_event = json.loads(payload_text)
    except ValueError as error:
        raise WebhookVerificationError("signed Stripe webhook payload is not valid JSON") from error

    if not isinstance(parsed_event, dict):
        raise WebhookVerificationError("Stripe webhook did not contain an event object")
    
    return parsed_event


@transaction.atomic
def apply_webhook_event(event: dict) -> None:
    """Store and apply one verified Stripe event, exactly once.

    The Stripe event id is the row's primary key, so get_or_create doubles as
    the dedup check. A fresh row means this delivery wins: the row and the
    event's effects commit in the same transaction, so both happen or neither
    does. An existing row means a redelivery of something already applied,
    and dispatch is skipped.
    """
    stripe_event_id, event_type, event_created_at = _event_envelope(event=event)
    _webhook_event, created = models.StripeWebhookEvent.objects.get_or_create(
        stripe_event_id=stripe_event_id,
        defaults={
            "event_type": event_type,
            "stripe_created_at": event_created_at,
            "payload": event,
        },
    )
    if not created:
        logger.info(f"Stripe webhook event {stripe_event_id} was already applied; redelivery skipped")
        return
    
    _dispatch_webhook_event(
        event=event,
        event_type=event_type,
        event_created_at=event_created_at,
        triggering_stripe_event_id=stripe_event_id,
    )


def _session_url(session: object) -> str:
    """Return the plain hosted URL from a Stripe session object."""
    url = getattr(session, "url", None)
    if not isinstance(url, str) or not url:
        raise RuntimeError("Stripe created a session without a hosted URL")
    return url


def _nonempty_string(value: object) -> str | None:
    """Return the value when it is a non-empty string, else None."""
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
    organization_id = metadata.get(_ORGANIZATION_METADATA_KEY)
    if not isinstance(organization_id, str):
        return None
    try:
        parsed_organization_id = UUID(hex=organization_id)
    except ValueError:
        return None
    return models.Organization.objects.filter(id=parsed_organization_id).first()


def _resolve_organization(metadata: object, stripe_subscription_id: str | None) -> models.Organization | None:
    """Find the organization an event belongs to.

    Checkout stamps our organization id into the subscription's metadata, so
    that is checked first. An event carrying no usable metadata falls back to
    whichever organization's mirror already follows this subscription id.
    """
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


def _validated_subscription_event_state(event_type: str, subscription_object: dict) -> tuple[str, str, str]:
    """Extract the customer id, subscription id, and status, raising when any is missing.

    A delete event carries no live status; it simply means the subscription
    is canceled.
    """
    stripe_customer_id = _nonempty_string(value=subscription_object.get("customer"))
    stripe_subscription_id = _nonempty_string(value=subscription_object.get("id"))
    status = "canceled" if event_type == "customer.subscription.deleted" else _nonempty_string(
        value=subscription_object.get("status"),
    )
    missing_fields = [
        field_name
        for field_name, value in (
            ("customer id", stripe_customer_id),
            ("subscription id", stripe_subscription_id),
            ("status", status),
        )
        if value is None
    ]
    if missing_fields:
        raise ValueError(f"Stripe subscription event {event_type} is missing required {', '.join(missing_fields)}")
    return stripe_customer_id, stripe_subscription_id, status


def _invoice_subscription_identity(invoice: dict) -> tuple[str | None, object]:
    """Read subscription identity and metadata from the current Invoice parent shape."""
    parent = invoice.get("parent")
    if not isinstance(parent, dict) or parent.get("type") != "subscription_details":
        return None, None
    subscription_details = parent.get("subscription_details")
    if not isinstance(subscription_details, dict):
        return None, None
    subscription_reference = subscription_details.get("subscription")
    if isinstance(subscription_reference, dict):
        stripe_subscription_id = _nonempty_string(value=subscription_reference.get("id"))
    else:
        stripe_subscription_id = _nonempty_string(value=subscription_reference)
    return stripe_subscription_id, subscription_details.get("metadata")


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


def _write_subscription_event_mirror(
    organization: models.Organization,
    stripe_customer_id: str,
    stripe_subscription_id: str,
    status: str,
    current_period_start: datetime.datetime | None,
    current_period_end: datetime.datetime | None,
    cancel_at: datetime.datetime | None,
    canceled_at: datetime.datetime | None,
    event_created_at: datetime.datetime,
) -> models.BillingSubscription:
    """Overwrite the mirror with one complete subscription snapshot.

    The caller has already validated the snapshot and decided it should win.
    Every field this event family owns is written verbatim, ``None``
    included, so a newer event can clear stale periods or cancellation
    timestamps.
    """
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if subscription is None:
        return models.BillingSubscription.objects.create(
            organization=organization,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            status=status,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            cancel_at=cancel_at,
            canceled_at=canceled_at,
            latest_subscription_event_created_at=event_created_at,
        )

    subscription.stripe_customer_id = stripe_customer_id
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.status = status
    subscription.current_period_start = current_period_start
    subscription.current_period_end = current_period_end
    subscription.cancel_at = cancel_at
    subscription.canceled_at = canceled_at
    subscription.latest_subscription_event_created_at = event_created_at
    subscription.save(update_fields=[
        "stripe_customer_id",
        "stripe_subscription_id",
        "status",
        "current_period_start",
        "current_period_end",
        "cancel_at",
        "canceled_at",
        "latest_subscription_event_created_at",
        "updated_at",
    ])
    return subscription


def _write_invoice_subscription_facts(
    organization: models.Organization,
    stripe_customer_id: str | None,
    stripe_subscription_id: str | None,
    current_period_start: datetime.datetime | None,
    current_period_end: datetime.datetime | None,
) -> models.BillingSubscription | None:
    """Merge the few facts an invoice carries into the mirror.

    Unlike a subscription snapshot, an invoice is sparse: ``None`` means "the
    invoice didn't say", so existing fields are left alone rather than
    cleared. An invoice arriving before any subscription event may create the
    mirror, but only if it carries both ids — and it never invents a status.
    """
    subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if subscription is None:
        if stripe_customer_id is None or stripe_subscription_id is None:
            logger.error(
                f"cannot create BillingSubscription for organization {organization.id} without customer id, "
                "and subscription id"
            )
            return None
        return models.BillingSubscription.objects.create(
            organization=organization,
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )

    updated_fields: list[str] = []
    for field_name, value in (
        ("stripe_customer_id", stripe_customer_id),
        ("stripe_subscription_id", stripe_subscription_id),
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


def _transition_organization_plan(subscription: models.BillingSubscription) -> tuple[str, str]:
    """Set ``Organization.plan`` from the mirrored status and return ``(previous_plan, resulting_plan)``.

    Stripe only manages Trial and Operator. Team and Enterprise organizations
    are hand-managed, so their plan never changes here no matter what the
    subscription status says — the returned transition has equal values.
    """
    organization = subscription.organization
    previous_plan = organization.plan
    derived_plan = plans.OPERATOR if subscription.status in models.BillingSubscription.OPERATOR_STATUSES else plans.TRIAL
    if previous_plan not in _STRIPE_MANAGED_PLANS:
        logger.error(
            f"Stripe subscription {subscription.stripe_subscription_id} implies plan {derived_plan} for hand-managed "
            f"organization {organization.id} on plan {previous_plan}; plan unchanged"
        )
        return previous_plan, previous_plan
    if previous_plan == derived_plan:
        return previous_plan, previous_plan
    organization.plan = derived_plan
    organization.save(update_fields=["plan", "updated_at"])
    return previous_plan, derived_plan


def _reconcile_subscription(subscription: models.BillingSubscription, triggering_stripe_event_id: str) -> None:
    """Turn whatever the mirror currently says into plan and credit changes.

    First the plan. An Operator dropping back to Trial gets its trial credits
    restarted, keyed on the triggering event rather than on the subscription:
    one subscription can drop to Trial for an unpaid period, recover to
    Operator, and later drop again at cancellation — each drop is its own
    reset.

    Then the grant. Credits are only granted when the plan landed on Operator
    AND the paid-period fact matches the subscription's current period. That
    match is what stops a paid invoice for an old period, or for a
    subscription the mirror no longer follows, from granting against the
    wrong period.

    Both paths are idempotent (see ``grants.py``), so reconciling the same
    state twice changes nothing — which is what makes it safe to run after
    every single event.
    """
    organization = subscription.organization
    previous_plan, resulting_plan = _transition_organization_plan(subscription=subscription)
    
    if previous_plan == plans.OPERATOR and resulting_plan == plans.TRIAL:
        grants.restart_trial_credits(
            organization=organization,
            stripe_event_id=triggering_stripe_event_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )
    
    if (
        resulting_plan != plans.OPERATOR
        or subscription.latest_paid_period_start is None
        or subscription.latest_paid_period_start != subscription.current_period_start
    ):
        return
    
    grants.grant_operator_period_credits(
        organization=organization,
        stripe_subscription_id=subscription.stripe_subscription_id,
        period_start=subscription.latest_paid_period_start.date(),
    )


def _event_names_superseded_subscription(
    current_subscription: models.BillingSubscription | None,
    stripe_subscription_id: str | None,
) -> bool:
    """Does this event name a different subscription than the live one the mirror follows?

    The mirror follows one subscription at a time. While that subscription is
    alive, an event about any other id means the organization has moved on
    from that other subscription, and applying its facts would overwrite the
    live one's state. Once the followed subscription is gone (unset or
    canceled) there is nothing left to protect, and a new id is free to take
    over the mirror.
    """
    return (
        current_subscription is not None
        and stripe_subscription_id is not None
        and current_subscription.stripe_subscription_id != stripe_subscription_id
        and current_subscription.status not in {None, "canceled"}
    )


def _created_event_names_second_live_subscription(
    event_type: str,
    current_subscription: models.BillingSubscription | None,
    stripe_subscription_id: str,
) -> bool:
    """Does this create event reveal a second live subscription?

    The mirror has room for exactly one subscription. A new id showing up
    before the followed one is canceled suggests Stripe now holds two live
    subscriptions for this organization. The event is still applied through
    the normal ordering path; this check only flags the anomaly so a human
    can clean up.
    """
    return (
        event_type == "customer.subscription.created"
        and current_subscription is not None
        and current_subscription.stripe_subscription_id != stripe_subscription_id
        and current_subscription.status != "canceled"
    )


def _event_is_older_than_mirrored_subscription_state(
    current_subscription: models.BillingSubscription | None,
    event_created_at: datetime.datetime,
) -> bool:
    """Is this event older than the state already in the mirror?

    Stripe can deliver events in any order. The newest applied envelope time
    acts as the mirror's high-water mark: an older event stays in the webhook
    audit trail but is not allowed to move the mirror backward.
    """
    return (
        current_subscription is not None
        and current_subscription.latest_subscription_event_created_at is not None
        and event_created_at < current_subscription.latest_subscription_event_created_at
    )


def _event_would_resurrect_canceled_subscription(
    current_subscription: models.BillingSubscription | None,
    stripe_subscription_id: str,
    status: str,
    event_created_at: datetime.datetime,
) -> bool:
    """Would this equal-timestamp event bring a canceled subscription back to life?

    Stripe timestamps have one-second resolution, so two events can tie. When
    the tie is on the same subscription and the mirror already says canceled,
    canceled wins: it is a terminal state, and only a strictly newer event may
    replace it.
    """
    return (
        current_subscription is not None
        and current_subscription.latest_subscription_event_created_at is not None
        and event_created_at == current_subscription.latest_subscription_event_created_at
        and current_subscription.stripe_subscription_id == stripe_subscription_id
        and current_subscription.status == "canceled"
        and status != "canceled"
    )


def _apply_subscription_event(
    event_type: str,
    event_created_at: datetime.datetime,
    subscription_object: dict,
) -> models.BillingSubscription | None:
    """Mirror one subscription event, unless a newer event already won.

    The organization row is locked before any timestamps are compared, so two
    webhooks for the same subscription delivered concurrently cannot both
    read the same stale "latest event" and race into the wrong final state.

    The guards then run in order: a create event revealing a second live
    subscription is logged for manual cleanup but still applied normally; an
    update or delete for a superseded subscription is skipped; an event older
    than the mirror is skipped; an equal-timestamp resurrection of a canceled
    subscription is refused. Whatever survives overwrites the mirror.
    """
    stripe_customer_id, stripe_subscription_id, status = _validated_subscription_event_state(
        event_type=event_type,
        subscription_object=subscription_object,
    )
    organization = _resolve_organization(
        metadata=subscription_object.get("metadata"),
        stripe_subscription_id=stripe_subscription_id,
    )
    if organization is None:
        logger.error(f"cannot resolve organization for Stripe subscription event {event_type} ({stripe_subscription_id})")
        return None

    organization = models.Organization.objects.select_for_update().get(id=organization.id)
    current_subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if _created_event_names_second_live_subscription(
        event_type=event_type,
        current_subscription=current_subscription,
        stripe_subscription_id=stripe_subscription_id,
    ):
        logger.error(
            f"organization {organization.id} has multiple live Stripe subscriptions: existing "
            f"{current_subscription.stripe_subscription_id}, newly created {stripe_subscription_id}; "
            "manual intervention required"
        )
    if (
        event_type in {"customer.subscription.updated", "customer.subscription.deleted"}
        and _event_names_superseded_subscription(
            current_subscription=current_subscription,
            stripe_subscription_id=stripe_subscription_id,
        )
    ):
        logger.error(
            f"Stripe subscription event {event_type} for superseded subscription {stripe_subscription_id} cannot "
            f"update organization {organization.id}'s live subscription mirror "
            f"{current_subscription.stripe_subscription_id}; event facts skipped"
        )
        return current_subscription

    if _event_is_older_than_mirrored_subscription_state(
        current_subscription=current_subscription,
        event_created_at=event_created_at,
    ):
        logger.info(
            f"Stripe subscription event {event_type} for {stripe_subscription_id} was superseded by newer state "
            f"from {current_subscription.latest_subscription_event_created_at.isoformat()}; mirror unchanged"
        )
        return current_subscription
    if _event_would_resurrect_canceled_subscription(
        current_subscription=current_subscription,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        event_created_at=event_created_at,
    ):
        logger.info(
            f"Stripe subscription event {event_type} for {stripe_subscription_id} matched canceled state from "
            f"{current_subscription.latest_subscription_event_created_at.isoformat()}; equal-timestamp tie resolved "
            "in favor of terminal canceled status; mirror unchanged"
        )
        return current_subscription

    current_period_start, current_period_end = _subscription_period(subscription_object=subscription_object)
    return _write_subscription_event_mirror(
        organization=organization,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancel_at=_datetime_from_timestamp(value=subscription_object.get("cancel_at")),
        canceled_at=_datetime_from_timestamp(value=subscription_object.get("canceled_at")),
        event_created_at=event_created_at,
    )


def _apply_paid_invoice(invoice: dict) -> models.BillingSubscription | None:
    """Record what a paid invoice proves: identity, periods, and the paid-period fact.

    Never a status — that belongs to subscription events. An invoice for a
    subscription other than the live one the mirror follows is skipped
    entirely, so a superseded subscription's invoice cannot overwrite the
    live mirror. The invoice may arrive before any subscription event; in
    that case it creates the mirror itself, so the payment is recorded either
    way. The paid-period fact only moves forward: a late invoice for an older
    period does not rewind it.
    """
    stripe_subscription_id, metadata = _invoice_subscription_identity(invoice=invoice)
    organization = _resolve_organization(metadata=metadata, stripe_subscription_id=stripe_subscription_id)
    if organization is None:
        logger.error(f"cannot resolve organization for paid Stripe invoice subscription {stripe_subscription_id}")
        return None

    organization = models.Organization.objects.select_for_update().get(id=organization.id)
    current_subscription = models.BillingSubscription.objects.filter(organization=organization).first()
    if _event_names_superseded_subscription(
        current_subscription=current_subscription,
        stripe_subscription_id=stripe_subscription_id,
    ):
        logger.error(
            f"paid Stripe invoice for superseded subscription {stripe_subscription_id} cannot update organization "
            f"{organization.id}'s live subscription mirror {current_subscription.stripe_subscription_id}; "
            "invoice facts skipped"
        )
        return current_subscription

    current_period_start, current_period_end = _invoice_subscription_period(invoice=invoice)
    subscription = _write_invoice_subscription_facts(
        organization=organization,
        stripe_customer_id=_nonempty_string(value=invoice.get("customer")),
        stripe_subscription_id=stripe_subscription_id,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )
    if subscription is None:
        return None

    if current_period_start is None:
        logger.error(
            f"paid Stripe invoice for subscription {stripe_subscription_id} has no subscription line-item period; "
            "paid-period fact unchanged"
        )
        return subscription
    if subscription.latest_paid_period_start is None or current_period_start >= subscription.latest_paid_period_start:
        subscription.latest_paid_period_start = current_period_start
        subscription.save(update_fields=["latest_paid_period_start", "updated_at"])
    return subscription


def _resolve_subscription_for_failed_invoice(invoice: dict) -> models.BillingSubscription | None:
    """Find the mirror so reconciliation can run; record nothing from the invoice.

    A failed payment matters to the lifecycle, but the invoice itself carries
    no fact the mirror trusts. The status change it implies arrives
    separately, on the ``customer.subscription.updated`` event Stripe sends
    alongside it.
    """
    stripe_subscription_id, metadata = _invoice_subscription_identity(invoice=invoice)
    organization = _resolve_organization(metadata=metadata, stripe_subscription_id=stripe_subscription_id)
    if organization is None:
        logger.error(f"cannot resolve organization for failed Stripe invoice subscription {stripe_subscription_id}")
        return None

    organization = models.Organization.objects.select_for_update().get(id=organization.id)
    return models.BillingSubscription.objects.filter(organization=organization).first()


def _event_envelope(event: dict) -> tuple[str, str, datetime.datetime]:
    """Extract the id, type, and Stripe-assigned creation time used to store and order this event."""
    stripe_event_id = _nonempty_string(value=event.get("id"))
    event_type = _nonempty_string(value=event.get("type"))
    event_created_at = _datetime_from_timestamp(value=event.get("created"))
    if stripe_event_id is None or event_type is None or event_created_at is None:
        raise ValueError("verified Stripe webhook is missing id, type, or created")
    return stripe_event_id, event_type, event_created_at


def _dispatch_webhook_event(
    event: dict,
    event_type: str,
    event_created_at: datetime.datetime,
    triggering_stripe_event_id: str,
) -> None:
    """Route one supported event to its handler; unrelated event types are silent no-ops.

    This is the one place that requires ``data.object`` to be a dictionary,
    for subscription and invoice events alike. Raising here happens inside
    the retention transaction, so the event row rolls back and Stripe
    redelivers instead of counting a malformed event as done.
    """
    if event_type not in _HANDLED_EVENT_TYPES:
        return
    
    event_data = event.get("data")
    stripe_object = event_data.get("object") if isinstance(event_data, dict) else None
    if not isinstance(stripe_object, dict):
        raise ValueError(f"handled Stripe event {event_type} requires data.object to be an object")

    subscription: models.BillingSubscription | None
    if event_type in _SUBSCRIPTION_EVENT_TYPES:
        subscription = _apply_subscription_event(
            event_type=event_type,
            event_created_at=event_created_at,
            subscription_object=stripe_object,
        )
    elif event_type == "invoice.paid":
        subscription = _apply_paid_invoice(invoice=stripe_object)
    elif event_type == "invoice.payment_failed":
        subscription = _resolve_subscription_for_failed_invoice(invoice=stripe_object)
    else:
        raise AssertionError(f"handled Stripe event type {event_type!r} has no dispatcher branch")
    
    if subscription is not None:
        _reconcile_subscription(
            subscription=subscription,
            triggering_stripe_event_id=triggering_stripe_event_id,
        )
