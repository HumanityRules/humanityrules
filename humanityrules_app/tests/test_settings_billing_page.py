"""Tests for the organization billing page and its hosted-payment redirects.

The context tests pin the page's billing-period interpretation independently
from HTML. View tests then cover the app-shell split, organization-admin gate,
normal CSRF-protected forms, disabled configuration state, and the boundary
where plain Stripe URLs become 303 browser redirects.
"""

import datetime
import importlib
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services import abac_service
from humanityrules_app.services.billing import billing_page, plans, stripe_lifecycle

settings_view = importlib.import_module("humanityrules_app.views.settings")


def _set_ledger_created_at(entry: models.BillingLedgerEntry, created_at: datetime.datetime) -> None:
    """Place a ledger fixture at an exact time despite its auto-managed timestamp."""
    models.BillingLedgerEntry.objects.filter(id=entry.id).update(created_at=created_at)


def _create_ledger_entry(
    organization: models.Organization,
    entry_type: str,
    amount: int,
    idempotency_key: str,
    created_at: datetime.datetime,
) -> models.BillingLedgerEntry:
    """Create one timestamp-controlled ledger movement for period tests."""
    entry = models.BillingLedgerEntry.objects.create(
        organization=organization,
        type=entry_type,
        amount=Decimal(amount),
        idempotency_key=idempotency_key,
        usage_event=None,
        description="Billing page test movement",
        metadata={},
    )
    _set_ledger_created_at(entry=entry, created_at=created_at)
    return entry


def _create_subscription(
    organization: models.Organization,
    status: str,
    current_period_start: datetime.datetime | None,
    current_period_end: datetime.datetime | None,
    stripe_customer_id: str,
) -> models.BillingSubscription:
    """Create one local Stripe mirror without crossing the lifecycle boundary."""
    return models.BillingSubscription.objects.create(
        organization=organization,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=f"sub_{organization.slug}",
        status=status,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
    )


