"""Tests for the platform fleet table and policy-proxy activity."""

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
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
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
        self.assertIn("None recorded", body)

        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            last_policy_proxy_activity_at=timezone.now() - datetime.timedelta(minutes=20),
        )
        response = self.client.get("/platform/fleet/")
        body = response.content.decode()
        self.assertNotIn("None recorded", body)

    def test_fleet_renders_one_table_row_per_ha_and_rows_for_empty_environments(self) -> None:
        models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name="Second Fleet Agent",
            slug="second-fleet-agent",
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            live_state=models.App.LiveState.DEPLOYED,
            last_attempt_id=uuid.uuid7(),
        )
        empty_environment = models.Environment.objects.create(
            aws_account=self.environment.aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
        )

        response = self.client.get("/platform/fleet/")
        empty_environment_count = sum(not group.apps for group in fleet_service.build_fleet_snapshot())

        self.assertContains(response, "<table", count=1)
        self.assertContains(response, 'data-fleet-row="app"', count=2)
        self.assertContains(response, 'data-fleet-row="environment-empty"', count=empty_environment_count)
        self.assertContains(response, "Fleet Org", count=2 + empty_environment_count)
        self.assertContains(response, self.environment.slug, count=2)
        self.assertContains(response, empty_environment.slug, count=1)
        self.assertNotContains(response, "Query live state")

    def test_removed_live_state_endpoint_returns_not_found(self) -> None:
        response = self.client.get(f"/platform/fleet/env/{self.environment.id}/live/")

        self.assertEqual(response.status_code, 404)
