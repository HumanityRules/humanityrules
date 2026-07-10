"""Tests for the staff-only fleet-wide redeploy action."""

from django.test import TestCase

from humanityrules_app import models
from humanityrules_app.services.jobs import job_worker


class TestFleetRedeployAll(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Fleet Org", slug="fleet-org")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Fleet AWS")
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="fleet/hermes",
            clone_url="https://github.com/fleet/hermes.git",
        )
        self.app = self._create_app(name="Fleet Agent", slug="fleet-agent", status=models.App.Status.ACTIVE)
        self.environment = self._create_environment(name="Staging", slug="staging", status=models.Environment.Status.READY)
        self.succeeded_source = self._create_deployment(
            app=self.app,
            environment=self.environment,
            status=models.Deployment.Status.SUCCEEDED,
            suffix="staging",
        )
        self.staff = models.User.objects.create_user(
            username="fleet-staff",
            password="pw",
            current_organization=self.organization,
            is_staff=True,
        )
        self.nonstaff = models.User.objects.create_user(
            username="fleet-user",
            password="pw",
            current_organization=self.organization,
        )
        self.client.force_login(self.staff)

    def _create_app(self, name: str, slug: str, status: str) -> models.App:
        """Create an app in the shared test workspace."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            repository=self.repository,
            name=name,
            slug=slug,
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8787,
            health_check_path="/health",
            status=status,
        )

    def _create_environment(self, name: str, slug: str, status: str) -> models.Environment:
        """Create an environment in the shared test AWS account."""
        return models.Environment.objects.create(
            aws_account=self.aws_account,
            name=name,
            slug=slug,
            aws_region="us-east-1",
            status=status,
        )

    def _create_deployment(self, app: models.App, environment: models.Environment, status: str, suffix: str) -> models.Deployment:
        """Create one launched blueprint and deployment source."""
        blueprint = models.DeploymentBlueprint.objects.create(
            app=app,
            environment=environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
            cpu=256,
            memory=512,
            subdomain=f"{app.slug}-{environment.slug}",
        )
        return models.Deployment.objects.create(
            blueprint=blueprint,
            app=app,
            environment=environment,
            subdomain=blueprint.subdomain,
            git_ref="main",
            image_tag=f"{app.slug}-main-{suffix}",
            status=status,
        )

    def _add_failed_environment(self) -> models.Deployment:
        """Add a second, ready environment whose latest deployment failed."""
        environment = self._create_environment(name="Production", slug="production", status=models.Environment.Status.READY)
        return self._create_deployment(
            app=self.app,
            environment=environment,
            status=models.Deployment.Status.FAILED,
            suffix="production",
        )

    def test_staff_fleet_page_shows_redeploy_all_button(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Redeploy all")
        self.assertContains(response, "/platform/fleet/redeploy-all/confirm/")

    def test_confirmation_shows_failed_checkbox_and_current_counts(self) -> None:
        self._add_failed_environment()

        response = self.client.get("/platform/fleet/redeploy-all/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "For 1 entry, the latest deployment succeeded")
        self.assertContains(response, "Include 1 entry whose latest deployment failed")
        self.assertContains(response, 'name="include_failed"')

    def test_redeploy_all_skips_failed_by_default(self) -> None:
        failed_source = self._add_failed_environment()

        response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(response.status_code, 200)
        pending = models.Deployment.objects.filter(status=models.Deployment.Status.PENDING)
        self.assertEqual(pending.count(), 1)
        self.assertEqual(pending.get().environment, self.environment)
        self.assertEqual(models.Deployment.objects.filter(environment=failed_source.environment).count(), 1)
        self.assertContains(response, "Queued 1 redeployment")
        self.assertContains(response, "Failed not included")

    def test_include_failed_queues_each_app_environment_with_unique_tags(self) -> None:
        failed_source = self._add_failed_environment()

        response = self.client.post("/platform/fleet/redeploy-all/", {"include_failed": "on"})

        self.assertEqual(response.status_code, 200)
        pending = list(models.Deployment.objects.filter(status=models.Deployment.Status.PENDING).order_by("environment__slug"))
        self.assertEqual(len(pending), 2)
        self.assertEqual({deployment.environment_id for deployment in pending}, {self.environment.id, failed_source.environment_id})
        self.assertEqual({deployment.created_by_id for deployment in pending}, {self.staff.id})
        self.assertEqual({deployment.status_message for deployment in pending}, {"Fleet redeploy all triggered via web UI"})
        self.assertEqual(len({deployment.image_tag for deployment in pending}), 2)
        self.assertContains(response, "Queued 2 redeployments")

    def test_redeploy_all_reports_torn_down_and_non_ready_targets(self) -> None:
        torn_down_environment = self._create_environment(
            name="Retired",
            slug="retired",
            status=models.Environment.Status.READY,
        )
        self._create_deployment(
            app=self.app,
            environment=torn_down_environment,
            status=models.Deployment.Status.TORN_DOWN,
            suffix="retired",
        )
        draft_environment = self._create_environment(
            name="Draft",
            slug="draft",
            status=models.Environment.Status.PENDING,
        )
        self._create_deployment(
            app=self.app,
            environment=draft_environment,
            status=models.Deployment.Status.SUCCEEDED,
            suffix="draft",
        )
        pending_removal_app = self._create_app(
            name="Removing Agent",
            slug="removing-agent",
            status=models.App.Status.PENDING_REMOVAL,
        )
        self._create_deployment(
            app=pending_removal_app,
            environment=self.environment,
            status=models.Deployment.Status.SUCCEEDED,
            suffix="removing",
        )

        response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(models.Deployment.objects.filter(status=models.Deployment.Status.PENDING).count(), 1)
        self.assertContains(response, "Torn down")
        self.assertContains(response, "Environment not ready")
        self.assertContains(response, "App pending removal")

    def test_second_submission_does_not_queue_a_duplicate(self) -> None:
        first_response = self.client.post("/platform/fleet/redeploy-all/")
        second_response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(models.Deployment.objects.filter(status=models.Deployment.Status.PENDING).count(), 1)
        self.assertContains(second_response, "Queued 0 redeployments")
        self.assertContains(second_response, "Deployment already in progress")

    def test_fleet_redeploy_endpoints_hide_from_nonstaff(self) -> None:
        self.client.force_login(self.nonstaff)

        confirm_response = self.client.get("/platform/fleet/redeploy-all/confirm/")
        post_response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(confirm_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)

    def test_redeploy_all_requires_post(self) -> None:
        response = self.client.get("/platform/fleet/redeploy-all/")

        self.assertEqual(response.status_code, 405)

    def test_worker_serializes_pending_deployments_for_the_same_app(self) -> None:
        self._add_failed_environment()
        self.client.post("/platform/fleet/redeploy-all/", {"include_failed": "on"})

        first = job_worker._claim_pending_app_deployment(label="")
        second = job_worker._claim_pending_app_deployment(label="")

        self.assertIsNotNone(first)
        self.assertIsNone(second)

        first.status = models.Deployment.Status.SUCCEEDED
        first.save(update_fields=["status", "updated_at"])
        next_deployment = job_worker._claim_pending_app_deployment(label="")
        self.assertIsNotNone(next_deployment)
        self.assertEqual(next_deployment.app_id, self.app.id)
