"""Tests for billing usage-event ingestion from app brokers.

The app and the owner label on every event come from the per-app bearer; the
body carries only `events`.
"""

import datetime
import json

from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services.billing import grants
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


def _quantities(**overrides: int) -> dict:
    quantities = {
        "input_tokens": 200,
        "output_tokens": 350,
        "cache_read_tokens": 1000,
        "cache_write_tokens": 0,
        "reasoning_tokens": 80,
    }
    quantities.update(overrides)
    return quantities


def _event(**overrides: object) -> dict:
    event = {
        "idempotency_key": "evt-1",
        "occurred_at": (timezone.now() - datetime.timedelta(seconds=30)).isoformat(),
        "source": "llm",
        "subkey": "gpt-5.2-codex",
        "quantities": _quantities(),
    }
    event.update(overrides)
    return event


class BillingUsageIngestTestBase(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Usage Org", slug="usage-org")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Usage AWS")
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        self.owner = models.User.objects.create_user(
            username="vmendi", email="vmendi@example.com", password="pw", current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            user=self.owner, organization=self.organization, role=models.OrganizationMembership.Role.MEMBER,
        )
        self.app = self._make_app(slug="usage-agent", owner_username=self.owner.username)
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="a" * 64)
        self.client = Client()

    def _make_app(self, slug: str, owner_username: str | None) -> models.App:
        app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name=slug,
            slug=slug,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        if owner_username is not None:
            models.ResourceTag.objects.create(
                organization=self.organization, resource_type=models.ResourceTag.ResourceType.APP,
                app=app, key="owner", value=owner_username,
            )
        return app

    def post_events(self, events: list[dict], token: str | None) -> tuple[int, dict]:
        return self.post_body(body={"events": events}, token=token)

    def post_body(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/runtime/billing-usage-events",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()

    def get_entitlement(self, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.get("/api/runtime/billing-entitlement", **headers)
        return response.status_code, response.json()


class TestBillingUsageAuthentication(BillingUsageIngestTestBase):
    def test_missing_bearer_is_rejected(self) -> None:
        status, body = self.post_events(events=[_event()], token=None)

        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_is_rejected(self) -> None:
        status, body = self.post_events(events=[_event()], token="wrong")

        self.assertEqual(status, 401)
        self.assertIn("error", body)


class TestBillingUsageIngestion(BillingUsageIngestTestBase):
    def test_batch_creates_events_with_attribution(self) -> None:
        occurred_at = timezone.now() - datetime.timedelta(minutes=1)

        status, body = self.post_events(
            events=[
                _event(idempotency_key="evt-1", occurred_at=occurred_at.isoformat()),
                _event(idempotency_key="evt-2", subkey=""),
            ],
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(models.BillingUsageEvent.objects.count(), 2)
        event = models.BillingUsageEvent.objects.get(idempotency_key="evt-1")
        self.assertEqual(event.organization, self.organization)
        self.assertEqual(event.app_id, self.app.id)
        self.assertEqual(event.app_slug, self.app.slug)
        self.assertEqual(event.owner_username, "vmendi")
        self.assertEqual(event.source, "llm")
        self.assertEqual(event.subkey, "gpt-5.2-codex")
        self.assertEqual(event.quantities, _quantities())
        self.assertEqual(event.occurred_at, occurred_at)
        self.assertIsNone(event.rated_at)

    def test_duplicate_idempotency_keys_are_ignored(self) -> None:
        first_status, _body = self.post_events(events=[_event()], token=self.raw_token)
        second_status, second_body = self.post_events(
            events=[_event(quantities=_quantities(input_tokens=999999)), _event(idempotency_key="evt-new")],
            token=self.raw_token,
        )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(second_body["ok"], True)
        self.assertEqual(second_body["skipped"], 0)
        self.assertEqual(models.BillingUsageEvent.objects.count(), 2)
        # The duplicate did not overwrite the original event.
        self.assertEqual(models.BillingUsageEvent.objects.get(idempotency_key="evt-1").quantities["input_tokens"], 200)

    def test_identity_in_body_is_ignored_in_favour_of_the_bearers_app(self) -> None:
        other = self._make_app(slug="theirs", owner_username="someone-else")

        status, _body = self.post_body(
            body={"owner_username": "someone-else", "app_slug": other.slug, "events": [_event()]},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        event = models.BillingUsageEvent.objects.get(idempotency_key="evt-1")
        self.assertEqual(event.app_id, self.app.id)
        self.assertEqual(event.app_slug, self.app.slug)
        self.assertEqual(event.owner_username, "vmendi")

    def test_app_without_owner_cannot_report_usage(self) -> None:
        lonely = self._make_app(slug="lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="b" * 64)

        status, body = self.post_events(events=[_event()], token=lonely_token)

        self.assertEqual(status, 404)
        self.assertIn("error", body)
        self.assertFalse(models.BillingUsageEvent.objects.exists())

    def test_events_survive_app_deletion(self) -> None:
        app_id, app_slug = self.app.id, self.app.slug
        status, _body = self.post_events(
            events=[_event(idempotency_key="evt-1"), _event(idempotency_key="evt-2")],
            token=self.raw_token,
        )

        self.app.delete()

        self.assertEqual(status, 200)
        self.assertFalse(models.App.objects.filter(id=app_id).exists())
        self.assertEqual(models.BillingUsageEvent.objects.count(), 2)
        for event in models.BillingUsageEvent.objects.all():
            self.assertEqual(event.app_id, app_id)
            self.assertEqual(event.app_slug, app_slug)

    def test_empty_batch_is_accepted(self) -> None:
        status, body = self.post_events(events=[], token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["skipped"], 0)


class TestBillingUsageVersionSkew(BillingUsageIngestTestBase):
    """A broker older than this CP still gets its usage ingested."""

    def test_missing_quantity_keys_read_as_zero(self) -> None:
        quantities = _quantities()
        del quantities["cache_read_tokens"]
        del quantities["cache_write_tokens"]

        status, body = self.post_events(events=[_event(quantities=quantities)], token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["skipped"], 0)
        stored = models.BillingUsageEvent.objects.get(idempotency_key="evt-1")
        self.assertEqual(stored.quantities, _quantities(cache_read_tokens=0, cache_write_tokens=0))

    def test_unknown_source_is_skipped_without_dropping_the_batch(self) -> None:
        status, body = self.post_events(
            events=[
                _event(idempotency_key="evt-llm"),
                _event(idempotency_key="evt-tavily", source="tavily", quantities={"request_count": 3}),
            ],
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["ok"], True)
        self.assertEqual(body["skipped"], 1)
        self.assertEqual([event.idempotency_key for event in models.BillingUsageEvent.objects.all()], ["evt-llm"])

    def test_a_skipped_event_still_fails_the_batch_when_malformed(self) -> None:
        """Skipping is for unknown sources only — an unusable envelope is still a bug."""
        status, body = self.post_events(
            events=[_event(idempotency_key="", source="tavily")],
            token=self.raw_token,
        )

        self.assertEqual(status, 400)
        self.assertIn("idempotency_key", body["error"])

    def test_a_non_object_event_rejects_the_batch(self) -> None:
        status, body = self.post_events(events=[_event(), "not an event"], token=self.raw_token)

        self.assertEqual(status, 400)
        self.assertIn("events[1]", body["error"])
        self.assertFalse(models.BillingUsageEvent.objects.exists())


class TestBillingUsageValidation(BillingUsageIngestTestBase):
    def _assert_rejected(self, event: dict, error_fragment: str) -> None:
        status, body = self.post_events(events=[_event(), event], token=self.raw_token)

        self.assertEqual(status, 400)
        self.assertIn("events[1]", body["error"])
        self.assertIn(error_fragment, body["error"])
        self.assertFalse(models.BillingUsageEvent.objects.exists())

    def test_negative_quantity_rejects_the_batch(self) -> None:
        self._assert_rejected(
            event=_event(idempotency_key="evt-2", quantities=_quantities(output_tokens=-1)),
            error_fragment="output_tokens",
        )

    def test_boolean_quantity_is_rejected(self) -> None:
        self._assert_rejected(
            event=_event(idempotency_key="evt-2", quantities=_quantities(input_tokens=True)),
            error_fragment="input_tokens",
        )

    def test_unknown_quantity_key_is_rejected(self) -> None:
        self._assert_rejected(
            event=_event(idempotency_key="evt-2", quantities=_quantities(request_count=1)),
            error_fragment="not recognized",
        )

    def test_missing_idempotency_key_is_rejected(self) -> None:
        event = _event()
        del event["idempotency_key"]
        self._assert_rejected(event=event, error_fragment="idempotency_key")

    def test_naive_timestamp_is_rejected(self) -> None:
        self._assert_rejected(
            event=_event(idempotency_key="evt-2", occurred_at="2026-07-29T12:00:00"),
            error_fragment="timezone",
        )

    def test_far_future_timestamp_is_rejected(self) -> None:
        occurred_at = (timezone.now() + datetime.timedelta(minutes=6)).isoformat()
        self._assert_rejected(event=_event(idempotency_key="evt-2", occurred_at=occurred_at), error_fragment="future")

    def test_oversized_batch_is_rejected(self) -> None:
        events = [_event(idempotency_key=f"evt-{index}") for index in range(501)]

        status, body = self.post_events(events=events, token=self.raw_token)

        self.assertEqual(status, 400)
        self.assertIn("500", body["error"])
        self.assertFalse(models.BillingUsageEvent.objects.exists())


class TestEntitlementSnapshotOnIngest(BillingUsageIngestTestBase):
    """Every ingest response carries the snapshot, so a spending org's broker cache stays fresh."""

    def test_ingest_response_carries_the_snapshot(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        status, body = self.post_events(events=[_event()], token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["entitlement"], {
            "credits_remaining": 500,
            "monthly_grant": 500,
            "renewal_date": None,
            "plan": "trial",
            "exhausted": False,
        })

    def test_rejected_batch_carries_no_snapshot(self) -> None:
        status, body = self.post_events(events=[_event(quantities="not a dict")], token=self.raw_token)

        self.assertEqual(status, 400)
        self.assertNotIn("entitlement", body)


class TestBillingEntitlementEndpoint(BillingUsageIngestTestBase):
    """The on-demand snapshot the broker fetches when its cache has gone stale."""

    def test_missing_bearer_is_rejected(self) -> None:
        status, body = self.get_entitlement(token=None)

        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_is_rejected(self) -> None:
        status, body = self.get_entitlement(token="wrong")

        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_bearer_resolves_the_apps_organization(self) -> None:
        grants.grant_trial_credits(organization=self.organization)

        status, body = self.get_entitlement(token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["entitlement"], {
            "credits_remaining": 500,
            "monthly_grant": 500,
            "renewal_date": None,
            "plan": "trial",
            "exhausted": False,
        })

    def test_an_org_with_no_billing_activity_reads_as_empty_but_not_exhausted(self) -> None:
        status, body = self.get_entitlement(token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body["entitlement"]["credits_remaining"], 0)
        self.assertFalse(body["entitlement"]["exhausted"])

    def test_post_only_endpoints_stay_post_only(self) -> None:
        response = self.client.get(
            "/api/runtime/billing-usage-events", HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )

        self.assertEqual(response.status_code, 405)
