"""Tests for staff-only fleet redeploy and recovery actions against the App job model."""

import uuid
from unittest.mock import patch

from django.test import TestCase

from humanityrules_app import models
from humanityrules_app.services import fleet_service
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
        self.environment = self._create_environment(name="Staging", slug="staging", status=models.Environment.Status.READY)
        self.app = self._make_app(
            slug="fleetagent",
            environment=self.environment,
            live_state=models.App.LiveState.DEPLOYED,
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

    def _create_environment(self, name: str, slug: str, status: str) -> models.Environment:
        return models.Environment.objects.create(
            aws_account=self.aws_account,
            name=name,
            slug=slug,
            aws_region="us-east-1",
            status=status,
        )

    def _make_app(
        self,
        slug: str,
        environment: models.Environment,
        live_state: str,
        job_status: str = models.App.JobStatus.IDLE,
        last_attempt_error: str = "",
    ) -> models.App:
        """Create a fleet app row with an attempt behind it (so it shows on the fleet page)."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=environment,
            repository=self.repository,
            name=slug,
            slug=slug,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            live_state=live_state,
            job_status=job_status,
            last_attempt_id=uuid.uuid7(),
            last_attempt_error=last_attempt_error,
        )

    def _add_failed_app(self) -> models.App:
        """Add a second app, in its own ready environment, whose latest attempt failed."""
        environment = self._create_environment(name="Production", slug="production", status=models.Environment.Status.READY)
        return self._make_app(
            slug="failedagent",
            environment=environment,
            live_state=models.App.LiveState.DEPLOYED,
            last_attempt_error="deploy crashed",
        )

    def test_staff_fleet_page_shows_redeploy_all_button(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Redeploy all")
        self.assertContains(response, "/platform/fleet/redeploy-all/confirm/")
        self.assertContains(response, f"/platform/fleet/app/{self.app.id}/redeploy/")

    def test_staff_fleet_page_shows_fail_stuck_deployments_button(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fail stuck deployments")
        self.assertContains(response, "/platform/fleet/fail-unsettled/confirm/")

    def test_fail_unsettled_confirmation_shows_current_count(self) -> None:
        self._make_app(
            slug="pendingagent",
            environment=self.environment,
            live_state=models.App.LiveState.NOT_DEPLOYED,
            job_status=models.App.JobStatus.DEPLOY_PENDING,
        )

        response = self.client.get("/platform/fleet/fail-unsettled/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mark 1 unfinished deployment as failed", html=False)
        self.assertContains(response, "/platform/fleet/fail-unsettled/")

    def test_fail_unsettled_marks_every_unsettled_job_status_failed(self) -> None:
        unsettled_statuses = (
            models.App.JobStatus.DEPLOY_PENDING,
            models.App.JobStatus.DEPLOYING,
            models.App.JobStatus.TEARDOWN_PENDING,
            models.App.JobStatus.TEARING_DOWN,
            models.App.JobStatus.REMOVAL_PENDING,
            models.App.JobStatus.REMOVING,
        )
        unsettled_apps = [
            self._make_app(
                slug=f"unsettled{index}",
                environment=self.environment,
                live_state=models.App.LiveState.DEPLOYED,
                job_status=status,
            )
            for index, status in enumerate(unsettled_statuses)
        ]

        response = self.client.post("/platform/fleet/fail-unsettled/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Marked {len(unsettled_apps)} unsettled deployments as failed")
        for app in unsettled_apps:
            app.refresh_from_db()
            self.assertEqual(app.job_status, models.App.JobStatus.IDLE)
            self.assertEqual(app.last_attempt_error, fleet_service.RECOVERY_STATUS_MESSAGE)
        # The already-idle succeeded app is untouched.
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
        self.assertEqual(self.app.last_attempt_error, "")

    def test_fail_unsettled_is_safe_to_repeat(self) -> None:
        pending = self._make_app(
            slug="pendingagent",
            environment=self.environment,
            live_state=models.App.LiveState.NOT_DEPLOYED,
            job_status=models.App.JobStatus.DEPLOY_PENDING,
        )

        first_response = self.client.post("/platform/fleet/fail-unsettled/")
        pending.refresh_from_db()
        first_error = pending.last_attempt_error
        second_response = self.client.post("/platform/fleet/fail-unsettled/")

        self.assertContains(first_response, "Marked 1 unsettled deployment as failed")
        self.assertContains(second_response, "Marked 0 unsettled deployments as failed")
        self.assertEqual(first_error, fleet_service.RECOVERY_STATUS_MESSAGE)

    def test_per_ha_redeploy_queues_only_the_selected_app(self) -> None:
        failed_app = self._add_failed_app()

        response = self.client.post(f"/platform/fleet/app/{self.app.id}/redeploy/")

        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        failed_app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertEqual(failed_app.job_status, models.App.JobStatus.IDLE)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=self.app, event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
            ).exists()
        )
        self.assertContains(response, "Queued 1 redeployment")

    def test_per_ha_redeploy_allows_a_failed_latest_attempt(self) -> None:
        failed_app = self._add_failed_app()

        response = self.client.post(f"/platform/fleet/app/{failed_app.id}/redeploy/")

        self.assertEqual(response.status_code, 200)
        failed_app.refresh_from_db()
        self.assertEqual(failed_app.job_status, models.App.JobStatus.DEPLOY_PENDING)

    def test_per_ha_redeploy_rejects_a_busy_app(self) -> None:
        self.app.job_status = models.App.JobStatus.DEPLOYING
        self.app.save(update_fields=["job_status", "updated_at"])

        response = self.client.post(f"/platform/fleet/app/{self.app.id}/redeploy/")

        self.assertContains(response, "Queued 0 redeployments")
        self.assertContains(response, fleet_service.SKIP_APP_BUSY)

    def test_per_ha_redeploy_reports_environment_that_becomes_not_ready_during_admission(self) -> None:
        def reject_deploy(*, app: models.App, created_by: models.User) -> None:
            models.Environment.objects.filter(id=app.environment_id).update(status=models.Environment.Status.TEARDOWN_PENDING)
            raise fleet_service.app_job_service.AppJobAdmissionError("Environment is not ready")

        with patch.object(fleet_service.app_job_service, "queue_deploy", side_effect=reject_deploy):
            result = fleet_service.queue_redeploy(app_id=self.app.id, created_by=self.staff)

        self.assertEqual(result.queued_count, 0)
        self.assertEqual(result.skipped_counts, {fleet_service.SKIP_ENVIRONMENT_NOT_READY: 1})

    def test_per_ha_redeploy_button_is_hidden_when_ineligible(self) -> None:
        self.app.live_state = models.App.LiveState.TORN_DOWN
        self.app.save(update_fields=["live_state", "updated_at"])

        response = self.client.get("/platform/fleet/")

        self.assertNotContains(response, f"/platform/fleet/app/{self.app.id}/redeploy/")

    def test_confirmation_shows_failed_checkbox_and_current_counts(self) -> None:
        self._add_failed_app()

        response = self.client.get("/platform/fleet/redeploy-all/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "For 1 entry, the latest deployment succeeded")
        self.assertContains(response, "Include 1 entry whose latest deployment failed")
        self.assertContains(response, 'name="include_failed"')

    def test_redeploy_all_skips_failed_by_default(self) -> None:
        failed_app = self._add_failed_app()

        response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        failed_app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertEqual(failed_app.job_status, models.App.JobStatus.IDLE)
        self.assertContains(response, "Queued 1 redeployment")
        self.assertContains(response, "Failed not included")

    def test_include_failed_queues_each_eligible_app(self) -> None:
        failed_app = self._add_failed_app()

        response = self.client.post("/platform/fleet/redeploy-all/", {"include_failed": "on"})

        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        failed_app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertEqual(failed_app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertContains(response, "Queued 2 redeployments")

    def test_redeploy_all_reports_torn_down_and_non_ready_targets(self) -> None:
        self._make_app(
            slug="retiredagent",
            environment=self.environment,
            live_state=models.App.LiveState.TORN_DOWN,
        )
        draft_environment = self._create_environment(name="Draft", slug="draft", status=models.Environment.Status.PENDING)
        self._make_app(
            slug="draftagent",
            environment=draft_environment,
            live_state=models.App.LiveState.DEPLOYED,
        )
        self._make_app(
            slug="removingagent",
            environment=self.environment,
            live_state=models.App.LiveState.DEPLOYED,
            job_status=models.App.JobStatus.REMOVAL_PENDING,
        )

        response = self.client.post("/platform/fleet/redeploy-all/")

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertContains(response, fleet_service.SKIP_TORN_DOWN)
        self.assertContains(response, fleet_service.SKIP_ENVIRONMENT_NOT_READY)
        self.assertContains(response, fleet_service.SKIP_APP_PENDING_REMOVAL)

    def test_second_submission_does_not_queue_a_duplicate(self) -> None:
        first_response = self.client.post("/platform/fleet/redeploy-all/")
        second_response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertContains(first_response, "Queued 1 redeployment")
        self.assertContains(second_response, "Queued 0 redeployments")
        self.assertContains(second_response, fleet_service.SKIP_APP_BUSY)

    def test_fleet_redeploy_endpoints_hide_from_nonstaff(self) -> None:
        self.client.force_login(self.nonstaff)

        confirm_response = self.client.get("/platform/fleet/redeploy-all/confirm/")
        post_response = self.client.post("/platform/fleet/redeploy-all/")
        per_ha_response = self.client.post(f"/platform/fleet/app/{self.app.id}/redeploy/")
        recovery_confirm_response = self.client.get("/platform/fleet/fail-unsettled/confirm/")
        recovery_response = self.client.post("/platform/fleet/fail-unsettled/")

        self.assertEqual(confirm_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        self.assertEqual(per_ha_response.status_code, 404)
        self.assertEqual(recovery_confirm_response.status_code, 404)
        self.assertEqual(recovery_response.status_code, 404)

    def test_redeploy_all_requires_post(self) -> None:
        all_response = self.client.get("/platform/fleet/redeploy-all/")
        per_ha_response = self.client.get(f"/platform/fleet/app/{self.app.id}/redeploy/")
        recovery_response = self.client.get("/platform/fleet/fail-unsettled/")

        self.assertEqual(all_response.status_code, 405)
        self.assertEqual(per_ha_response.status_code, 405)
        self.assertEqual(recovery_response.status_code, 405)

    def test_worker_claims_one_pending_deploy_per_app(self) -> None:
        self.app.job_status = models.App.JobStatus.DEPLOY_PENDING
        self.app.save(update_fields=["job_status", "updated_at"])

        first = job_worker._claim_pending_app_deployment(label="")
        second = job_worker._claim_pending_app_deployment(label="")

        self.assertIsNotNone(first)
        self.assertEqual(first.id, self.app.id)
        self.assertEqual(first.job_status, models.App.JobStatus.DEPLOYING)
        self.assertIsNone(second)
