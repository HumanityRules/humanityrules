"""Tests for the staff-only fleet Remove action (teardown + data purge + delete)."""

import uuid

from django.test import TestCase

from humanityrules_app import models
from humanityrules_app.services import fleet_service
from humanityrules_app.tests import app_test_factories


class TestFleetRemove(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Fleet Org", slug="fleet-org")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Fleet AWS")
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Assistants",
            slug="assistants",
        )
        self.source_template = app_test_factories.make_source_template()
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        self.app = self._make_app(slug="fleetagent", job_status=models.App.JobStatus.IDLE)
        self.staff = models.User.objects.create_user(
            username="fleet-staff",
            password="pw",
            current_organization=self.organization,
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def _make_app(self, slug: str, job_status: str) -> models.App:
        """Create a live fleet app row (deployed, so infra teardown is part of removal)."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=self.source_template,
            name=slug,
            slug=slug,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            live_state=models.App.LiveState.DEPLOYED,
            may_have_infra=True,
            job_status=job_status,
            last_attempt_id=uuid.uuid7(),
        )

    def test_fleet_page_shows_remove_button_for_a_live_ha(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/platform/fleet/app/{self.app.id}/remove/confirm/")

    def test_remove_button_is_hidden_while_a_job_is_in_flight(self) -> None:
        self.app.job_status = models.App.JobStatus.DEPLOYING
        self.app.save(update_fields=["job_status", "updated_at"])

        response = self.client.get("/platform/fleet/")

        self.assertNotContains(response, f"/platform/fleet/app/{self.app.id}/remove/confirm/")

    def test_confirm_modal_names_the_ha_and_posts_to_the_remove_url(self) -> None:
        response = self.client.get(f"/platform/fleet/app/{self.app.id}/remove/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remove fleetagent")
        self.assertContains(response, f"/platform/fleet/app/{self.app.id}/remove/")

    def test_remove_queues_a_removal_for_a_still_deployed_app(self) -> None:
        response = self.client.post(f"/platform/fleet/app/{self.app.id}/remove/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Queued removal of fleetagent")
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=self.app, event_type=models.DeploymentRecord.EventType.REMOVAL_STARTED,
            ).exists()
        )

    def test_remove_leaves_the_worker_label_untouched(self) -> None:
        self.app.label = "worktree-a"
        self.app.save(update_fields=["label", "updated_at"])

        self.client.post(f"/platform/fleet/app/{self.app.id}/remove/")

        self.app.refresh_from_db()
        self.assertEqual(self.app.label, "worktree-a")

    def test_remove_rejects_a_busy_app(self) -> None:
        self.app.job_status = models.App.JobStatus.DEPLOYING
        self.app.save(update_fields=["job_status", "updated_at"])

        response = self.client.post(f"/platform/fleet/app/{self.app.id}/remove/")

        self.assertContains(response, fleet_service.SKIP_APP_BUSY)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.DEPLOYING)

    def test_remove_rejects_an_environment_that_is_not_ready(self) -> None:
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status"])

        response = self.client.post(f"/platform/fleet/app/{self.app.id}/remove/")

        self.assertContains(response, fleet_service.SKIP_ENVIRONMENT_NOT_READY)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)

    def test_remove_is_not_reachable_for_non_staff(self) -> None:
        nonstaff = models.User.objects.create_user(
            username="fleet-user",
            password="pw",
            current_organization=self.organization,
        )
        self.client.force_login(nonstaff)

        response = self.client.post(f"/platform/fleet/app/{self.app.id}/remove/")

        self.assertEqual(response.status_code, 404)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