class TestBillingPageContext(TestCase):
    """The service turns org-owned billing rows into display-ready plain values."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(
            name="Billing Context Org",
            slug="billing-context-org",
        )
        self.now = timezone.now()

    def test_operator_subscription_period_takes_precedence_and_only_charges_burn(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan"])
        period_start = self.now - datetime.timedelta(days=10)
        period_end = self.now + datetime.timedelta(days=20)
        _create_subscription(
            organization=self.organization,
            status="active",
            current_period_start=period_start,
            current_period_end=period_end,
            stripe_customer_id="cus_context",
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=2000,
            idempotency_key="grant:context",
            created_at=self.now - datetime.timedelta(days=30),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-900,
            idempotency_key="charge:before-subscription",
            created_at=self.now - datetime.timedelta(days=20),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-120,
            idempotency_key="charge:inside-one",
            created_at=self.now - datetime.timedelta(days=5),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-30,
            idempotency_key="charge:inside-two",
            created_at=self.now - datetime.timedelta(days=2),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.ADJUSTMENT,
            amount=-500,
            idempotency_key="adjustment:inside",
            created_at=self.now - datetime.timedelta(days=1),
        )
        other_organization = models.Organization.objects.create(name="Other Org", slug="other-billing-context-org")
        _create_ledger_entry(
            organization=other_organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-700,
            idempotency_key="charge:other-org",
            created_at=self.now - datetime.timedelta(days=1),
        )

        billing = billing_page.billing_page_context(organization=self.organization)

        self.assertEqual(billing.current_period_burn, 150)
        self.assertEqual(billing.renewal_date, period_end.date())
        self.assertIsNone(billing.plan_end_date)
        self.assertTrue(billing.show_manage_billing)

    def test_pending_cancellation_exposes_an_end_date_instead_of_a_renewal(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan"])
        period_end = self.now + datetime.timedelta(days=20)
        subscription = _create_subscription(
            organization=self.organization,
            status="active",
            current_period_start=self.now,
            current_period_end=period_end,
            stripe_customer_id="cus_pending_cancellation",
        )
        subscription.cancel_at = period_end
        subscription.save(update_fields=["cancel_at", "updated_at"])

        billing = billing_page.billing_page_context(organization=self.organization)

        self.assertIsNone(billing.renewal_date)
        self.assertEqual(billing.plan_end_date, period_end.date())
        self.assertTrue(billing.show_manage_billing)

    def test_latest_grant_is_the_fallback_anchor(self) -> None:
        _create_subscription(
            organization=self.organization,
            status="canceled",
            current_period_start=self.now - datetime.timedelta(days=1),
            current_period_end=self.now + datetime.timedelta(days=30),
            stripe_customer_id="cus_canceled",
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=500,
            idempotency_key="grant:old",
            created_at=self.now - datetime.timedelta(days=30),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-80,
            idempotency_key="charge:between-grants",
            created_at=self.now - datetime.timedelta(days=20),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.GRANT,
            amount=500,
            idempotency_key="grant:latest",
            created_at=self.now - datetime.timedelta(days=10),
        )
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-60,
            idempotency_key="charge:after-latest-grant",
            created_at=self.now - datetime.timedelta(days=5),
        )

        billing = billing_page.billing_page_context(organization=self.organization)

        self.assertEqual(billing.current_period_burn, 60)
        self.assertIsNone(billing.renewal_date)

    def test_organization_creation_is_the_final_anchor(self) -> None:
        organization_created_at = self.now - datetime.timedelta(days=20)
        models.Organization.objects.filter(id=self.organization.id).update(created_at=organization_created_at)
        self.organization.refresh_from_db()
        _create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=-25,
            idempotency_key="charge:since-creation",
            created_at=self.now - datetime.timedelta(days=10),
        )

        billing = billing_page.billing_page_context(organization=self.organization)

        self.assertEqual(billing.current_period_burn, 25)

    @override_settings(STRIPE_SECRET_KEY=None, STRIPE_OPERATOR_PRICE_ID=None)
    def test_display_values_clamp_balance_and_use_the_effective_plan(self) -> None:
        self.organization.plan_overrides = {"monthly_credit_grant": 750}
        self.organization.save(update_fields=["plan_overrides"])
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal(-50))

        billing = billing_page.billing_page_context(organization=self.organization)

        self.assertEqual(billing.plan_key, "trial")
        self.assertEqual(billing.plan_name, "Trial")
        self.assertTrue(billing.show_credits)
        self.assertEqual(billing.credits_remaining, 0)
        self.assertEqual(billing.monthly_grant, 750)
        self.assertEqual(billing.trial_runtime_days, 7)
        self.assertEqual(billing.max_agents, 1)
        self.assertEqual(billing.operator_price_usd, "39")
        self.assertEqual(billing.team_agent_price_usd, "29")
        self.assertTrue(billing.show_upgrade)
        self.assertFalse(billing.show_manage_billing)
        self.assertFalse(billing.stripe_configured)

    def test_renewal_requires_an_operator_status_and_period_end(self) -> None:
        cases = (
            ("active", None),
            ("canceled", self.now + datetime.timedelta(days=30)),
            ("incomplete", self.now + datetime.timedelta(days=30)),
        )
        for index, (status, period_end) in enumerate(cases):
            with self.subTest(status=status, period_end=period_end):
                organization = models.Organization.objects.create(
                    name=f"Renewal Org {index}",
                    slug=f"renewal-org-{index}",
                )
                _create_subscription(
                    organization=organization,
                    status=status,
                    current_period_start=self.now,
                    current_period_end=period_end,
                    stripe_customer_id=f"cus_renewal_{index}",
                )

                billing = billing_page.billing_page_context(organization=organization)

                self.assertIsNone(billing.renewal_date)

    def test_action_flags_follow_the_current_plan_and_customer_mirror(self) -> None:
        _create_subscription(
            organization=self.organization,
            status="canceled",
            current_period_start=None,
            current_period_end=None,
            stripe_customer_id="",
        )

        billing = billing_page.billing_page_context(organization=self.organization)
        self.assertTrue(billing.show_upgrade)
        self.assertFalse(billing.show_manage_billing)

        models.BillingSubscription.objects.filter(organization=self.organization).update(
            stripe_customer_id="cus_now_available",
        )
        billing = billing_page.billing_page_context(organization=self.organization)
        self.assertTrue(billing.show_manage_billing)

        for plan_name in (plans.TEAM, plans.ENTERPRISE):
            with self.subTest(plan_name=plan_name):
                organization = models.Organization.objects.create(
                    name=f"{plan_name.title()} Org",
                    slug=f"{plan_name}-billing-org",
                    plan=plan_name,
                )
                billing = billing_page.billing_page_context(organization=organization)
                self.assertFalse(billing.show_upgrade)
                self.assertFalse(billing.show_manage_billing)
                self.assertFalse(billing.show_credits)

    def test_stripe_is_configured_only_when_both_required_values_are_set(self) -> None:
        cases = (
            (None, None, False),
            ("sk_test_humr", None, False),
            (None, "price_operator", False),
            ("sk_test_humr", "price_operator", True),
        )
        for secret_key, price_id, expected in cases:
            with (
                self.subTest(secret_key=secret_key, price_id=price_id),
                override_settings(STRIPE_SECRET_KEY=secret_key, STRIPE_OPERATOR_PRICE_ID=price_id),
            ):
                billing = billing_page.billing_page_context(organization=self.organization)
                self.assertEqual(billing.stripe_configured, expected)


class TestSettingsBillingViews(TestCase):
    """Only current-org administrators may render billing or leave for Stripe."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Billing View Org", slug="billing-view-org")
        self.admin = models.User.objects.create_user(
            username="billing-admin@example.com",
            email="billing-admin@example.com",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.organization, admin_user=self.admin)
        self.member = models.User.objects.create_user(
            username="billing-member@example.com",
            email="billing-member@example.com",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.member,
            role=models.OrganizationMembership.Role.MEMBER,
        )
        models.IdentityAttribute.objects.create(
            organization=self.organization,
            user=self.member,
            key="org-role",
            value="member",
        )

    def test_non_admin_is_forbidden_from_page_checkout_and_portal(self) -> None:
        self.client.force_login(user=self.member)

        page_response = self.client.get(path="/settings/billing/", HTTP_HX_REQUEST="true")
        checkout_response = self.client.post(path="/settings/billing/checkout")
        portal_response = self.client.post(path="/settings/billing/portal")

        self.assertEqual(page_response.status_code, 403)
        self.assertEqual(checkout_response.status_code, 403)
        self.assertEqual(portal_response.status_code, 403)

    def test_full_page_load_returns_the_shell_without_building_billing_context(self) -> None:
        self.client.force_login(user=self.admin)
        with patch.object(billing_page, "billing_page_context") as build_context:
            response = self.client.get(path="/settings/billing/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "humanityrules_app/app_shell.html")
        self.assertEqual(response.context["content_url"], "/settings/billing/")
        build_context.assert_not_called()

    def test_checkout_success_landing_preserves_and_logs_the_session_marker(self) -> None:
        self.client.force_login(user=self.admin)
        with (
            patch.object(billing_page, "billing_page_context") as build_context,
            patch.object(settings_view.logger, "info") as info_log,
        ):
            response = self.client.get(path="/settings/billing/?checkout_session_id=cs_completed")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "humanityrules_app/app_shell.html")
        self.assertEqual(
            response.context["content_url"],
            "/settings/billing/?checkout_session_id=cs_completed",
        )
        build_context.assert_not_called()
        info_log.assert_called_once()
        self.assertIn("cs_completed", info_log.call_args.args[0])

    def test_trial_with_checkout_marker_renders_activating_state_and_polls(self) -> None:
        self.client.force_login(user=self.admin)
        with (
            patch.object(billing_page, "billing_page_context") as build_context,
            patch.object(stripe_lifecycle.stripe, "StripeClient") as stripe_client,
        ):
            response = self.client.get(
                path="/settings/billing/?checkout_session_id=cs_completed",
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_activating"])
        self.assertContains(response, "Activating your Operator subscription")
        self.assertContains(response, "Your payment was successful")
        self.assertContains(response, 'hx-get="/settings/billing/?checkout_session_id=cs_completed"')
        self.assertContains(response, 'hx-trigger="load delay:5s"')
        self.assertContains(response, 'hx-target="#main-content"')
        self.assertNotContains(response, "Upgrade to Operator")
        build_context.assert_not_called()
        stripe_client.assert_not_called()

    def test_checkout_marker_is_ignored_after_operator_activation_and_polling_stops(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan", "updated_at"])
        self.client.force_login(user=self.admin)

        response = self.client.get(
            path="/settings/billing/?checkout_session_id=cs_completed",
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_activating"])
        self.assertContains(response, "Operator")
        self.assertNotContains(response, "Activating your Operator subscription")
        self.assertNotContains(response, 'hx-trigger="load delay:5s"')
        self.assertNotContains(response, "Upgrade to Operator")

    @override_settings(STRIPE_SECRET_KEY=None, STRIPE_OPERATOR_PRICE_ID=None)
    def test_htmx_page_renders_display_values_and_disabled_upgrade(self) -> None:
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal(425))
        self.client.force_login(user=self.admin)

        response = self.client.get(path="/settings/billing/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "humanityrules_app/settings/billing.html")
        self.assertEqual(response.context["active_tab"], "billing")
        self.assertIsInstance(response.context["billing"], billing_page.BillingPageContext)
        self.assertContains(response, "Trial")
        self.assertContains(response, "425 of 500 credits remaining")
        self.assertContains(response, "0 credits used this period")
        self.assertContains(response, "500 one-time credits")
        self.assertContains(response, "7 days of runtime")
        self.assertContains(response, "1 agent")
        self.assertNotContains(response, "Current plan")
        self.assertContains(response, "Upgrade to Operator")
        self.assertContains(response, "$39")
        self.assertContains(response, "/month")
        self.assertContains(response, "$29")
        self.assertContains(response, "/agent/month")
        self.assertContains(response, "Custom")
        self.assertContains(response, "Contact us")
        self.assertContains(response, "Payments aren't set up yet.")
        self.assertContains(response, "disabled")
        self.assertNotContains(response, "Renews on")
        self.assertNotContains(response, "Ends on")

    def test_customer_cloud_page_shows_plans_without_credit_numbers(self) -> None:
        self.organization.plan = plans.TEAM
        self.organization.save(update_fields=["plan", "updated_at"])
        self.client.force_login(user=self.admin)

        response = self.client.get(path="/settings/billing/", HTTP_HX_REQUEST="true")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current plan")
        self.assertNotContains(response, "credits remaining")
        self.assertNotContains(response, "credits used this period")
        self.assertNotContains(response, "Upgrade to Operator")

    def test_htmx_page_distinguishes_renewal_from_pending_cancellation(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan", "updated_at"])
        period_end = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC)
        subscription = _create_subscription(
            organization=self.organization,
            status="active",
            current_period_start=period_end - datetime.timedelta(days=31),
            current_period_end=period_end,
            stripe_customer_id="cus_date_line",
        )
        self.client.force_login(user=self.admin)

        renewing_response = self.client.get(path="/settings/billing/", HTTP_HX_REQUEST="true")

        self.assertContains(renewing_response, "Renews on Sep 1, 2026")
        self.assertNotContains(renewing_response, "Ends on")
        subscription.cancel_at = period_end
        subscription.save(update_fields=["cancel_at", "updated_at"])

        ending_response = self.client.get(path="/settings/billing/", HTTP_HX_REQUEST="true")

        self.assertContains(ending_response, "Ends on Sep 1, 2026")
        self.assertContains(ending_response, "You can resume anytime from Manage billing.")
        self.assertNotContains(ending_response, "Renews on")

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_checkout_redirects_with_distinct_success_and_cancel_urls(self) -> None:
        self.client.force_login(user=self.admin)
        with patch.object(
            stripe_lifecycle,
            "create_stripe_operator_checkout_url",
            return_value="https://checkout.example/session",
        ) as create_checkout:
            response = self.client.post(path="/settings/billing/checkout")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "https://checkout.example/session")
        create_checkout.assert_called_once_with(
            organization=self.organization,
            success_url=(
                "http://testserver/settings/billing/"
                "?checkout_session_id={CHECKOUT_SESSION_ID}"
            ),
            cancel_url="http://testserver/settings/billing/",
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_portal_redirects_to_the_hosted_url(self) -> None:
        period_start = timezone.now()
        _create_subscription(
            organization=self.organization,
            status="active",
            current_period_start=period_start,
            current_period_end=period_start + datetime.timedelta(days=30),
            stripe_customer_id="cus_portal",
        )
        self.client.force_login(user=self.admin)
        with patch.object(
            stripe_lifecycle,
            "create_stripe_portal_url",
            return_value="https://billing.example/portal",
        ) as create_portal:
            response = self.client.post(path="/settings/billing/portal")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "https://billing.example/portal")
        create_portal.assert_called_once_with(
            organization=self.organization,
            return_url="http://testserver/settings/billing/",
        )

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_checkout_rejects_an_organization_without_an_available_upgrade(self) -> None:
        self.organization.plan = plans.OPERATOR
        self.organization.save(update_fields=["plan"])
        self.client.force_login(user=self.admin)
        with (
            patch.object(stripe_lifecycle, "create_stripe_operator_checkout_url") as create_checkout,
            patch.object(settings_view.logger, "error") as error_log,
        ):
            response = self.client.post(path="/settings/billing/checkout")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "/settings/billing/")
        create_checkout.assert_not_called()
        error_log.assert_called_once()

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_portal_rejects_an_organization_without_available_billing_management(self) -> None:
        self.client.force_login(user=self.admin)
        with (
            patch.object(stripe_lifecycle, "create_stripe_portal_url") as create_portal,
            patch.object(settings_view.logger, "error") as error_log,
        ):
            response = self.client.post(path="/settings/billing/portal")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "/settings/billing/")
        create_portal.assert_not_called()
        error_log.assert_called_once()

    @override_settings(STRIPE_SECRET_KEY=None, STRIPE_OPERATOR_PRICE_ID=None)
    def test_unconfigured_stripe_redirects_back_without_calling_lifecycle(self) -> None:
        self.client.force_login(user=self.admin)
        with (
            patch.object(stripe_lifecycle, "create_stripe_operator_checkout_url") as create_checkout,
            patch.object(stripe_lifecycle, "create_stripe_portal_url") as create_portal,
            patch.object(settings_view.logger, "error") as error_log,
        ):
            checkout_response = self.client.post(path="/settings/billing/checkout")
            portal_response = self.client.post(path="/settings/billing/portal")

        self.assertEqual(checkout_response.status_code, 303)
        self.assertEqual(checkout_response["Location"], "/settings/billing/")
        self.assertEqual(portal_response.status_code, 303)
        self.assertEqual(portal_response["Location"], "/settings/billing/")
        create_checkout.assert_not_called()
        create_portal.assert_not_called()
        self.assertEqual(error_log.call_count, 2)

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_payment_views_require_post(self) -> None:
        self.client.force_login(user=self.admin)

        checkout_response = self.client.get(path="/settings/billing/checkout")
        portal_response = self.client.get(path="/settings/billing/portal")

        self.assertEqual(checkout_response.status_code, 405)
        self.assertEqual(portal_response.status_code, 405)

    @override_settings(STRIPE_SECRET_KEY="sk_test_humr", STRIPE_OPERATOR_PRICE_ID="price_operator")
    def test_payment_views_require_csrf(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user=self.admin)
        with (
            patch.object(stripe_lifecycle, "create_stripe_operator_checkout_url") as create_checkout,
            patch.object(stripe_lifecycle, "create_stripe_portal_url") as create_portal,
        ):
            checkout_response = csrf_client.post(path="/settings/billing/checkout")
            portal_response = csrf_client.post(path="/settings/billing/portal")

        self.assertEqual(checkout_response.status_code, 403)
        self.assertEqual(portal_response.status_code, 403)
        create_checkout.assert_not_called()
        create_portal.assert_not_called()
