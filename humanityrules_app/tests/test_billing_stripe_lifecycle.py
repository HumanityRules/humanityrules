"""Tests for Stripe's boundary, subscription mirror, plan transitions, and renewals.

No test reaches Stripe. Session creation is observed at the SDK boundary, and
webhook events are plain dictionaries matching the API version the pinned SDK
models: subscription periods live on the single item, while invoice identity
and metadata live under ``parent.subscription_details``. The database tests
exercise event reordering, retries, hand-managed plans, and the locked
expiry-plus-grant behavior that keeps the ledger and balance aligned.
"""

import datetime
import hashlib
import hmac
import importlib
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from humanityrules_app import models
from humanityrules_app.services.billing import entitlements, plans, stripe_lifecycle

stripe_webhook_view = importlib.import_module("humanityrules_app.views.stripe_webhook")

PERIOD_START = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
PERIOD_END = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)


def _stripe_event(event_type: str, stripe_object: dict) -> dict:
    """Build the plain event envelope passed to the lifecycle dispatcher."""
    return {"type": event_type, "data": {"object": stripe_object}}


def _subscription_object(
    organization: models.Organization,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    status: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
) -> dict:
    """Build the one-item Operator subscription shape emitted by Stripe 15.3.1."""
    return {
        "id": stripe_subscription_id,
        "customer": stripe_customer_id,
        "status": status,
        "metadata": {"organization_id": str(organization.id)},
        "items": {
            "data": [{
                "current_period_start": int(period_start.timestamp()),
                "current_period_end": int(period_end.timestamp()),
            }],
        },
    }


def _invoice_object(
    organization_id: str,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    period_start: datetime.datetime,
    period_end: datetime.datetime,
    subscription_status: str | None,
) -> dict:
    """Build an invoice whose usage period precedes the paid subscription-line period."""
    invoice = {
        "id": f"in_{stripe_subscription_id}",
        "customer": stripe_customer_id,
        "parent": {
            "type": "subscription_details",
            "subscription_details": {
                "subscription": stripe_subscription_id,
                "metadata": {"organization_id": organization_id},
            },
        },
        "lines": {
            "data": [{
                "parent": {"type": "subscription_item_details"},
                "period": {
                    "start": int(period_start.timestamp()),
                    "end": int(period_end.timestamp()),
                },
            }],
        },
        "period_start": int((period_start - datetime.timedelta(days=31)).timestamp()),
        "period_end": int((period_end - datetime.timedelta(days=31)).timestamp()),
    }
    if subscription_status is not None:
        invoice["parent"]["subscription_details"]["subscription"] = {
            "id": stripe_subscription_id,
            "status": subscription_status,
        }
    return invoice


def _create_mirror(
    organization: models.Organization,
    stripe_subscription_id: str,
    status: str,
    period_start: datetime.datetime | None,
    period_end: datetime.datetime | None,
) -> models.BillingSubscription:
    """Create an existing Stripe mirror for transition and fallback tests."""
    return models.BillingSubscription.objects.create(
        organization=organization,
        stripe_customer_id=f"cus_{stripe_subscription_id}",
        stripe_subscription_id=stripe_subscription_id,
        status=status,
        current_period_start=period_start,
        current_period_end=period_end,
    )


def _seed_balance(organization: models.Organization, credits: Decimal) -> None:
    """Seed a matching ledger movement and balance projection for renewal tests."""
    entry_type = models.BillingLedgerEntry.Type.ADJUSTMENT if credits >= 0 else models.BillingLedgerEntry.Type.CHARGE
    models.BillingLedgerEntry.objects.create(
        organization=organization,
        type=entry_type,
        amount=credits,
        idempotency_key=f"seed:{organization.id}",
        usage_event=None,
        description="Test opening balance",
        metadata={},
    )
    models.BillingBalance.objects.create(organization=organization, credits=credits)


