"""Tests for policy-proxy runtime activity ingestion."""

import datetime
import hashlib
import json

from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app import models


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="activity/hermes",
            clone_url="https://github.com/activity/hermes.git",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            environment=self.environment,
            repository=repository,
            name="Activity Agent",
            slug="activity-agent",
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
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

    def post_activity(self, app_id: str, observed_at: str, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/runtime/policy-proxy-activity",
            data=json.dumps({"app_id": app_id, "observed_at": observed_at}),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()


class TestPolicyProxyActivityAuthentication(PolicyProxyActivityTestBase):
    def test_missing_bearer_is_rejected(self) -> None:
        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at=timezone.now().isoformat(),
            token=None,
        )

        self.assertEqual(status, 401)
        self.assertIn("error", body)

    def test_wrong_bearer_is_rejected(self) -> None:
        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at=timezone.now().isoformat(),
            token="wrong",
        )

        self.assertEqual(status, 401)
        self.assertIn("error", body)


class TestPolicyProxyActivityIngestion(PolicyProxyActivityTestBase):
    def test_first_report_creates_app_environment_activity(self) -> None:
        observed_at = timezone.now() - datetime.timedelta(minutes=2)

        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at=observed_at.isoformat(),
            token=self.raw_token,
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
            app_id=self.app.slug,
            observed_at=oldest.isoformat(),
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        activity = models.AppEnvironmentActivity.objects.get(app=self.app)
        self.assertEqual(activity.last_policy_proxy_activity_at, newest)

        status, _body = self.post_activity(
            app_id=self.app.slug,
            observed_at=later.isoformat(),
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        activity.refresh_from_db()
        self.assertEqual(activity.last_policy_proxy_activity_at, later)

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

        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at=timezone.now().isoformat(),
            token=other_token,
        )

        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "app not found in environment")
        self.assertFalse(models.AppEnvironmentActivity.objects.exists())

    def test_timestamp_requires_timezone(self) -> None:
        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at="2026-07-09T12:00:00",
            token=self.raw_token,
        )

        self.assertEqual(status, 400)
        self.assertIn("timezone", body["error"])

    def test_timestamp_too_far_in_future_is_rejected(self) -> None:
        observed_at = timezone.now() + datetime.timedelta(minutes=6)

        status, body = self.post_activity(
            app_id=self.app.slug,
            observed_at=observed_at.isoformat(),
            token=self.raw_token,
        )

        self.assertEqual(status, 400)
        self.assertIn("future", body["error"])
