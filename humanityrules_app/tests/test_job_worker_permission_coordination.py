"""Tests for serializing permission applies by app and environment."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from humanityrules_app import models
from humanityrules_app.services import permissions_service
from humanityrules_app.services.jobs import job_worker


class TestJobWorkerPermissionCoordination(TestCase):
    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Permission Coordination", slug="permission-coordination")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Permission AWS",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.environment = self._create_environment(name="Production", slug="production")
        self.other_environment = self._create_environment(name="Staging", slug="staging")
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Operations",
            slug="operations",
        )
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="hermes",
            full_name="permission-coordination/hermes",
            clone_url="https://github.com/permission-coordination/hermes.git",
        )
        self.app = self._create_app(name="Permission Agent", slug="permission-agent")
        self.other_app = self._create_app(name="Other Agent", slug="other-agent")

    def _create_environment(self, name: str, slug: str) -> models.Environment:
        """Create a ready environment for worker claims."""
        return models.Environment.objects.create(
            aws_account=self.aws_account,
            name=name,
            slug=slug,
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )

    def _create_app(self, name: str, slug: str) -> models.App:
        """Create an active app on the unlabelled worker."""
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
        )

    def _create_request(self, app: models.App, environment: models.Environment, status: str) -> models.AppPermissionRequest:
        """Create a permission request for a logical apply target."""
        return models.AppPermissionRequest.objects.create(
            app=app,
            environment=environment,
            status=status,
        )

    def test_claim_waits_for_applying_request_on_same_target(self) -> None:
        self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        pending = self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

        claimed = job_worker._claim_pending_permissions_apply(label="")

        pending.refresh_from_db()
        self.assertIsNone(claimed)
        self.assertEqual(pending.status, models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)

    def test_claim_proceeds_after_same_target_apply_finishes(self) -> None:
        applying = self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        pending = self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )
        applying.status = models.AppPermissionRequest.Status.APPLIED
        applying.save(update_fields=["status", "updated_at"])

        claimed = job_worker._claim_pending_permissions_apply(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, pending.id)
        self.assertEqual(claimed.status, models.AppPermissionRequest.Status.APPLYING)

    def test_claim_allows_same_app_in_different_environment(self) -> None:
        self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        pending = self._create_request(
            app=self.app,
            environment=self.other_environment,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

        claimed = job_worker._claim_pending_permissions_apply(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, pending.id)

    def test_claim_allows_different_app_in_same_environment(self) -> None:
        self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )
        pending = self._create_request(
            app=self.other_app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

        claimed = job_worker._claim_pending_permissions_apply(label="")

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, pending.id)

    def test_database_rejects_two_applying_requests_for_same_target(self) -> None:
        self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self._create_request(
                app=self.app,
                environment=self.environment,
                status=models.AppPermissionRequest.Status.APPLYING,
            )

    def test_approve_does_not_move_applying_request_backward(self) -> None:
        applying = self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPLYING,
        )

        approved = permissions_service.approve(app_permission_request=applying)

        applying.refresh_from_db()
        self.assertFalse(approved)
        self.assertEqual(applying.status, models.AppPermissionRequest.Status.APPLYING)

    def test_approve_is_idempotent_while_request_is_pending(self) -> None:
        pending = self._create_request(
            app=self.app,
            environment=self.environment,
            status=models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY,
        )

        approved = permissions_service.approve(app_permission_request=pending)

        pending.refresh_from_db()
        self.assertTrue(approved)
        self.assertEqual(pending.status, models.AppPermissionRequest.Status.APPROVED_PENDING_APPLY)
