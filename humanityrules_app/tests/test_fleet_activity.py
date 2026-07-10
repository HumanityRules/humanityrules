"""Tests for policy-proxy activity on the platform fleet view."""

import datetime

from django.test import TestCase
from django.utils import timezone

from humanityrules_app import models
from humanityrules_app.services import fleet_status


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
        repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="fleet/hermes",
            clone_url="https://github.com/fleet/hermes.git",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=workspace,
            repository=repository,
            name="Fleet Agent",
            slug="fleet-agent",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8787,
            health_check_path="/health",
        )
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
            cpu=256,
            memory=512,
        )
        models.Deployment.objects.create(
            blueprint=blueprint,
            app=self.app,
            environment=self.environment,
            git_ref="main",
            image_tag="fleet-agent-main",
            status=models.Deployment.Status.SUCCEEDED,
        )
        self.staff = models.User.objects.create_user(
            username="fleet-staff",
            password="pw",
            current_organization=self.organization,
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_snapshot_annotates_activity_for_same_app_and_environment(self) -> None:
        observed_at = timezone.now() - datetime.timedelta(minutes=20)
        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            environment=self.environment,
            last_policy_proxy_activity_at=observed_at,
        )

        groups = fleet_status.build_fleet_snapshot()

        group = next(group for group in groups if group.environment == self.environment)
        self.assertEqual(group.deployments[0].last_policy_proxy_activity_at, observed_at)

    def test_fleet_renders_activity_and_empty_state(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("No activity recorded", body)

        models.AppEnvironmentActivity.objects.create(
            organization=self.organization,
            app=self.app,
            environment=self.environment,
            last_policy_proxy_activity_at=timezone.now() - datetime.timedelta(minutes=20),
        )
        response = self.client.get("/platform/fleet/")
        body = response.content.decode()
        self.assertIn("Last activity", body)
        self.assertNotIn("No activity recorded", body)
