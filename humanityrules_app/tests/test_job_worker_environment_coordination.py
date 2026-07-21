"""Tests for environment teardown coordination across worker job types."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models
from humanityrules_app.services import abac_service
from humanityrules_app.services.jobs import environment_operation_gate
from humanityrules_app.services.jobs import job_worker


class TestJobWorkerEnvironmentCoordination(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Coordination Org", slug="coordination-org")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Coordination AWS")
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
            full_name="coordination/hermes",
            clone_url="https://github.com/coordination/hermes.git",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            repository=self.repository,
            name="Coordination Agent",
            slug="coordination-agent",
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        self.user = models.User.objects.create_user(
            username="coordination-user",
            password="pw",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.organization, admin_user=self.user)

    def _create_deployment(self, status: str, suffix: str) -> models.Deployment:
        """Create a deployment attempt in the coordinated environment."""
        return models.Deployment.objects.create(
            app=self.app,
            git_ref="main",
            image_tag=f"coordination-agent-main-{suffix}",
            status=status,
        )

    def _create_permission_request(self, status: str) -> models.AppPermissionRequest:
        """Create a permission request in the coordinated environment."""
        return models.AppPermissionRequest.objects.create(
            app=self.app,
            status=status,
        )

    def _create_removal_job(self, status: str) -> models.AppRemovalJob:
        """Create an app removal job that targets the coordinated app."""
        return models.AppRemovalJob.objects.create(
            organization=self.organization,
            app_id_snapshot=self.app.id,
            app_slug_snapshot=self.app.slug,
            app_name_snapshot=self.app.name,
            workspace_slug_snapshot=self.workspace.slug,
            status=status,
        )

    def test_app_jobs_do_not_start_after_environment_teardown_is_queued(self) -> None:
        self._create_deployment(status=models.Deployment.Status.PENDING, suffix="deploy")
        self._create_deployment(status=models.Deployment.Status.TEARDOWN_PENDING, suffix="teardown")
        self._create_permission_request(status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        deployment = job_worker._claim_pending_app_deployment(label="")
        app_teardown = job_worker._claim_pending_app_deployment_teardown(label="")
        permission_apply = job_worker._claim_pending_permissions_apply(label="")

        self.assertIsNone(deployment)
        self.assertIsNone(app_teardown)
        self.assertIsNone(permission_apply)

    def test_environment_teardown_waits_for_a_running_deployment(self) -> None:
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING, suffix="deploying")
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        first_claim = job_worker._claim_pending_environment_teardown()
        deployment.status = models.Deployment.Status.SUCCEEDED
        deployment.save(update_fields=["status", "updated_at"])
        second_claim = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(first_claim)
        self.assertIsNotNone(second_claim)
        self.assertEqual(second_claim.id, self.environment.id)

    def test_environment_teardown_waits_for_a_running_permission_apply(self) -> None:
        permission_request = self._create_permission_request(status=models.AppPermissionRequest.Status.APPLYING)
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        first_claim = job_worker._claim_pending_environment_teardown()
        permission_request.status = models.AppPermissionRequest.Status.APPLIED
        permission_request.save(update_fields=["status", "updated_at"])
        second_claim = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(first_claim)
        self.assertIsNotNone(second_claim)

    def test_environment_teardown_can_claim_with_only_queued_app_jobs(self) -> None:
        self._create_deployment(status=models.Deployment.Status.PENDING, suffix="pending")
        self._create_permission_request(status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        claimed = job_worker._claim_pending_environment_teardown()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, models.Environment.Status.TEARING_DOWN)

    def test_pending_app_removal_runs_before_environment_teardown(self) -> None:
        removal_job = self._create_removal_job(status=models.AppRemovalJob.Status.PENDING)
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        environment_claim = job_worker._claim_pending_environment_teardown()
        removal_claim = job_worker._claim_pending_app_removal(label="")
        environment_claim_during_removal = job_worker._claim_pending_environment_teardown()
        removal_job.status = models.AppRemovalJob.Status.SUCCEEDED
        removal_job.save(update_fields=["status", "updated_at"])
        environment_claim_after_removal = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(environment_claim)
        self.assertIsNotNone(removal_claim)
        self.assertEqual(removal_claim.id, removal_job.id)
        self.assertIsNone(environment_claim_during_removal)
        self.assertIsNotNone(environment_claim_after_removal)

    def test_app_removal_does_not_start_after_environment_teardown_claim(self) -> None:
        removal_job = self._create_removal_job(status=models.AppRemovalJob.Status.PENDING)
        self.environment.status = models.Environment.Status.TEARING_DOWN
        self.environment.save(update_fields=["status", "updated_at"])

        removal_claim = job_worker._claim_pending_app_removal(label="")

        self.assertIsNone(removal_claim)
        removal_job.refresh_from_db()
        self.assertEqual(removal_job.status, models.AppRemovalJob.Status.PENDING)

    def test_teardown_queue_supersedes_queued_app_work(self) -> None:
        self._create_deployment(status=models.Deployment.Status.PENDING, suffix="pending")
        self._create_permission_request(status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)

        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.environment.refresh_from_db()
        self.assertTrue(result.queued)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)

    def test_teardown_queue_rejects_running_app_work(self) -> None:
        self._create_deployment(status=models.Deployment.Status.DEPLOYING, suffix="deploying")

        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.environment.refresh_from_db()
        self.assertFalse(result.queued)
        self.assertEqual(result.reason, environment_operation_gate.REASON_ACTIVE_APP_OPERATIONS)
        self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_teardown_queue_transitions_a_clean_environment(self) -> None:
        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.environment.refresh_from_db()
        self.assertTrue(result.queued)
        self.assertEqual(result.previous_status, models.Environment.Status.READY)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)
        self.assertEqual(self.environment.status_message, "Teardown queued by test")

    def test_teardown_queue_rejects_provisioning_without_force(self) -> None:
        self.environment.status = models.Environment.Status.PROVISIONING
        self.environment.save(update_fields=["status", "updated_at"])

        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.assertFalse(result.queued)
        self.assertEqual(result.reason, environment_operation_gate.REASON_NOT_TEARDOWNABLE)

    def test_conditional_status_transition_preserves_teardown(self) -> None:
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])

        transitioned = environment_operation_gate.transition_environment_status(
            environment_id=self.environment.id,
            expected_statuses=(models.Environment.Status.ERROR,),
            new_status=models.Environment.Status.PENDING,
            status_message="Retry provisioning",
        )

        self.environment.refresh_from_db()
        self.assertFalse(transitioned)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)

    def test_force_teardown_recovers_stranded_environment_work(self) -> None:
        deployment = self._create_deployment(status=models.Deployment.Status.DEPLOYING, suffix="deploying")
        permission_request = self._create_permission_request(status=models.AppPermissionRequest.Status.APPLYING)
        self.environment.status = models.Environment.Status.PROVISIONING
        self.environment.save(update_fields=["status", "updated_at"])
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "teardown-env",
            "--slug",
            self.environment.slug,
            "--aws-account",
            self.aws_account.name,
            "--force",
            stdout=stdout,
            stderr=stderr,
        )

        self.environment.refresh_from_db()
        deployment.refresh_from_db()
        permission_request.refresh_from_db()
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertEqual(permission_request.status, models.AppPermissionRequest.Status.FAILED)
        self.assertIn("Force recovery: yes", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_force_teardown_does_not_bypass_running_app_removal(self) -> None:
        self._create_removal_job(status=models.AppRemovalJob.Status.RUNNING)
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "teardown-env",
            "--slug",
            self.environment.slug,
            "--aws-account",
            self.aws_account.name,
            "--force",
            stdout=stdout,
            stderr=stderr,
        )

        self.environment.refresh_from_db()
        self.assertEqual(self.environment.status, models.Environment.Status.READY)
        self.assertIn("cannot skip required app cleanup", stderr.getvalue())

    def test_management_command_teardown_rejects_running_app_work(self) -> None:
        self._create_deployment(status=models.Deployment.Status.DEPLOYING, suffix="deploying")
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "teardown-env",
            "--slug",
            self.environment.slug,
            "--aws-account",
            self.aws_account.name,
            stdout=stdout,
            stderr=stderr,
        )

        self.environment.refresh_from_db()
        self.assertEqual(self.environment.status, models.Environment.Status.READY)
        self.assertIn("while an app deployment", stderr.getvalue())

    def test_web_teardown_rejects_running_app_work(self) -> None:
        self._create_deployment(status=models.Deployment.Status.DEPLOYING, suffix="deploying")
        self.client.force_login(self.user)

        response = self.client.post(reverse("environment_teardown", kwargs={"environment_id": self.environment.id}))

        self.environment.refresh_from_db()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_app_removal_queue_is_allowed_while_environment_teardown_is_pending(self) -> None:
        self.environment.status = models.Environment.Status.TEARDOWN_PENDING
        self.environment.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(models.AppRemovalJob.objects.filter(app_id_snapshot=self.app.id).exists())

    def test_app_removal_queue_is_rejected_after_environment_teardown_starts(self) -> None:
        self.environment.status = models.Environment.Status.TEARING_DOWN
        self.environment.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.user)

        response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(response.status_code, 422)
        self.assertFalse(models.AppRemovalJob.objects.filter(app_id_snapshot=self.app.id).exists())
