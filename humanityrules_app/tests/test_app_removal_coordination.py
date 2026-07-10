"""Tests for serializing app-removal enqueue and worker state."""

from io import StringIO
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models
from humanityrules_app.management.commands import humr_control
from humanityrules_app.services import abac_service
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
            repository=self.repository,
            name="Removal Agent",
            slug="removal-agent",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8787,
            health_check_path="/health",
        )
        models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
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

    def _create_removal_job(self, status: str) -> models.AppRemovalJob:
        """Create a removal job for the fixture App."""
        return models.AppRemovalJob.objects.create(
            organization=self.organization,
            app_id_snapshot=self.app.id,
            app_slug_snapshot=self.app.slug,
            app_name_snapshot=self.app.name,
            workspace_slug_snapshot=self.workspace.slug,
            status=status,
        )

    def test_web_enqueue_rechecks_stale_app_after_lock(self) -> None:
        stale_app = self.app
        self.client.force_login(self.user)
        first_response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        with patch("humanityrules_app.views.apps._get_app_for_user", return_value=stale_app):
            second_response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 422)
        self.assertEqual(models.AppRemovalJob.objects.filter(app_id_snapshot=self.app.id).count(), 1)

    def test_cli_enqueue_rechecks_stale_app_after_lock(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        command = humr_control.Command(stdout=stdout, stderr=stderr)

        command._queue_app_removal(
            app=self.app,
            delete_secrets=False,
            delete_persistent_data=False,
            delete_policies=False,
        )
        command._queue_app_removal(
            app=self.app,
            delete_secrets=False,
            delete_persistent_data=False,
            delete_policies=False,
        )

        self.assertEqual(models.AppRemovalJob.objects.filter(app_id_snapshot=self.app.id).count(), 1)
        self.assertIn("already pending removal", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_database_rejects_two_active_removals_for_same_app(self) -> None:
        self._create_removal_job(status=models.AppRemovalJob.Status.PENDING)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_removal_job(status=models.AppRemovalJob.Status.RUNNING)

    def test_database_allows_new_removal_after_terminal_job(self) -> None:
        self._create_removal_job(status=models.AppRemovalJob.Status.SUCCEEDED)

        pending = self._create_removal_job(status=models.AppRemovalJob.Status.PENDING)

        self.assertEqual(pending.status, models.AppRemovalJob.Status.PENDING)

    def test_worker_rejects_job_with_mismatched_organization(self) -> None:
        other_organization = models.Organization.objects.create(name="Other Removal Org", slug="other-removal-org")
        job = models.AppRemovalJob.objects.create(
            organization=other_organization,
            app_id_snapshot=self.app.id,
            app_slug_snapshot=self.app.slug,
            app_name_snapshot=self.app.name,
            workspace_slug_snapshot=self.workspace.slug,
        )

        claimed = job_worker._claim_pending_app_removal(label="")

        job.refresh_from_db()
        self.assertIsNone(claimed)
        self.assertEqual(job.status, models.AppRemovalJob.Status.PENDING)
