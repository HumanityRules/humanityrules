"""Tests for billing usage-event ingestion from environment brokers."""

import datetime
import hashlib
import json

from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.tests.app_test_factories import make_source_template


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name="Usage Agent",
            slug="usage-agent",
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        self.raw_token = "a" * 64
        models.EnvironmentBearerToken.objects.create(
            environment=self.environment,
            token_hash=_hash(raw=self.raw_token),
        )
        self.client = Client()

    def post_events(self, events: list[dict], token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/runtime/billing-usage-events",
            data=json.dumps({
                "owner_username": "vmendi",
                "app_slug": self.app.slug,
                "events": events,
            }),
            content_type="application/json",
            **headers,
        )
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
        self.assertEqual(body, {"ok": True})
        self.assertEqual(models.BillingUsageEvent.objects.count(), 2)
        event = models.BillingUsageEvent.objects.get(idempotency_key="evt-1")
        self.assertEqual(event.organization, self.organization)
        self.assertEqual(event.app_id, self.app.id)
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
        self.assertEqual(second_body, {"ok": True})
        self.assertEqual(models.BillingUsageEvent.objects.count(), 2)
        # The duplicate did not overwrite the original event.
        self.assertEqual(models.BillingUsageEvent.objects.get(idempotency_key="evt-1").quantities["input_tokens"], 200)

    def test_app_must_belong_to_bearer_environment(self) -> None:
        other_environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
        )
        other_token = "b" * 64
        models.EnvironmentBearerToken.objects.create(
            environment=other_environment,
            token_hash=_hash(raw=other_token),
        )

        status, body = self.post_events(events=[_event()], token=other_token)

        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "app not found in environment")
        self.assertFalse(models.BillingUsageEvent.objects.exists())

    def test_empty_batch_is_accepted(self) -> None:
        status, body = self.post_events(events=[], token=self.raw_token)

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})


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

    def test_missing_quantity_key_is_rejected(self) -> None:
        quantities = _quantities()
        del quantities["cache_read_tokens"]
        self._assert_rejected(event=_event(idempotency_key="evt-2", quantities=quantities), error_fragment="exactly")

    def test_unknown_quantity_key_is_rejected(self) -> None:
        self._assert_rejected(
            event=_event(idempotency_key="evt-2", quantities=_quantities(request_count=1)),
            error_fragment="exactly",
        )

    def test_unknown_source_is_rejected(self) -> None:
        self._assert_rejected(event=_event(idempotency_key="evt-2", source="tavily"), error_fragment="source")

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