class TestStripeHostedSessions(TestCase):
    """Checkout and portal expose plain URLs while all Stripe objects stay inside the lifecycle module."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Session Org", slug="session-org")

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_checkout_stamps_both_metadata_locations_and_reuses_the_customer(self) -> None:
        _create_mirror(
            organization=self.organization,
            stripe_subscription_id="sub_existing",
            status="canceled",
            period_start=None,
            period_end=None,
        )
        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.return_value = SimpleNamespace(url="https://checkout.example/session")

        with patch.object(stripe_lifecycle.stripe, "StripeClient", return_value=stripe_client) as client_constructor:
            checkout_url = stripe_lifecycle.create_operator_checkout_url(
                organization=self.organization,
                success_url="https://humr.example/success",
                cancel_url="https://humr.example/cancel",
            )

        self.assertEqual(checkout_url, "https://checkout.example/session")
        client_constructor.assert_called_once_with(api_key="sk_test_humr")
        stripe_client.v1.checkout.sessions.create.assert_called_once_with(
            params={
                "mode": "subscription",
                "line_items": [{"price": "price_operator", "quantity": 1}],
                "success_url": "https://humr.example/success",
                "cancel_url": "https://humr.example/cancel",
                "metadata": {"organization_id": str(self.organization.id)},
                "subscription_data": {"metadata": {"organization_id": str(self.organization.id)}},
                "customer": "cus_sub_existing",
            },
            options=None,
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_checkout_without_a_mirror_lets_stripe_create_the_customer(self) -> None:
        stripe_client = MagicMock()
        stripe_client.v1.checkout.sessions.create.return_value = SimpleNamespace(url="https://checkout.example/new")

        with patch.object(stripe_lifecycle.stripe, "StripeClient", return_value=stripe_client):
            stripe_lifecycle.create_operator_checkout_url(
                organization=self.organization,
                success_url="https://humr.example/success",
                cancel_url="https://humr.example/cancel",
            )

        params = stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
        self.assertNotIn("customer", params)

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr")
    def test_portal_uses_the_mirrored_customer_and_returns_a_plain_url(self) -> None:
        _create_mirror(
            organization=self.organization,
            stripe_subscription_id="sub_portal",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        stripe_client = MagicMock()
        stripe_client.v1.billing_portal.sessions.create.return_value = SimpleNamespace(
            url="https://billing.example/portal",
        )

        with patch.object(stripe_lifecycle.stripe, "StripeClient", return_value=stripe_client):
            portal_url = stripe_lifecycle.create_portal_url(
                organization=self.organization,
                return_url="https://humr.example/billing",
            )

        self.assertEqual(portal_url, "https://billing.example/portal")
        stripe_client.v1.billing_portal.sessions.create.assert_called_once_with(
            params={"customer": "cus_sub_portal", "return_url": "https://humr.example/billing"},
            options=None,
        )

    def test_portal_refuses_an_organization_without_a_stripe_customer(self) -> None:
        with self.assertRaisesMessage(ValueError, "has no Stripe customer"):
            stripe_lifecycle.create_portal_url(
                organization=self.organization,
                return_url="https://humr.example/billing",
            )


def _signature_header(payload: bytes, secret: str) -> str:
    """Sign a payload exactly as Stripe does for webhook delivery."""
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


class TestWebhookVerificationBoundary(TestCase):
    """The signed payload's own JSON is the event; only Stripe's signature check gates it."""

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_verified_event_becomes_a_plain_dict(self) -> None:
        payload = b'{"type": "customer.subscription.updated", "data": {"object": {"id": "sub_one"}}}'

        event = stripe_lifecycle.verify_and_parse_webhook(
            payload=payload,
            signature_header=_signature_header(payload=payload, secret="whsec_humr"),
        )

        self.assertEqual(event, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_one"}}})

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_bad_signature_becomes_the_service_error(self) -> None:
        with self.assertRaises(stripe_lifecycle.WebhookVerificationError) as raised:
            stripe_lifecycle.verify_and_parse_webhook(payload=b"{}", signature_header="t=1,v1=deadbeef")

        self.assertEqual(str(raised.exception), "invalid Stripe webhook signature")
        self.assertIsNotNone(raised.exception.__cause__)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_signed_invalid_json_is_reported_as_a_payload_failure_not_a_signature_failure(self) -> None:
        payload = b"not json"

        with self.assertRaises(stripe_lifecycle.WebhookVerificationError) as raised:
            stripe_lifecycle.verify_and_parse_webhook(
                payload=payload,
                signature_header=_signature_header(payload=payload, secret="whsec_humr"),
            )

        self.assertEqual(str(raised.exception), "signed Stripe webhook payload is not valid JSON")
        self.assertIsInstance(raised.exception.__cause__, ValueError)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_non_object_event_becomes_the_service_error(self) -> None:
        payload = b"[1, 2, 3]"

        with self.assertRaises(stripe_lifecycle.WebhookVerificationError):
            stripe_lifecycle.verify_and_parse_webhook(
                payload=payload,
                signature_header=_signature_header(payload=payload, secret="whsec_humr"),
            )


class TestSubscriptionWebhookMirroring(TestCase):
    """Created, updated, and deleted events upsert the mirror and then transition the plan."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Mirror Org", slug="mirror-org")

    def test_created_event_creates_the_full_mirror_and_activates_operator(self) -> None:
        subscription_object = _subscription_object(
            organization=self.organization,
            stripe_subscription_id="sub_created",
            stripe_customer_id="cus_created",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="customer.subscription.created", stripe_object=subscription_object),
        )

        subscription = models.BillingSubscription.objects.get(organization=self.organization)
        self.organization.refresh_from_db()
        self.assertEqual(subscription.stripe_customer_id, "cus_created")
        self.assertEqual(subscription.stripe_subscription_id, "sub_created")
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.current_period_start, PERIOD_START)
        self.assertEqual(subscription.current_period_end, PERIOD_END)
        self.assertEqual(self.organization.plan, plans.OPERATOR)

    def test_updated_event_falls_back_to_subscription_id_and_updates_the_existing_row(self) -> None:
        existing = _create_mirror(
            organization=self.organization,
            stripe_subscription_id="sub_updated",
            status="incomplete",
            period_start=None,
            period_end=None,
        )
        next_period_start = PERIOD_END
        next_period_end = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
        subscription_object = _subscription_object(
            organization=self.organization,
            stripe_subscription_id="sub_updated",
            stripe_customer_id="cus_updated",
            status="past_due",
            period_start=next_period_start,
            period_end=next_period_end,
        )
        subscription_object["metadata"] = {}

        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="customer.subscription.updated", stripe_object=subscription_object),
        )

        subscription = models.BillingSubscription.objects.get(organization=self.organization)
        self.organization.refresh_from_db()
        self.assertEqual(subscription.id, existing.id)
        self.assertEqual(subscription.stripe_customer_id, "cus_updated")
        self.assertEqual(subscription.status, "past_due")
        self.assertEqual(subscription.current_period_start, next_period_start)
        self.assertEqual(subscription.current_period_end, next_period_end)
        self.assertEqual(self.organization.plan, plans.OPERATOR)

    def test_deleted_event_forces_canceled_and_downgrades_to_trial(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan", "updated_at"])
        _create_mirror(
            organization=self.organization,
            stripe_subscription_id="sub_deleted",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        subscription_object = _subscription_object(
            organization=self.organization,
            stripe_subscription_id="sub_deleted",
            stripe_customer_id="cus_deleted",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="customer.subscription.deleted", stripe_object=subscription_object),
        )

        subscription = models.BillingSubscription.objects.get(organization=self.organization)
        self.organization.refresh_from_db()
        self.assertEqual(subscription.status, "canceled")
        self.assertEqual(self.organization.plan, plans.TRIAL)

    def test_an_unresolvable_event_logs_and_returns_without_writing(self) -> None:
        subscription_object = _subscription_object(
            organization=self.organization,
            stripe_subscription_id="sub_missing_org",
            stripe_customer_id="cus_missing_org",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        subscription_object["metadata"] = {"organization_id": "not-a-uuid"}

        with patch.object(stripe_lifecycle.logger, "error") as error_log:
            stripe_lifecycle.apply_webhook_event(
                event=_stripe_event(event_type="customer.subscription.created", stripe_object=subscription_object),
            )

        self.assertTrue(error_log.called)
        self.assertFalse(models.BillingSubscription.objects.exists())

    def test_unhandled_event_is_a_silent_no_op(self) -> None:
        with patch.object(stripe_lifecycle.logger, "error") as error_log:
            stripe_lifecycle.apply_webhook_event(
                event=_stripe_event(event_type="checkout.session.completed", stripe_object={"id": "cs_one"}),
            )

        error_log.assert_not_called()
        self.assertFalse(models.BillingSubscription.objects.exists())


class TestSubscriptionPlanTransition(TestCase):
    """One function owns the complete Stripe-status-to-plan matrix and its hand-managed guard."""

    def test_operator_statuses_activate_operator(self) -> None:
        for index, status in enumerate(models.BillingSubscription.OPERATOR_STATUSES):
            with self.subTest(status=status):
                organization = models.Organization.objects.create(
                    name=f"Operator {status}",
                    slug=f"operator-status-{index}",
                )
                subscription = _create_mirror(
                    organization=organization,
                    stripe_subscription_id=f"sub_operator_{index}",
                    status=status,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

                stripe_lifecycle.transition_organization_plan(subscription=subscription)

                organization.refresh_from_db()
                self.assertEqual(organization.plan, plans.OPERATOR)

    def test_every_other_stripe_status_downgrades_to_trial(self) -> None:
        statuses = ("canceled", "unpaid", "incomplete", "incomplete_expired", "paused")
        for index, status in enumerate(statuses):
            with self.subTest(status=status):
                organization = models.Organization.objects.create(
                    name=f"Trial {status}",
                    slug=f"trial-status-{index}",
                    plan=plans.OPERATOR,
                )
                subscription = _create_mirror(
                    organization=organization,
                    stripe_subscription_id=f"sub_trial_{index}",
                    status=status,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

                stripe_lifecycle.transition_organization_plan(subscription=subscription)

                organization.refresh_from_db()
                self.assertEqual(organization.plan, plans.TRIAL)

    def test_team_and_enterprise_are_logged_and_never_changed(self) -> None:
        for index, plan in enumerate((plans.TEAM, plans.ENTERPRISE)):
            with self.subTest(plan=plan):
                organization = models.Organization.objects.create(
                    name=f"Hand Managed {plan}",
                    slug=f"hand-managed-{index}",
                    plan=plan,
                )
                subscription = _create_mirror(
                    organization=organization,
                    stripe_subscription_id=f"sub_hand_{index}",
                    status="active",
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

                with patch.object(stripe_lifecycle.logger, "error") as error_log:
                    stripe_lifecycle.transition_organization_plan(subscription=subscription)

                organization.refresh_from_db()
                self.assertEqual(organization.plan, plan)
                error_log.assert_called_once()

    def test_equal_derived_plan_is_a_save_and_log_no_op(self) -> None:
        organization = models.Organization.objects.create(
            name="Already Operator",
            slug="already-operator",
            plan=plans.OPERATOR,
        )
        subscription = _create_mirror(
            organization=organization,
            stripe_subscription_id="sub_already_operator",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        with (
            patch.object(organization, "save") as save,
            patch.object(stripe_lifecycle.logger, "error") as error_log,
        ):
            stripe_lifecycle.transition_organization_plan(subscription=subscription)

        save.assert_not_called()
        error_log.assert_not_called()


class TestPaidInvoiceRenewal(TestCase):
    """A paid invoice transitions first, then resets the balance to the period's grant."""

    def test_out_of_order_paid_invoice_creates_the_mirror_then_expires_and_grants(self) -> None:
        organization = models.Organization.objects.create(
            name="Out of Order",
            slug="out-of-order",
            plan_overrides={"monthly_credit_grant": 2500},
        )
        _seed_balance(organization=organization, credits=Decimal(125))
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_out_of_order",
            stripe_customer_id="cus_out_of_order",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status=None,
        )

        stripe_lifecycle.apply_webhook_event(event=_stripe_event(event_type="invoice.paid", stripe_object=invoice))

        organization.refresh_from_db()
        subscription = models.BillingSubscription.objects.get(organization=organization)
        balance = models.BillingBalance.objects.get(organization=organization)
        renewal_entries = list(
            models.BillingLedgerEntry.objects
            .filter(organization=organization, idempotency_key__contains="sub_out_of_order")
            .order_by("created_at")
        )
        self.assertEqual(organization.plan, plans.OPERATOR)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.current_period_start, PERIOD_START)
        self.assertEqual(subscription.current_period_end, PERIOD_END)
        self.assertEqual(balance.credits, Decimal(2500))
        self.assertEqual([entry.type for entry in renewal_entries], ["expiry", "grant"])
        self.assertEqual([entry.amount for entry in renewal_entries], [Decimal(-125), Decimal(2500)])
        self.assertEqual(renewal_entries[0].idempotency_key, "expiry:sub_out_of_order:2026-08-01")
        self.assertEqual(
            renewal_entries[0].metadata,
            {"subscription_id": "sub_out_of_order", "period_start": "2026-08-01"},
        )
        self.assertEqual(
            renewal_entries[1].metadata,
            {"plan": plans.OPERATOR, "plan_version": plans.PLAN_VERSION},
        )

    def test_negative_balance_is_written_off_before_the_new_grant(self) -> None:
        organization = models.Organization.objects.create(name="Debt Org", slug="debt-org")
        _seed_balance(organization=organization, credits=Decimal(-50))
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_debt",
            stripe_customer_id="cus_debt",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status=None,
        )

        stripe_lifecycle.apply_webhook_event(event=_stripe_event(event_type="invoice.paid", stripe_object=invoice))

        balance = models.BillingBalance.objects.get(organization=organization)
        renewal_entries = list(
            models.BillingLedgerEntry.objects
            .filter(organization=organization, idempotency_key__contains="sub_debt")
            .order_by("created_at")
        )
        self.assertEqual(balance.credits, Decimal(2000))
        self.assertEqual(
            [entry.type for entry in renewal_entries],
            [models.BillingLedgerEntry.Type.EXPIRY, models.BillingLedgerEntry.Type.GRANT],
        )
        self.assertEqual([entry.amount for entry in renewal_entries], [Decimal(50), Decimal(2000)])
        self.assertEqual(renewal_entries[0].description, "Previous period balance written off at renewal")

    def test_replaying_paid_invoice_makes_the_entire_expiry_grant_pair_a_no_op(self) -> None:
        organization = models.Organization.objects.create(name="Replay Org", slug="replay-org")
        _seed_balance(organization=organization, credits=Decimal(80))
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_replay",
            stripe_customer_id="cus_replay",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status=None,
        )
        event = _stripe_event(event_type="invoice.paid", stripe_object=invoice)

        stripe_lifecycle.apply_webhook_event(event=event)
        stripe_lifecycle.apply_webhook_event(event=event)

        renewal_entries = models.BillingLedgerEntry.objects.filter(
            organization=organization,
            idempotency_key__contains="sub_replay",
        )
        self.assertEqual(renewal_entries.filter(type=models.BillingLedgerEntry.Type.EXPIRY).count(), 1)
        self.assertEqual(renewal_entries.filter(type=models.BillingLedgerEntry.Type.GRANT).count(), 1)
        self.assertEqual(models.BillingBalance.objects.get(organization=organization).credits, Decimal(2000))

    def test_late_paid_invoice_preserves_canceled_status_and_trial_plan(self) -> None:
        organization = models.Organization.objects.create(
            name="Canceled Org",
            slug="canceled-org",
            plan=plans.OPERATOR,
        )
        _create_mirror(
            organization=organization,
            stripe_subscription_id="sub_canceled",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        subscription_object = _subscription_object(
            organization=organization,
            stripe_subscription_id="sub_canceled",
            stripe_customer_id="cus_sub_canceled",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="customer.subscription.deleted", stripe_object=subscription_object),
        )
        next_period_end = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_canceled",
            stripe_customer_id="cus_sub_canceled",
            period_start=PERIOD_END,
            period_end=next_period_end,
            subscription_status=None,
        )

        stripe_lifecycle.apply_webhook_event(event=_stripe_event(event_type="invoice.paid", stripe_object=invoice))

        subscription = models.BillingSubscription.objects.get(organization=organization)
        organization.refresh_from_db()
        self.assertEqual(subscription.status, "canceled")
        self.assertEqual(subscription.current_period_start, PERIOD_END)
        self.assertEqual(subscription.current_period_end, next_period_end)
        self.assertEqual(organization.plan, plans.TRIAL)

    def test_paid_invoice_without_a_line_item_period_logs_and_skips_the_grant(self) -> None:
        organization = models.Organization.objects.create(name="Missing Period", slug="missing-period")
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_missing_period",
            stripe_customer_id="cus_missing_period",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status=None,
        )
        invoice["lines"] = {"data": [{"parent": {"type": "subscription_item_details"}}]}

        with patch.object(stripe_lifecycle.logger, "error") as error_log:
            stripe_lifecycle.apply_webhook_event(
                event=_stripe_event(event_type="invoice.paid", stripe_object=invoice),
            )

        error_log.assert_called_once()
        subscription = models.BillingSubscription.objects.get(organization=organization)
        self.assertEqual(subscription.status, "active")
        self.assertIsNone(subscription.current_period_start)
        self.assertIsNone(subscription.current_period_end)
        self.assertFalse(models.BillingLedgerEntry.objects.filter(organization=organization).exists())


