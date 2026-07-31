"""Tests for the staff-only, cross-organization platform billing page."""

import datetime
import uuid
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services import billing_admin_service
from humanityrules_app.tests.app_test_factories import make_source_template


class TestPlatformBilling(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Billing Org", slug="billing-org")
        self.empty_organization = models.Organization.objects.create(name="Empty Org", slug="empty-org")
        self.staff = models.User.objects.create_user(
            username="billing-staff",
            password="pw",
            current_organization=self.organization,
            is_staff=True,
        )
        self.nonstaff = models.User.objects.create_user(
            username="billing-user",
            password="pw",
            current_organization=self.organization,
        )
        aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Billing AWS")
        environment = models.Environment.objects.create(
            aws_account=aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
        )
        workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        source_template = make_source_template()
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            environment=environment,
            source_template=source_template,
            name="Billing Agent",
            slug="billing-agent",
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            created_by=self.staff,
        )
        self.silent_app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            environment=environment,
            source_template=source_template,
            name="Silent Agent",
            slug="silent-agent",
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            created_by=self.staff,
        )

    def _create_event(
        self,
        organization: models.Organization,
        app_id: uuid.UUID,
        app_slug: str,
        occurred_at: datetime.datetime,
        idempotency_key: str,
        rated_at: datetime.datetime | None,
    ) -> models.BillingUsageEvent:
        return models.BillingUsageEvent.objects.create(
            organization=organization,
            app_id=app_id,
            app_slug=app_slug,
            owner_username="agent-owner",
            source="llm",
            subkey="gpt-5.2-codex",
            quantities={
                "input_tokens": 200,
                "output_tokens": 350,
                "cache_read_tokens": 1000,
                "cache_write_tokens": 0,
                "reasoning_tokens": 80,
            },
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            rated_at=rated_at,
        )

    def _create_ledger_entry(
        self,
        organization: models.Organization,
        entry_type: str,
        amount: Decimal,
        idempotency_key: str,
        app_id: uuid.UUID | None,
        app_slug: str,
    ) -> models.BillingLedgerEntry:
        metadata = {}
        if entry_type == models.BillingLedgerEntry.Type.CHARGE:
            metadata = {
                "app_id": str(app_id),
                "app_slug": app_slug,
                "event_count": 1,
                "exact_credits": str(amount),
                "rate_card_versions": ["v1"],
                "usage": {},
            }
        return models.BillingLedgerEntry.objects.create(
            organization=organization,
            type=entry_type,
            amount=amount,
            idempotency_key=idempotency_key,
            description=f"Test {entry_type}",
            metadata=metadata,
        )

    def test_page_summarizes_usage_and_includes_silent_and_empty_tenants(self) -> None:
        now = timezone.now()
        self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=now - datetime.timedelta(hours=1),
            idempotency_key="recent-unrated",
            rated_at=None,
        )
        self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=now - datetime.timedelta(days=45),
            idempotency_key="old-rated",
            rated_at=now - datetime.timedelta(days=44),
        )
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/")

        self.assertEqual(response.status_code, 200)
        snapshot = response.context["snapshot"]
        self.assertEqual(response.context["billing_window"].key, "30d")
        self.assertEqual(snapshot.summary.event_count, 1)
        self.assertEqual(snapshot.summary.reporting_organization_count, 1)
        self.assertEqual(snapshot.summary.pending_rating_count, 1)
        billing_group = next(group for group in snapshot.organization_groups if group.organization == self.organization)
        rows_by_slug = {row.app_slug: row for row in billing_group.apps}
        self.assertEqual(rows_by_slug[self.app.slug].event_count, 1)
        self.assertEqual(rows_by_slug[self.silent_app.slug].event_count, 0)
        self.assertContains(response, "Empty Org")
        self.assertContains(response, 'data-billing-row="organization-empty"')

    def test_all_window_retains_removed_app_history(self) -> None:
        removed_app_id = uuid.uuid7()
        self._create_event(
            organization=self.organization,
            app_id=removed_app_id,
            app_slug="removed-agent",
            occurred_at=timezone.now() - datetime.timedelta(days=90),
            idempotency_key="removed-event",
            rated_at=None,
        )
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/?window=all")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "removed-agent")
        self.assertContains(response, "Removed HA")
        self.assertContains(response, 'data-billing-row="removed-app"')

    def test_page_surfaces_accounting_aged_pending_and_integrity(self) -> None:
        now = timezone.now()
        event = self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=now - datetime.timedelta(hours=1),
            idempotency_key="aged-unrated",
            rated_at=None,
        )
        models.BillingUsageEvent.objects.filter(id=event.id).update(
            created_at=now - datetime.timedelta(minutes=10),
        )
        posting_date = now.astimezone(datetime.UTC).date()
        self._create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=Decimal("-12"),
            idempotency_key=f"charge:{self.organization.id}:{self.app.id}:{posting_date.isoformat()}",
            app_id=self.app.id,
            app_slug=self.app.slug,
        )
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal("-12"))
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/")

        snapshot = response.context["snapshot"]
        self.assertEqual(snapshot.summary.credits_charged, Decimal("12"))
        self.assertEqual(snapshot.summary.negative_balance_organization_count, 1)
        self.assertEqual(snapshot.summary.aged_pending_rating_count, 1)
        self.assertEqual(snapshot.summary.integrity_issue_organization_count, 0)
        billing_group = next(group for group in snapshot.organization_groups if group.organization == self.organization)
        rows_by_slug = {row.app_slug: row for row in billing_group.apps}
        self.assertEqual(billing_group.balance_credits, Decimal("-12"))
        self.assertEqual(billing_group.credits_charged, Decimal("12"))
        self.assertEqual(billing_group.aged_pending_rating_count, 1)
        self.assertFalse(billing_group.has_integrity_issue)
        self.assertEqual(rows_by_slug[self.app.slug].credits_charged, Decimal("12"))
        self.assertContains(response, "Negative shadow balances")
        self.assertContains(response, "Ledger healthy")
        self.assertContains(response, "1 aged")

    def test_rating_backlog_is_all_time_even_when_usage_window_excludes_event(self) -> None:
        now = timezone.now()
        event = self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=now - datetime.timedelta(days=45),
            idempotency_key="stranded-unrated",
            rated_at=None,
        )
        models.BillingUsageEvent.objects.filter(id=event.id).update(
            created_at=now - datetime.timedelta(days=44),
        )
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/?window=30d")

        snapshot = response.context["snapshot"]
        billing_group = next(group for group in snapshot.organization_groups if group.organization == self.organization)
        rows_by_slug = {row.app_slug: row for row in billing_group.apps}
        self.assertEqual(snapshot.summary.event_count, 0)
        self.assertEqual(snapshot.summary.pending_rating_count, 1)
        self.assertEqual(snapshot.summary.aged_pending_rating_count, 1)
        self.assertEqual(billing_group.event_count, 0)
        self.assertEqual(billing_group.aged_pending_rating_count, 1)
        self.assertEqual(rows_by_slug[self.app.slug].event_count, 0)
        self.assertEqual(rows_by_slug[self.app.slug].aged_pending_rating_count, 1)

    def test_page_flags_balance_drift(self) -> None:
        now = timezone.now()
        posting_date = now.astimezone(datetime.UTC).date()
        self._create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=Decimal("-12"),
            idempotency_key=f"charge:{self.organization.id}:{self.app.id}:{posting_date.isoformat()}",
            app_id=self.app.id,
            app_slug=self.app.slug,
        )
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal("-11"))
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/")

        snapshot = response.context["snapshot"]
        billing_group = next(group for group in snapshot.organization_groups if group.organization == self.organization)
        self.assertEqual(snapshot.summary.integrity_issue_organization_count, 1)
        self.assertTrue(billing_group.has_integrity_issue)
        self.assertEqual(billing_group.integrity_issues, ("Balance differs from the ledger by 1 credit",))
        self.assertContains(response, "Integrity issue")

    def test_page_flags_closed_day_charge_mutation(self) -> None:
        posting_date = timezone.now().astimezone(datetime.UTC).date() - datetime.timedelta(days=2)
        self._create_ledger_entry(
            organization=self.organization,
            entry_type=models.BillingLedgerEntry.Type.CHARGE,
            amount=Decimal("-4"),
            idempotency_key=f"charge:{self.organization.id}:{self.app.id}:{posting_date.isoformat()}",
            app_id=self.app.id,
            app_slug=self.app.slug,
        )
        models.BillingBalance.objects.create(organization=self.organization, credits=Decimal("-4"))
        self.client.force_login(self.staff)

        response = self.client.get("/platform/billing/")

        snapshot = response.context["snapshot"]
        billing_group = next(group for group in snapshot.organization_groups if group.organization == self.organization)
        self.assertEqual(snapshot.summary.integrity_issue_organization_count, 1)
        self.assertEqual(billing_group.integrity_issues, ("A closed-day charge was modified after posting",))

    def test_event_drilldown_shows_quantities_and_is_org_scoped(self) -> None:
        event = self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=timezone.now(),
            idempotency_key="detail-event",
            rated_at=None,
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            f"/platform/billing/organization/{self.organization.id}/app/{self.app.id}/events/?window=30d"
        )
        wrong_org_response = self.client.get(
            f"/platform/billing/organization/{self.empty_organization.id}/app/{self.app.id}/events/?window=30d"
        )
        wrong_org_page_response = self.client.get(
            f"/platform/billing/?window=30d&organization={self.empty_organization.id}&app={self.app.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, event.idempotency_key)
        self.assertContains(response, "input_tokens")
        self.assertContains(response, "gpt-5.2-codex")
        self.assertEqual(wrong_org_response.status_code, 404)
        self.assertIsNone(wrong_org_page_response.context["expanded_event_page"])
        self.assertNotContains(wrong_org_page_response, event.idempotency_key)

    def test_event_panel_state_is_encoded_in_the_page_url(self) -> None:
        event = self._create_event(
            organization=self.organization,
            app_id=self.app.id,
            app_slug=self.app.slug,
            occurred_at=timezone.now(),
            idempotency_key="expanded-event",
            rated_at=None,
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            f"/platform/billing/?window=30d&organization={self.organization.id}&app={self.app.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["expanded_app_id"], self.app.id)
        self.assertEqual(response.context["expanded_organization"], self.organization)
        self.assertContains(response, event.idempotency_key)
        self.assertContains(
            response,
            f'href="/platform/billing/?window=7d&amp;organization={self.organization.id}&amp;app={self.app.id}"',
        )
        self.assertContains(
            response,
            f'hx-push-url="/platform/billing/?window=30d&amp;organization={self.organization.id}&amp;app={self.app.id}"',
        )
        self.assertContains(response, 'hx-select="#billing-events-')

    def test_event_drilldown_is_paginated(self) -> None:
        now = timezone.now()
        for index in range(billing_admin_service.EVENTS_PER_PAGE + 1):
            self._create_event(
                organization=self.organization,
                app_id=self.app.id,
                app_slug=self.app.slug,
                occurred_at=now - datetime.timedelta(seconds=index),
                idempotency_key=f"page-event-{index}",
                rated_at=None,
            )
        self.client.force_login(self.staff)

        response = self.client.get(
            f"/platform/billing/organization/{self.organization.id}/app/{self.app.id}/events/?window=30d&page=2"
        )
        refreshed_page_response = self.client.get(
            f"/platform/billing/?window=30d&organization={self.organization.id}&app={self.app.id}&page=2"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["event_page"].page.number, 2)
        self.assertEqual(len(response.context["event_page"].page.object_list), 1)
        self.assertContains(response, f"Page 2 of 2 · {billing_admin_service.EVENTS_PER_PAGE + 1} events")
        self.assertEqual(refreshed_page_response.context["expanded_event_page"].page.number, 2)
        self.assertContains(refreshed_page_response, f"Page 2 of 2 · {billing_admin_service.EVENTS_PER_PAGE + 1} events")

    def test_ledger_drilldown_is_paginated_and_org_scoped(self) -> None:
        for index in range(billing_admin_service.LEDGER_ENTRIES_PER_PAGE + 1):
            self._create_ledger_entry(
                organization=self.organization,
                entry_type=models.BillingLedgerEntry.Type.ADJUSTMENT,
                amount=Decimal("1"),
                idempotency_key=f"adjustment-{index}",
                app_id=None,
                app_slug="",
            )
        self._create_ledger_entry(
            organization=self.empty_organization,
            entry_type=models.BillingLedgerEntry.Type.ADJUSTMENT,
            amount=Decimal("10"),
            idempotency_key="other-org-adjustment",
            app_id=None,
            app_slug="",
        )
        self.client.force_login(self.staff)

        response = self.client.get(
            f"/platform/billing/organization/{self.organization.id}/ledger/?window=30d&page=2"
        )
        refreshed_page_response = self.client.get(
            f"/platform/billing/?window=30d&organization={self.organization.id}&ledger=1&page=2"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ledger_page"].page.number, 2)
        self.assertEqual(len(response.context["ledger_page"].page.object_list), 1)
        self.assertContains(
            response,
            f"Page 2 of 2 · {billing_admin_service.LEDGER_ENTRIES_PER_PAGE + 1} entries",
        )
        self.assertNotContains(response, "other-org-adjustment")
        self.assertEqual(refreshed_page_response.context["expanded_ledger_page"].page.number, 2)
        self.assertEqual(refreshed_page_response.context["expanded_organization"], self.organization)
        self.assertContains(
            refreshed_page_response,
            f'href="/platform/billing/?window=7d&amp;organization={self.organization.id}&amp;ledger=1"',
        )

    def test_platform_billing_is_hidden_from_nonstaff(self) -> None:
        anonymous_response = self.client.get("/platform/billing/")
        self.client.force_login(self.nonstaff)
        nonstaff_response = self.client.get("/platform/billing/")
        event_response = self.client.get(
            f"/platform/billing/organization/{self.organization.id}/app/{self.app.id}/events/"
        )
        ledger_response = self.client.get(
            f"/platform/billing/organization/{self.organization.id}/ledger/"
        )

        self.assertEqual(anonymous_response.status_code, 404)
        self.assertEqual(nonstaff_response.status_code, 404)
        self.assertEqual(event_response.status_code, 404)
        self.assertEqual(ledger_response.status_code, 404)

    def test_platform_navigation_connects_fleet_and_billing(self) -> None:
        self.client.force_login(self.staff)

        billing_response = self.client.get("/platform/billing/")
        fleet_response = self.client.get("/platform/fleet/")

        self.assertContains(billing_response, 'href="/platform/fleet/"')
        self.assertContains(fleet_response, 'href="/platform/billing/"')
