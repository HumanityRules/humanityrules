"""Tests for staff-only fleet deployment actions."""

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
        self.app = self._create_app(
            name="Fleet Agent",
            slug="fleetagent",
            status=models.App.Status.ACTIVE,
            environment=self.environment,
        )
        self.succeeded_source = self._create_deployment(
            app=self.app,
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

    def _create_app(self, name: str, slug: str, status: str, environment: models.Environment) -> models.App:
        """Create an app in the shared test workspace."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=environment,
            repository=self.repository,
            name=name,
            slug=slug,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
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

    def _create_deployment(self, app: models.App, status: str, suffix: str) -> models.Deployment:
        """Create one deployment attempt for an app."""
        return models.Deployment.objects.create(
            app=app,
            git_ref="main",
            image_tag=f"{app.slug}-main-{suffix}",
            status=status,
        )

    def _add_failed_app(self) -> models.Deployment:
        """Add a second app, in its own ready environment, whose latest deployment failed."""
        environment = self._create_environment(name="Production", slug="production", status=models.Environment.Status.READY)
        app = self._create_app(
            name="Failed Agent",
            slug="failedagent",
            status=models.App.Status.ACTIVE,
            environment=environment,
        )
        return self._create_deployment(
            app=app,
            status=models.Deployment.Status.FAILED,
            suffix="production",
        )

    def test_staff_fleet_page_shows_redeploy_all_button(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Redeploy all")
        self.assertContains(response, "/platform/fleet/redeploy-all/confirm/")
        self.assertContains(response, f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")

    def test_staff_fleet_page_shows_fail_stuck_deployments_button(self) -> None:
        response = self.client.get("/platform/fleet/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fail stuck deployments")
        self.assertContains(response, "/platform/fleet/fail-unsettled/confirm/")

    def test_fail_unsettled_confirmation_shows_current_count(self) -> None:
        self._create_deployment(
            app=self.app,
            status=models.Deployment.Status.PENDING,
            suffix="pending",
        )

        response = self.client.get("/platform/fleet/fail-unsettled/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mark 1 unfinished deployment as failed", html=False)
        self.assertContains(response, "/platform/fleet/fail-unsettled/")

    def test_fail_unsettled_marks_every_unsettled_status_failed(self) -> None:
        unsettled_statuses = (
            models.Deployment.Status.PENDING,
            models.Deployment.Status.DEPLOYING,
            models.Deployment.Status.TEARDOWN_PENDING,
            models.Deployment.Status.TEARING_DOWN,
        )
        unsettled_deployments = [
            self._create_deployment(
                app=self.app,
                status=status,
                suffix=f"unsettled-{index}",
            )
            for index, status in enumerate(unsettled_statuses)
        ]
        settled_deployments = [
            self.succeeded_source,
            self._create_deployment(
                app=self.app,
                status=models.Deployment.Status.FAILED,
                suffix="failed",
            ),
            self._create_deployment(
                app=self.app,
                status=models.Deployment.Status.TORN_DOWN,
                suffix="torn-down",
            ),
        ]
        original_settled_statuses = {deployment.id: deployment.status for deployment in settled_deployments}

        response = self.client.post("/platform/fleet/fail-unsettled/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Marked {len(unsettled_deployments)} unsettled deployments as failed")
        for deployment in unsettled_deployments:
            deployment.refresh_from_db()
            self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
            self.assertEqual(deployment.status_message, fleet_service.RECOVERY_STATUS_MESSAGE)
            self.assertIsNotNone(deployment.completed_at)
        for deployment in settled_deployments:
            deployment.refresh_from_db()
            self.assertEqual(deployment.status, original_settled_statuses[deployment.id])

    def test_fail_unsettled_is_safe_to_repeat(self) -> None:
        pending = self._create_deployment(
            app=self.app,
            status=models.Deployment.Status.PENDING,
            suffix="pending-repeat",
        )

        first_response = self.client.post("/platform/fleet/fail-unsettled/")
        pending.refresh_from_db()
        first_completed_at = pending.completed_at
        second_response = self.client.post("/platform/fleet/fail-unsettled/")
        pending.refresh_from_db()

        self.assertContains(first_response, "Marked 1 unsettled deployment as failed")
        self.assertContains(second_response, "Marked 0 unsettled deployments as failed")
        self.assertEqual(pending.completed_at, first_completed_at)

    def test_per_ha_redeploy_queues_only_the_selected_app(self) -> None:
        failed_source = self._add_failed_app()

        response = self.client.post(f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")

        self.assertEqual(response.status_code, 200)
        pending = models.Deployment.objects.get(status=models.Deployment.Status.PENDING)
        self.assertEqual(pending.app, self.app)
        self.assertEqual(pending.created_by, self.staff)
        self.assertEqual(pending.status_message, "Fleet redeploy triggered via web UI")
        self.assertEqual(models.Deployment.objects.filter(app=failed_source.app).count(), 1)
        self.assertContains(response, "Queued 1 redeployment")

    def test_per_ha_redeploy_allows_a_failed_latest_deployment(self) -> None:
        failed_source = self._add_failed_app()

        response = self.client.post(f"/platform/fleet/deployment/{failed_source.id}/redeploy/")

        self.assertEqual(response.status_code, 200)
        pending = models.Deployment.objects.get(status=models.Deployment.Status.PENDING)
        self.assertEqual(pending.app_id, failed_source.app_id)

    def test_per_ha_redeploy_rejects_a_superseded_deployment(self) -> None:
        newer_source = models.Deployment.objects.create(
            app=self.app,
            git_ref="main",
            image_tag="fleetagent-main-newer",
            status=models.Deployment.Status.SUCCEEDED,
        )

        page_response = self.client.get("/platform/fleet/")
        post_response = self.client.post(f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")

        self.assertContains(page_response, f"/platform/fleet/deployment/{newer_source.id}/redeploy/")
        self.assertNotContains(page_response, f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")
        self.assertEqual(models.Deployment.objects.filter(status=models.Deployment.Status.PENDING).count(), 0)
        self.assertContains(post_response, "Newer deployment exists")

    def test_per_ha_redeploy_button_is_hidden_when_ineligible(self) -> None:
        self.succeeded_source.status = models.Deployment.Status.TORN_DOWN
        self.succeeded_source.save(update_fields=["status", "updated_at"])

        response = self.client.get("/platform/fleet/")

        self.assertNotContains(response, f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")

    def test_confirmation_shows_failed_checkbox_and_current_counts(self) -> None:
        self._add_failed_app()

        response = self.client.get("/platform/fleet/redeploy-all/confirm/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "For 1 entry, the latest deployment succeeded")
        self.assertContains(response, "Include 1 entry whose latest deployment failed")
        self.assertContains(response, 'name="include_failed"')

    def test_redeploy_all_skips_failed_by_default(self) -> None:
        failed_source = self._add_failed_app()

        response = self.client.post("/platform/fleet/redeploy-all/")

        self.assertEqual(response.status_code, 200)
        pending = models.Deployment.objects.filter(status=models.Deployment.Status.PENDING)
        self.assertEqual(pending.count(), 1)
        self.assertEqual(pending.get().app, self.app)
        self.assertEqual(models.Deployment.objects.filter(app=failed_source.app).count(), 1)
        self.assertContains(response, "Queued 1 redeployment")
        self.assertContains(response, "Failed not included")

    def test_include_failed_queues_each_app_with_unique_tags(self) -> None:
        failed_source = self._add_failed_app()

        response = self.client.post("/platform/fleet/redeploy-all/", {"include_failed": "on"})

        self.assertEqual(response.status_code, 200)
        pending = list(models.Deployment.objects.filter(status=models.Deployment.Status.PENDING).order_by("app__slug"))
        self.assertEqual(len(pending), 2)
        self.assertEqual({deployment.app_id for deployment in pending}, {self.app.id, failed_source.app_id})
        self.assertEqual({deployment.created_by_id for deployment in pending}, {self.staff.id})
        self.assertEqual({deployment.status_message for deployment in pending}, {"Fleet redeploy all triggered via web UI"})
        self.assertEqual(len({deployment.image_tag for deployment in pending}), 2)
        self.assertContains(response, "Queued 2 redeployments")

    def test_redeploy_all_reports_torn_down_and_non_ready_targets(self) -> None:
        torn_down_app = self._create_app(
            name="Retired Agent",
            slug="retiredagent",
            status=models.App.Status.ACTIVE,
            environment=self.environment,
        )
        self._create_deployment(
            app=torn_down_app,
            status=models.Deployment.Status.TORN_DOWN,
            suffix="retired",
        )
        draft_environment = self._create_environment(
            name="Draft",
            slug="draft",
            status=models.Environment.Status.PENDING,
        )
        draft_app = self._create_app(
            name="Draft Agent",
            slug="draftagent",
            status=models.App.Status.ACTIVE,
            environment=draft_environment,
        )
        self._create_deployment(
            app=draft_app,
            status=models.Deployment.Status.SUCCEEDED,
            suffix="draft",
        )
        pending_removal_app = self._create_app(
            name="Removing Agent",
            slug="removingagent",
            status=models.App.Status.PENDING_REMOVAL,
            environment=self.environment,
        )
        self._create_deployment(
            app=pending_removal_app,
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
        self.assertContains(second_response, "Deployment or teardown already in progress")

    def test_fleet_redeploy_endpoints_hide_from_nonstaff(self) -> None:
        self.client.force_login(self.nonstaff)

        confirm_response = self.client.get("/platform/fleet/redeploy-all/confirm/")
        post_response = self.client.post("/platform/fleet/redeploy-all/")
        per_ha_response = self.client.post(f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")
        recovery_confirm_response = self.client.get("/platform/fleet/fail-unsettled/confirm/")
        recovery_response = self.client.post("/platform/fleet/fail-unsettled/")

        self.assertEqual(confirm_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)
        self.assertEqual(per_ha_response.status_code, 404)
        self.assertEqual(recovery_confirm_response.status_code, 404)
        self.assertEqual(recovery_response.status_code, 404)

    def test_redeploy_all_requires_post(self) -> None:
        all_response = self.client.get("/platform/fleet/redeploy-all/")
        per_ha_response = self.client.get(f"/platform/fleet/deployment/{self.succeeded_source.id}/redeploy/")
        recovery_response = self.client.get("/platform/fleet/fail-unsettled/")

        self.assertEqual(all_response.status_code, 405)
        self.assertEqual(per_ha_response.status_code, 405)
        self.assertEqual(recovery_response.status_code, 405)

    def test_worker_serializes_pending_deployments_for_the_same_app(self) -> None:
        self._create_deployment(
            app=self.app,
            status=models.Deployment.Status.PENDING,
            suffix="queued-1",
        )
        self._create_deployment(
            app=self.app,
            status=models.Deployment.Status.PENDING,
            suffix="queued-2",
        )

        first = job_worker._claim_pending_app_deployment(label="")
        second = job_worker._claim_pending_app_deployment(label="")

        self.assertIsNotNone(first)
        self.assertIsNone(second)

        first.status = models.Deployment.Status.SUCCEEDED
        first.save(update_fields=["status", "updated_at"])
        next_deployment = job_worker._claim_pending_app_deployment(label="")
        self.assertIsNotNone(next_deployment)
        self.assertEqual(next_deployment.app_id, self.app.id)
