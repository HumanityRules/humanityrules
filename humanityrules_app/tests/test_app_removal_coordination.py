"""Tests for app-removal enqueue idempotency, worker claim, and executor guards."""

from io import StringIO
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models
from humanityrules_app.management.commands import humr_control
from humanityrules_app.services import abac_service
from humanityrules_app.services.jobs import app_job_service
from humanityrules_app.services.jobs import app_remove_executor
from humanityrules_app.services.jobs import job_worker


class TestAppRemovalCoordination(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Removal Coordination", slug="removal-coordination")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Removal AWS",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Operations",
            slug="operations",
        )
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="removal-coordination/hermes",
            clone_url="https://github.com/removal-coordination/hermes.git",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            repository=self.repository,
            name="Removal Agent",
            slug="removalagent",
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        self.user = models.User.objects.create_user(
            username="removal-admin",
            password="pw",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.organization, admin_user=self.user)

    def _removal_started_count(self) -> int:
        return models.DeploymentRecord.objects.filter(
            app=self.app,
            event_type=models.DeploymentRecord.EventType.REMOVAL_STARTED,
        ).count()

    def test_web_enqueue_rechecks_stale_app_after_lock(self) -> None:
        stale_app = self.app
        self.client.force_login(self.user)
        first_response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        # A second submit carrying the pre-removal snapshot must lose to the re-locked row.
        with patch("humanityrules_app.views.apps._get_app_for_user", return_value=stale_app):
            second_response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 422)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)
        self.assertEqual(self._removal_started_count(), 1)

    def test_cli_enqueue_is_idempotent_for_pending_removal(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        command = humr_control.Command(stdout=stdout, stderr=stderr)

        command._queue_app_removal(app=self.app, delete_all_data=False)
        command._queue_app_removal(app=self.app, delete_all_data=False)

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)
        self.assertEqual(self._removal_started_count(), 1)
        self.assertIn("already pending removal", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_worker_claims_pending_removal_into_removing(self) -> None:
        app_job_service.queue_removal(app=self.app, created_by=self.user, delete_all_data=False, teardown_first=False)

        claimed = job_worker._claim_pending_app_removal(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, self.app.id)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVING)

    def test_removal_refused_while_infra_may_exist_and_stays_retryable(self) -> None:
        self.app.may_have_infra = True
        self.app.save(update_fields=["may_have_infra", "updated_at"])
        app_job_service.queue_removal(app=self.app, created_by=self.user, delete_all_data=False, teardown_first=False)
        self.app.job_status = models.App.JobStatus.REMOVING
        self.app.save(update_fields=["job_status", "updated_at"])

        success = app_remove_executor.run_removal(app_id=str(self.app.id))

        self.assertFalse(success)
        self.app.refresh_from_db()
        # The app is not deleted and returns to idle, so the removal can be retried after teardown.
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
        self.assertIn("tear it down first", self.app.last_attempt_error)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=self.app,
                attempt_id=self.app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.REMOVAL_FAILED,
            ).exists()
        )
        # Retry: an idle app accepts a fresh removal attempt.
        app_job_service.queue_removal(app=self.app, created_by=self.user, delete_all_data=False, teardown_first=True)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)

    def test_removal_with_teardown_first_tears_down_then_deletes_the_app(self) -> None:
        self.app.may_have_infra = True
        self.app.live_state = models.App.LiveState.DEPLOYED
        self.app.service_url = "https://removalagent.example.com"
        self.app.save(update_fields=["may_have_infra", "live_state", "service_url", "updated_at"])
        app_job_service.queue_removal(app=self.app, created_by=self.user, delete_all_data=False, teardown_first=True)
        self.app.job_status = models.App.JobStatus.REMOVING
        self.app.save(update_fields=["job_status", "updated_at"])

        with patch(
            "humanityrules_app.services.jobs.app_remove_executor.app_deployment_teardown_executor.teardown_infra",
            return_value=True,
        ) as teardown_mock:
            success = app_remove_executor.run_removal(app_id=str(self.app.id))

        self.assertTrue(success)
        teardown_mock.assert_called_once()
        self.assertFalse(models.App.objects.filter(id=self.app.id).exists())
