"""Tests for policy-proxy runtime activity ingestion.

The reporting app is derived from its bearer token, so an app_id in the body is
inert. That replaces the old "app not found in environment" rejection, which
only existed because the caller named the app it was reporting for.
"""

import datetime
import json

from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


class PolicyProxyActivityTestBase(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Activity Org", slug="activity-org")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Activity AWS")
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
        self.app = self.make_app(name="Activity Agent", slug="activity-agent")
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="a" * 64)
        self.client = Client()

    def make_app(self, name: str, slug: str) -> models.App:
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name=name,
            slug=slug,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )

    def post_activity(self, observed_at: str, token: str | None, app_id: str | None) -> tuple[int, dict]:
        headers = bearer_test_helpers.auth_header(raw=token) if token is not None else {}
        body: dict[str, str] = {"observed_at": observed_at}
        if app_id is not None:
            body["app_id"] = app_id
        response = self.client.post(
            "/api/runtime/policy-proxy-activity",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()


class TestPolicyProxyActivityAuthentication(PolicyProxyActivityTestBase):
    def test_missing_bearer_is_rejected(self) -> None:
        status, body = self.post_activity(
            observed_at=timezone.now().isoformat(),
            token=None,
            app_id=None,
        )

        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_is_rejected(self) -> None:
        status, body = self.post_activity(
            observed_at=timezone.now().isoformat(),
            token="wrong",
            app_id=None,
        )

        self.assertEqual(status, 401)
        self.assertIn("error", body)


class TestPolicyProxyActivityIngestion(PolicyProxyActivityTestBase):
    def test_first_report_creates_app_environment_activity(self) -> None:
        observed_at = timezone.now() - datetime.timedelta(minutes=2)

        status, body = self.post_activity(
            observed_at=observed_at.isoformat(),
            token=self.raw_token,
            app_id=None,
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})
        activity = models.AppEnvironmentActivity.objects.get(
            organization=self.organization,
            app=self.app,
        )
        self.assertEqual(activity.last_policy_proxy_activity_at, observed_at)

    def test_reports_update_monotonically(self) -> None:
        newest = timezone.now() - datetime.timedelta(minutes=1)
        oldest = newest - datetime.timedelta(hours=1)
        later = newest + datetime.timedelta(seconds=30)
        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            last_policy_proxy_activity_at=newest,
        )

        status, _body = self.post_activity(
            observed_at=oldest.isoformat(),
            token=self.raw_token,
            app_id=None,
        )

        self.assertEqual(status, 200)
        activity = models.AppEnvironmentActivity.objects.get(app=self.app)
        self.assertEqual(activity.last_policy_proxy_activity_at, newest)

        status, _body = self.post_activity(
            observed_at=later.isoformat(),
            token=self.raw_token,
            app_id=None,
        )

        self.assertEqual(status, 200)
        activity.refresh_from_db()
        self.assertEqual(activity.last_policy_proxy_activity_at, later)

    def test_activity_lands_on_the_bearer_app_whatever_the_body_names(self) -> None:
        neighbour = self.make_app(name="Neighbour Agent", slug="neighbour-agent")

        status, _body = self.post_activity(
            observed_at=timezone.now().isoformat(),
            token=self.raw_token,
            app_id=neighbour.slug,
        )

        self.assertEqual(status, 200)
        self.assertTrue(models.AppEnvironmentActivity.objects.filter(app=self.app).exists())
        self.assertFalse(models.AppEnvironmentActivity.objects.filter(app=neighbour).exists())

    def test_a_neighbours_bearer_records_against_that_neighbour(self) -> None:
        neighbour = self.make_app(name="Neighbour Agent", slug="neighbour-agent")
        neighbour_token = bearer_test_helpers.make_app_bearer(app=neighbour, raw="b" * 64)

        status, _body = self.post_activity(
            observed_at=timezone.now().isoformat(),
            token=neighbour_token,
            app_id=None,
        )

        self.assertEqual(status, 200)
        self.assertTrue(models.AppEnvironmentActivity.objects.filter(app=neighbour).exists())
        self.assertFalse(models.AppEnvironmentActivity.objects.filter(app=self.app).exists())

    def test_timestamp_requires_timezone(self) -> None:
        status, body = self.post_activity(
            observed_at="2026-07-09T12:00:00",
            token=self.raw_token,
            app_id=None,
        )

        self.assertEqual(status, 400)
        self.assertIn("timezone", body["error"])

    def test_timestamp_too_far_in_future_is_rejected(self) -> None:
        observed_at = timezone.now() + datetime.timedelta(minutes=6)

        status, body = self.post_activity(
            observed_at=observed_at.isoformat(),
            token=self.raw_token,
            app_id=None,
        )

        self.assertEqual(status, 400)
        self.assertIn("future", body["error"])
