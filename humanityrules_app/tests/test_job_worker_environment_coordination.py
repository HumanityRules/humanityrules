"""Tests for environment teardown admission against the App job model."""

import uuid
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models
from humanityrules_app.services import abac_service
from humanityrules_app.services.jobs import app_job_service
from humanityrules_app.services.jobs import environment_job_service
from humanityrules_app.services.jobs import job_worker
from humanityrules_app.tests.app_test_factories import make_source_template


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
            source_template=make_source_template(),
            name=slug,
            slug=slug,
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

    def test_teardown_queue_rejects_each_unsettled_app_job(self) -> None:
        unsettled_statuses = (
            models.App.JobStatus.DEPLOY_PENDING,
            models.App.JobStatus.DEPLOYING,
            models.App.JobStatus.TEARDOWN_PENDING,
            models.App.JobStatus.TEARING_DOWN,
            models.App.JobStatus.REMOVAL_PENDING,
            models.App.JobStatus.REMOVING,
        )
        for job_status in unsettled_statuses:
            with self.subTest(job_status=job_status):
                self._set_job_status(app=self.app, job_status=job_status)
                with self.assertRaisesMessage(environment_job_service.EnvironmentJobAdmissionError, "unsettled job"):
                    environment_job_service.queue_teardown(
                        environment_id=self.environment.id,
                        status_message="Teardown queued by test",
                    )
                self.environment.refresh_from_db()
                self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_teardown_queue_rejects_running_permission_apply(self) -> None:
        self._create_permission_request(app=self.app, status=models.AppPermissionRequest.Status.APPLYING)

        with self.assertRaisesMessage(environment_job_service.EnvironmentJobAdmissionError, "permissions update in progress"):
            environment_job_service.queue_teardown(
                environment_id=self.environment.id,
                status_message="Teardown queued by test",
            )

        self.environment.refresh_from_db()
        self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_teardown_queue_transitions_a_clean_environment(self) -> None:
        previous_status = environment_job_service.queue_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
        )

        self.environment.refresh_from_db()
        self.assertEqual(previous_status, models.Environment.Status.READY)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)
        self.assertEqual(self.environment.status_message, "Teardown queued by test")

    def test_teardown_queue_accepts_an_error_environment(self) -> None:
        self._set_environment_status(models.Environment.Status.ERROR)

        previous_status = environment_job_service.queue_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
        )

        self.environment.refresh_from_db()
        self.assertEqual(previous_status, models.Environment.Status.ERROR)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)

    def test_teardown_queue_rejects_provisioning(self) -> None:
        self._set_environment_status(models.Environment.Status.PROVISIONING)

        with self.assertRaisesMessage(environment_job_service.EnvironmentJobAdmissionError, "cannot be torn down"):
            environment_job_service.queue_teardown(
                environment_id=self.environment.id,
                status_message="Teardown queued by test",
            )

    def test_environment_worker_claims_an_admitted_teardown(self) -> None:
        environment_job_service.queue_teardown(
            environment_id=self.environment.id,
            status_message="Teardown queued by test",
        )

        claimed = job_worker._claim_pending_environment_teardown()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, self.environment.id)
        self.assertEqual(claimed.status, models.Environment.Status.TEARING_DOWN)
        self.assertEqual(claimed.status_message, "Claimed by worker")

    def test_environment_worker_claim_sets_provisioning_started_message(self) -> None:
        self._set_environment_status(models.Environment.Status.PENDING)

        claimed = job_worker._claim_pending_environment_provisioning()

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, self.environment.id)
        self.assertEqual(claimed.status, models.Environment.Status.PROVISIONING)
        self.assertEqual(claimed.status_message, "Provisioning started")

    def test_conditional_status_transition_preserves_teardown(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        transitioned = environment_job_service.transition_status(
            environment_id=self.environment.id,
            expected_statuses=(models.Environment.Status.ERROR,),
            new_status=models.Environment.Status.PENDING,
            status_message="Retry provisioning",
        )

        self.environment.refresh_from_db()
        self.assertFalse(transitioned)
        self.assertEqual(self.environment.status, models.Environment.Status.TEARDOWN_PENDING)

    def test_management_command_teardown_rejects_running_app_work(self) -> None:
        self._set_job_status(app=self.app, job_status=models.App.JobStatus.DEPLOYING)
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
        self.assertIn("unsettled job", stderr.getvalue())

    def test_web_teardown_rejects_running_app_work(self) -> None:
        self._set_job_status(app=self.app, job_status=models.App.JobStatus.DEPLOYING)
        self.client.force_login(self.user)

        response = self.client.post(reverse("environment_teardown", kwargs={"environment_id": self.environment.id}))

        self.environment.refresh_from_db()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.environment.status, models.Environment.Status.READY)

    def test_app_removal_queue_is_rejected_after_environment_teardown_is_pending(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)
        self.client.force_login(self.user)

        response = self.client.post(reverse("app_remove", kwargs={"app_slug": self.app.slug}))

        self.assertEqual(response.status_code, 422)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)

    def test_direct_app_deploy_is_rejected_after_environment_teardown_is_pending(self) -> None:
        self._set_environment_status(models.Environment.Status.TEARDOWN_PENDING)

        with self.assertRaisesMessage(app_job_service.AppJobAdmissionError, "not ready for app operations"):
            app_job_service.queue_deploy(app=self.app, created_by=self.user)

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
