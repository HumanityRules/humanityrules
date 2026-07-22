"""Tests for policy-proxy activity on the platform fleet view."""

import datetime
import uuid

from django.test import TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services import fleet_service
from humanityrules_app.tests.app_test_factories import make_source_template


class TestFleetActivity(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Fleet Org", slug="fleet-org")
        aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Fleet AWS")
        self.environment = models.Environment.objects.create(
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
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name="Fleet Agent",
            slug="fleet-agent",
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            live_state=models.App.LiveState.DEPLOYED,
            last_attempt_id=uuid.uuid7(),
        )
        self.staff = models.User.objects.create_user(
            username="fleet-staff",
            password="pw",
            current_organization=self.organization,
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_snapshot_annotates_activity_for_app(self) -> None:
        observed_at = timezone.now() - datetime.timedelta(minutes=20)
        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            last_policy_proxy_activity_at=observed_at,
        )

        groups = fleet_service.build_fleet_snapshot()

        group = next(group for group in groups if group.environment == self.environment)
        self.assertEqual(group.apps[0].last_policy_proxy_activity_at, observed_at)

    def test_fleet_renders_activity_and_empty_state(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("No activity recorded", body)

        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            last_policy_proxy_activity_at=timezone.now() - datetime.timedelta(minutes=20),
        )
        response = self.client.get("/platform/fleet/")
        body = response.content.decode()
        self.assertIn("Last activity", body)
        self.assertNotIn("No activity recorded", body)