class TestFailedInvoice(TestCase):
    """Payment failures may refresh the mirror but never transition the plan during smart retries."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(
            name="Retry Org",
            slug="retry-org",
            plan=plans.OPERATOR,
        )
        self.subscription = _create_mirror(
            organization=self.organization,
            stripe_subscription_id="sub_retry",
            status="active",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    def test_expanded_subscription_status_can_create_an_out_of_order_mirror(self) -> None:
        organization = models.Organization.objects.create(name="Early Failure", slug="early-failure")
        invoice = _invoice_object(
            organization_id=str(organization.id),
            stripe_subscription_id="sub_early_failure",
            stripe_customer_id="cus_early_failure",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status="past_due",
        )

        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="invoice.payment_failed", stripe_object=invoice),
        )

        subscription = models.BillingSubscription.objects.get(organization=organization)
        organization.refresh_from_db()
        self.assertEqual(subscription.status, "past_due")
        self.assertEqual(organization.plan, plans.TRIAL)

    def test_carried_subscription_status_updates_the_mirror_without_changing_plan(self) -> None:
        invoice = _invoice_object(
            organization_id=str(self.organization.id),
            stripe_subscription_id="sub_retry",
            stripe_customer_id="cus_retry",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status="unpaid",
        )

        stripe_lifecycle.apply_webhook_event(
            event=_stripe_event(event_type="invoice.payment_failed", stripe_object=invoice),
        )

        self.subscription.refresh_from_db()
        self.organization.refresh_from_db()
        self.assertEqual(self.subscription.status, "unpaid")
        self.assertEqual(self.organization.plan, plans.OPERATOR)

    def test_missing_subscription_status_logs_and_leaves_the_mirror_unchanged(self) -> None:
        invoice = _invoice_object(
            organization_id=str(self.organization.id),
            stripe_subscription_id="sub_retry",
            stripe_customer_id="cus_retry",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
            subscription_status=None,
        )

        with patch.object(stripe_lifecycle.logger, "error") as error_log:
            stripe_lifecycle.apply_webhook_event(
                event=_stripe_event(event_type="invoice.payment_failed", stripe_object=invoice),
            )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        error_log.assert_called_once()


class TestStripeWebhookView(TestCase):
    """The HTTP boundary reports configuration and signature failures without importing Stripe."""

    def setUp(self) -> None:
        self.client = Client()

    @override_settings(STRIPE_WEBHOOK_SECRET=None)
    def test_unset_webhook_secret_returns_503_and_logs(self) -> None:
        with (
            patch.object(stripe_lifecycle, "verify_and_parse_webhook") as verify,
            patch.object(stripe_webhook_view.logger, "error") as error_log,
        ):
            response = self.client.post(
                "/api/stripe/webhook",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="signature",
            )

        self.assertEqual(response.status_code, 503)
        verify.assert_not_called()
        error_log.assert_called_once()

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_invalid_signature_returns_400_and_logs_the_underlying_cause(self) -> None:
        verification_error = stripe_lifecycle.WebhookVerificationError("invalid Stripe webhook signature")
        verification_error.__cause__ = ValueError("timestamp outside tolerance")
        with (
            patch.object(stripe_lifecycle, "verify_and_parse_webhook", side_effect=verification_error),
            patch.object(stripe_lifecycle, "apply_webhook_event") as apply_event,
            patch.object(stripe_webhook_view.logger, "error") as error_log,
        ):
            response = self.client.post(
                "/api/stripe/webhook",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="bad",
            )

        self.assertEqual(response.status_code, 400)
        apply_event.assert_not_called()
        error_log.assert_called_once_with(
            "Stripe webhook rejected: invalid Stripe webhook signature (ValueError: timestamp outside tolerance)"
        )

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_valid_event_is_applied_and_acknowledged(self) -> None:
        event = {"type": "customer.subscription.updated", "data": {"object": {}}}
        with (
            patch.object(stripe_lifecycle, "verify_and_parse_webhook", return_value=event) as verify,
            patch.object(stripe_lifecycle, "apply_webhook_event") as apply_event,
        ):
            response = self.client.post(
                "/api/stripe/webhook",
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="valid",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        verify.assert_called_once_with(payload=b"{}", signature_header="valid")
        apply_event.assert_called_once_with(event=event)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_humr")
    def test_non_post_method_is_rejected(self) -> None:
        response = self.client.get("/api/stripe/webhook")

        self.assertEqual(response.status_code, 405)


class TestSubscriptionRenewalEntitlement(TestCase):
    """Only Operator-deriving subscription states expose the mirror's period end date."""

    def test_operator_deriving_statuses_expose_the_iso_renewal_date(self) -> None:
        for index, status in enumerate(models.BillingSubscription.OPERATOR_STATUSES):
            with self.subTest(status=status):
                organization = models.Organization.objects.create(
                    name=f"Renewal {status}",
                    slug=f"renewal-{index}",
                )
                _create_mirror(
                    organization=organization,
                    stripe_subscription_id=f"sub_renewal_{index}",
                    status=status,
                    period_start=PERIOD_START,
                    period_end=PERIOD_END,
                )

                snapshot = entitlements.entitlement_snapshot(organization=organization)

                self.assertEqual(snapshot.renewal_date, "2026-09-01")

    def test_terminal_status_and_unknown_period_have_no_renewal_date(self) -> None:
        terminal = models.Organization.objects.create(name="Terminal", slug="terminal-renewal")
        unknown_period = models.Organization.objects.create(name="Unknown Period", slug="unknown-renewal")
        _create_mirror(
            organization=terminal,
            stripe_subscription_id="sub_terminal",
            status="canceled",
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        _create_mirror(
            organization=unknown_period,
            stripe_subscription_id="sub_unknown_period",
            status="active",
            period_start=None,
            period_end=None,
        )

        self.assertIsNone(entitlements.entitlement_snapshot(organization=terminal).renewal_date)
        self.assertIsNone(entitlements.entitlement_snapshot(organization=unknown_period).renewal_date)
