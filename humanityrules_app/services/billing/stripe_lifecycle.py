"""The boundary between Stripe's subscription lifecycle and HumR billing.

Checkout starts an Operator subscription and stamps the organization id on
both the Checkout Session and the future subscription. Stripe then drives all
state through signed webhooks, delivered at least once and in no guaranteed
order. Every verified event is persisted in ``StripeWebhookEvent`` before it
is applied, keyed on Stripe's own event id, so a redelivered event is
recognized and skipped instead of applied twice. Recording the event and
applying its effects happen in the same database transaction, so a
``StripeWebhookEvent`` row existing means everything it triggered committed
too.

Handlers only record the facts their event carries in the organization's one
``BillingSubscription`` mirror row; none of them decide the plan or post a
grant themselves. Every handled event first requires ``data.object`` to be a
dictionary. A missing or differently shaped object raises inside the retention
transaction, rolling back the event row so Stripe retries instead of treating
an unapplied event as complete. This outer-object requirement applies equally
to subscription and invoice events; accepted invoice objects may still omit
individual sparse facts.

``customer.subscription.*`` events own status, periods, the customer and
subscription ids, and the cancellation timestamps, copying Stripe's full
current state. A subscription snapshot missing a non-empty customer id,
subscription id, or (for a non-delete event) status also raises, before any
delivery guard runs. Valid events are dropped if their
envelope timestamp is older than the newest subscription event already
mirrored, since Stripe does not guarantee delivery order. Stripe event
timestamps carry only one-second resolution, so two distinct events for the
same subscription can legitimately share a timestamp; when that tie lands on
a subscription the mirror already marks canceled, an equal-timestamp event
reporting any other status is refused rather than applied, because a canceled
subscription is a dead end that will never emit a later event to correct an
accidental resurrection. The same tie against a *different* subscription id
is not covered by this rule and falls through to the normal handling above,
since a genuinely new, live subscription keeps sending events that will
correct the mirror on their own regardless of which one happened to land
first. ``invoice.paid`` owns exactly one fact, the newest paid period's start,
and never touches status:
Stripe advances a subscription's period fields on a failed renewal too, so
treating that advance as proof of payment would grant credits nobody paid for.
``invoice.payment_failed`` is retained for the audit trail but writes nothing
to the mirror — the plan consequence of a failed payment arrives separately,
through the ``customer.subscription.updated`` event Stripe sends alongside
it.

One reconciler runs after every handled event and turns whatever the mirror
currently holds into consequences: it derives ``Organization.plan`` from the
mirrored status, resets an ex-customer's credits when Operator becomes Trial,
then grants the current period's credits when the resulting plan is Operator
and the recorded paid-period fact matches the subscription's current period
(see ``grants.py``). Because reconciliation reads the accumulated mirror
rather than reacting to a single event in isolation, it does not matter
whether ``invoice.paid`` or ``customer.subscription.created`` is delivered
first for the same period — whichever one completes the matching status and
paid-period fact triggers the grant, and the other event's reconciliation pass
finds nothing left to do.

This is the only module that imports Stripe. Checkout and portal callers get
plain URL strings, the webhook view gets a plain event dict, and models plus
other billing services know only HumR values. Keeping the SDK at this boundary
also makes the mirror's writer rule explicit: only the handlers below mutate a
``BillingSubscription`` row.
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

    The SDK's event wrapper is deliberately bypassed: every handler downstream
    consumes plain dicts, so the signed payload's own JSON is the event. Only
    the SDK's signature check is used. Every failure raises the same error
    type, but signature failures and payload-shape failures carry distinct
    messages so the webhook view's log tells them apart.
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
    """Persist and apply one verified Stripe event exactly once.

    The Stripe event id is the row's primary key, so a redelivered event is
    recognized by the get_or_create lookup instead of inserted again. A fresh
    row is created and its effects are dispatched inside the same
    transaction, so either both happen or neither does; an existing row means
    the event was already fully handled, and dispatch is skipped.
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
    """Checkout stamps the organization id into subscription metadata; events that carry none resolve through the mirror row instead."""
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
    """Return the customer id, subscription id, and status a snapshot must carry, raising when any is missing."""
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
    """Replace the mirror from one validated, complete subscription snapshot.

    The caller has already required customer id, subscription id, and status
    before resolving delivery-order guards. Every nullable field this event
    family owns is written verbatim, including ``None``, so a newer event can
    clear stale periods or cancellation timestamps.
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
    """Merge the sparse identity and period facts carried by an invoice.

    The dispatcher has already required the invoice's ``data.object`` to be a
    dictionary, but an invoice is not a complete subscription snapshot.
    ``None`` therefore means "this invoice supplied no fact" and leaves an
    existing field unchanged. An invoice may arrive first and create a partial
    mirror, but it must carry both ids to do so and never invents a subscription
    status.
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
    """Apply Stripe's plan derivation and return ``(previous_plan, resulting_plan)``.

    Only Trial and Operator are Stripe-managed. A Team or Enterprise plan stays
    unchanged even when the subscription status implies a different plan, so
    its previous and resulting values are equal in the returned transition.
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
    """Turn the mirror's accumulated facts into plan and credit consequences.

    Plan is derived first. An Operator-to-Trial transition restarts the
    trial, keyed on the event that triggered this reconciliation pass rather
    than on the subscription: the same subscription can make that transition
    more than once (an unpaid period can recover back to Operator before a
    later cancellation), and each transition is its own reset. Otherwise a
    grant only fires when the derivation lands on Operator and the mirrored
    paid-period fact matches the subscription's current period. That match
    stops a paid invoice for an old period, or for a subscription id the
    mirror no longer follows, from granting against the wrong period. Both
    write-off-then-grant paths are idempotent (see ``grants.py``), so calling
    this again for the same state changes nothing.
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
    """Return whether an event names a different subscription while the mirror follows a live one.

    The mirror follows exactly one subscription at a time. An event for any
    other id is stale by construction — the organization has already moved
    on to a different subscription — so applying its facts would let that
    superseded subscription overwrite the mirror a newer, still-live one
    owns. Once the mirrored subscription is gone (unset or canceled) there is
    nothing live left to protect, so an event under a new id is free to
    become the mirror's subscription.
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
    """Return whether a create event competes with the subscription already followed.

    One organization has room for one subscription in its mirror. Seeing a new
    id before the followed subscription reaches ``canceled`` signals that
    Stripe may have two live subscriptions. The event still enters the normal
    ordering path, but the anomaly must be surfaced for manual cleanup and a
    possible mirror correction.
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
    """Return whether a delayed subscription event predates the state already mirrored.

    Stripe delivery order is not state order. The newest applied envelope time
    is therefore the mirror's high-water mark: an event below it remains in the
    webhook audit trail but cannot move the subscription snapshot backward.
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
    """Return whether an equal-timestamp event would resurrect a canceled mirror.

    Stripe envelope timestamps have one-second resolution, so creation order
    cannot distinguish every pair of events. For the same subscription,
    ``canceled`` is the deterministic winner of a tie; only a strictly newer
    event may replace that terminal state.
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
    """Mirror one subscription event unless a newer subscription event already won.

    Locks the organization row before comparing timestamps, so two webhooks
    for the same subscription delivered concurrently cannot both read the
    same stale "latest event" and race each other into the wrong final
    mirror state.

    A ``customer.subscription.created`` naming a subscription id different
    from the one the mirror already follows, while that existing subscription
    is not canceled, means the organization somehow has two live Stripe
    subscriptions. That is logged as a manual-intervention error, but the new
    event still mirrors normally through the newest-state-wins path below —
    the check only surfaces the anomaly, it does not refuse or cancel either
    subscription.
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
    """Record a paid invoice's identity, periods, and paid-period fact — never a subscription status.

    An invoice naming a subscription other than the one the mirror currently
    follows is skipped entirely: accepting its facts would let a superseded
    subscription's invoice overwrite the mirror that a newer, still-live
    subscription owns. Otherwise the mirror is created or updated even before
    any subscription event has arrived, so the paid-period fact reflects
    payment regardless of whether the subscription-event side of the mirror
    exists yet.
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
    """Find the existing mirror for reconciliation without recording invoice facts.

    Payment failure matters to the lifecycle, but the invoice itself owns no
    trusted mirror field — the status change it implies arrives separately on
    the accompanying ``customer.subscription.updated`` event.
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
    """Apply one supported Stripe event; unrelated event types are silent no-ops.

    This is the one place that checks ``data.object`` is a dictionary for
    every handled type, subscription and invoice alike, ahead of the
    type-specific handler.
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
