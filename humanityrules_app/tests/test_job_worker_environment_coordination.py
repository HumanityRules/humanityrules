"""Tests for environment teardown coordination against the App job model."""

import uuid
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
        self.app = self._make_app(slug="coordinationagent", job_status=models.App.JobStatus.IDLE)
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

    def _make_app(self, slug: str, job_status: str) -> models.App:
        """Create an app fixed in `job_status` with an open attempt id."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            repository=self.repository,
            name=slug,
            slug=slug,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8787,
            health_check_path="/health",
            cpu=256,
            memory=512,
            job_status=job_status,
            last_attempt_id=uuid.uuid7(),
        )

    def _set_job_status(self, app: models.App, job_status: str) -> None:
        app.job_status = job_status
        app.save(update_fields=["job_status", "updated_at"])

    def _create_permission_request(self, app: models.App, status: str) -> models.AppPermissionRequest:
        return models.AppPermissionRequest.objects.create(app=app, status=status)

    def _set_environment_status(self, status: str) -> None:
        self.environment.status = status
        self.environment.save(update_fields=["status", "updated_at"])

    def test_app_jobs_do_not_start_after_environment_teardown_is_queued(self) -> None:
        self._make_app(slug="deploypending", job_status=models.App.JobStatus.DEPLOY_PENDING)
        self._make_app(slug="teardownpending", job_status=models.App.JobStatus.TEARDOWN_PENDING)
        self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        deployment = job_worker._claim_pending_app_deployment(label="")
        app_teardown = job_worker._claim_pending_app_deployment_teardown(label="")
        permission_apply = job_worker._claim_pending_permissions_apply(label="")

        self.assertIsNone(deployment)
        self.assertIsNone(app_teardown)
        self.assertIsNone(permission_apply)

    def test_environment_teardown_waits_for_a_running_deployment(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.DEPLOYING)
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        first_claim = job_worker._claim_pending_environment_teardown()
        self._set_job_status(self.app, models.App.JobStatus.IDLE)
        second_claim = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(first_claim)
        self.assertIsNotNone(second_claim)
        self.assertEqual(second_claim.id, self.environment.id)

    def test_environment_teardown_waits_for_a_running_permission_apply(self) -> None:
        permission_request = self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPLYING)
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        first_claim = job_worker._claim_pending_environment_teardown()
        permission_request.status = models.AppPermissionRequest.Status.APPLIED
        permission_request.save(update_fields=["status", "updated_at"])
        second_claim = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(first_claim)
        self.assertIsNotNone(second_claim)

    def test_environment_teardown_can_claim_with_only_queued_app_jobs(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.DEPLOY_PENDING)
        self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        claimed = job_worker._claim_pending_environment_teardown()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, models.Environment.Status.TEARING_DOWN)

    def test_pending_app_removal_runs_before_environment_teardown(self) -> None:
        app_id = self.app.id
        self._set_job_status(self.app, models.App.JobStatus.REMOVAL_PENDING)
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        environment_claim = job_worker._claim_pending_environment_teardown()
        removal_claim = job_worker._claim_pending_app_removal(label="")
        environment_claim_during_removal = job_worker._claim_pending_environment_teardown()

        self.assertIsNone(environment_claim)
        self.assertIsNotNone(removal_claim)
        self.assertEqual(removal_claim.id, app_id)
        self.assertEqual(removal_claim.job_status, models.App.JobStatus.REMOVING)
        self.assertIsNone(environment_claim_during_removal)

        # A completed removal deletes the app row; the env teardown can then claim.
        self.app.delete()
        environment_claim_after_removal = job_worker._claim_pending_environment_teardown()
        self.assertIsNotNone(environment_claim_after_removal)

    def test_app_removal_does_not_start_after_environment_teardown_claim(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.REMOVAL_PENDING)
        self._set_environment_status(models.Environment.Status.TEARING_DOWN)

        removal_claim = job_worker._claim_pending_app_removal(label="")

        self.assertIsNone(removal_claim)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)

    def test_teardown_queue_supersedes_queued_app_work(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.DEPLOY_PENDING)
        self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)

        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.environment.refresh_from_db()
        self.assertTrue(result.queued)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)

    def test_teardown_queue_rejects_running_app_work(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.DEPLOYING)

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
        self._set_environment_status(models.Environment.Status.PROVISIONING)

        result = environment_operation_gate.queue_environment_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
            force=False,
        )

        self.assertFalse(result.queued)
        self.assertEqual(result.reason, environment_operation_gate.REASON_NOT_TEARDOWNABLE)

    def test_conditional_status_transition_preserves_teardown(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

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
        self._set_job_status(self.app, models.App.JobStatus.DEPLOYING)
        permission_request = self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPLYING)
        self._set_environment_status(models.Environment.Status.PROVISIONING)
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
        self.app.refresh_from_db()
        permission_request.refresh_from_db()
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
        self.assertEqual(self.app.last_attempt_error, environment_operation_gate.FORCED_FAILURE_MESSAGE)
        self.assertEqual(permission_request.status, models.AppPermissionRequest.Status.FAILED)
        self.assertIn("Force recovery: yes", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_force_teardown_does_not_bypass_running_app_removal(self) -> None:
        self._set_job_status(self.app, models.App.JobStatus.REMOVING)
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
        self._set_job_status(self.app, models.App.JobStatus.DEPLOYING)
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
        self._set_job_status(self.app, models.App.JobStatus.DEPLOYING)
        self.client.force_login(self.user)

        response = self.client.post(reverse("environment_teardown", kwargs={"environment_id": self.environment.id}))

        self.environment.refresh_from_db()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_app_removal_queue_is_allowed_while_environment_teardown_is_pending(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)
        self.client.force_login(self.user)

        response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(response.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.REMOVAL_PENDING)

    def test_app_removal_queue_is_rejected_after_environment_teardown_starts(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARING_DOWN)
        self.client.force_login(self.user)

        response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(response.status_code, 422)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
