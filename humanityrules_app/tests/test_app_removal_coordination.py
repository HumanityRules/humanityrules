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
from humanityrules_app.tests.app_test_factories import make_source_template


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
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=make_source_template(),
            name="Removal Agent",
            slug="removalagent",
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

    def test_cli_enqueue_rejects_a_stale_second_removal_attempt(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        command = humr_control.Command(stdout=stdout, stderr=stderr)

        command._queue_app_removal(app=self.app)
        command._queue_app_removal(app=self.app)

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)
        self.assertEqual(self._removal_started_count(), 1)
        self.assertIn("has a job in progress", stderr.getvalue())

    def test_worker_claims_pending_removal_into_removing(self) -> None:
        app_job_service.queue_removal(app=self.app, created_by=self.user, label=None)

        claimed = job_worker._claim_pending_app_removal(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, self.app.id)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVING)

    def _claim_into_removing(self) -> None:
        """Put the app where the worker would leave it, so run_removal can be driven directly."""
        app_job_service.queue_removal(app=self.app, created_by=self.user, label=None)
        self.app.refresh_from_db()
        self.app.job_status = models.App.JobStatus.REMOVING
        self.app.save(update_fields=["job_status", "updated_at"])

    def test_removal_is_admitted_while_infra_may_exist(self) -> None:
        self.app.may_have_infra = True
        self.app.save(update_fields=["may_have_infra", "updated_at"])

        app_job_service.queue_removal(app=self.app, created_by=self.user, label=None)

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)
        self.assertEqual(self._removal_started_count(), 1)

    def test_removal_of_a_deployed_app_tears_down_then_deletes_the_app(self) -> None:
        self.app.may_have_infra = True
        self.app.live_state = models.App.LiveState.DEPLOYED
        self.app.service_url = "https://removalagent.example.com"
        self.app.save(update_fields=["may_have_infra", "live_state", "service_url", "updated_at"])
        self._claim_into_removing()

        with patch.object(app_remove_executor.app_deployment_teardown_executor, "teardown_infra", return_value=True) as teardown_mock, \
             patch.object(app_remove_executor, "purge_app_namespace_data", return_value=(True, "ok")):
            success = app_remove_executor.run_removal(app_id=str(self.app.id))

        self.assertTrue(success)
        teardown_mock.assert_called_once()
        self.assertFalse(models.App.objects.filter(id=self.app.id).exists())

    def test_removal_always_purges_data_and_deletes_app_scoped_policies(self) -> None:
        policy = models.Policy.objects.create(
            organization=self.organization,
            name="Removal agent access",
            resource_type=models.Policy.ResourceType.APP,
            identity_conditions=[{"key": "role", "value": "member"}],
            resource_conditions=[{"key": "app-name", "value": self.app.slug}],
            actions=["app:use"],
        )
        other_policy = models.Policy.objects.create(
            organization=self.organization,
            name="Other agent access",
            resource_type=models.Policy.ResourceType.APP,
            identity_conditions=[{"key": "role", "value": "member"}],
            resource_conditions=[{"key": "app-name", "value": "otheragent"}],
            actions=["app:use"],
        )
        self._claim_into_removing()

        with patch.object(app_remove_executor, "purge_app_namespace_data", return_value=(True, "ok")) as purge_mock:
            success = app_remove_executor.run_removal(app_id=str(self.app.id))

        self.assertTrue(success)
        purge_mock.assert_called_once()
        self.assertFalse(models.App.objects.filter(id=self.app.id).exists())
        self.assertFalse(models.Policy.objects.filter(id=policy.id).exists())
        self.assertTrue(models.Policy.objects.filter(id=other_policy.id).exists())

    def test_removal_keeps_the_owners_integration_credentials(self) -> None:
        """Pinned by design: the rows are per-user, so a re-created app finds them connected."""
        credential = models.IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.environment,
            app_slug=self.app.slug,
            provider="google",
        )
        self._claim_into_removing()

        with patch.object(app_remove_executor, "purge_app_namespace_data", return_value=(True, "ok")):
            success = app_remove_executor.run_removal(app_id=str(self.app.id))

        self.assertTrue(success)
        self.assertFalse(models.App.objects.filter(id=self.app.id).exists())
        self.assertTrue(models.IntegrationUserCredential.objects.filter(id=credential.id).exists())
