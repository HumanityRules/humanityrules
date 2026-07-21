"""Tests for app deployment executor behavior."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

import humanityrules_app.models as models
import humanityrules_app.services.jobs.app_deployment_debug_simulator as app_deployment_debug_simulator
import humanityrules_app.services.jobs.app_deployment_executor as app_deployment_executor
from humanityrules_app.services import sandbox_service


class TestAppDeploymentExecutor(TestCase):
    """Verify deployment executor behavior for local debug mode."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Deploy Org", slug="deploy-org")
        self.user = models.User.objects.create_user(
            username="deploy-user",
            password="x",
            current_organization=self.organization,
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Deploy AWS")
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="repo",
            full_name="org/repo",
            default_branch="main",
            clone_url="https://github.com/org/repo.git",
        )
        self.workspace = models.Workspace.objects.create(
            organization=self.organization,
            name="Engineering",
            slug="engineering",
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            repository=self.repository,
            name="MyApp",
            slug="myapp",
            build_strategy="dockerfile",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            containers=[{"name": "myapp", "environment_variables": [], "app_secrets": {}}],
        )

    def _create_pending_deployment(self) -> models.Deployment:
        """Build the PENDING deployment row the deploy path would have produced."""
        return models.Deployment.objects.create(
            app=self.app,
            git_ref="main",
            image_tag="myapp-main-20260720",
            status=models.Deployment.Status.PENDING,
            status_message="Deployment queued",
            created_by=self.user,
        )

    @override_settings(HUMR_DEBUG_DEPLOYMENTS=True, HUMR_RUN_JOB_WORKER=True)
    def test_run_deployment_debug_mode_succeeds_without_external_calls(self) -> None:
        deployment_row = self._create_pending_deployment()

        with (
            patch("humanityrules_app.services.jobs.app_deployment_debug_simulator.time.sleep") as sleep_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor.repo_service.clone_repository") as clone_repository_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor.repo_service.cleanup_repository") as cleanup_repository_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_app_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor._get_aws_session") as get_aws_session_mock,
        ):
            success = app_deployment_executor.run_deployment(deployment_id=str(deployment_row.id))

        self.assertTrue(success)
        sleep_mock.assert_called_once_with(
            app_deployment_debug_simulator.DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS,
        )
        clone_repository_mock.assert_not_called()
        cleanup_repository_mock.assert_not_called()
        deploy_app_mock.assert_not_called()
        get_aws_session_mock.assert_not_called()

        deployment = models.Deployment.objects.get(id=deployment_row.id)

        self.assertEqual(deployment.status, models.Deployment.Status.SUCCEEDED)
        self.assertEqual(deployment.status_message, "Debug deployment completed successfully")
        self.assertEqual(deployment.service_url, "https://myapp.example.com")
        self.assertEqual(deployment.alb_dns, "myapp-staging.debug-alb.local")
        self.assertIsNotNone(deployment.started_at)
        self.assertIsNotNone(deployment.completed_at)

        log_messages = list(
            models.DeploymentLog.objects.filter(deployment=deployment)
            .order_by("created_at")
            .values_list("message", flat=True)
        )
        self.assertTrue(
            any("skipping repository clone and AWS calls" in message for message in log_messages)
        )

    def test_run_deployment_refuses_cross_tenant_row(self) -> None:
        """A Deployment whose app points at another org's environment must be refused."""
        other_org = models.Organization.objects.create(name="Other Org", slug="other-org")
        other_aws_account = models.AWSAccount.objects.create(organization=other_org, name="Other AWS")
        other_env = models.Environment.objects.create(
            aws_account=other_aws_account,
            name="Other Staging",
            slug="other-staging",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="other.example.com",
        )
        cross_tenant_app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=other_env,
            repository=self.repository,
            name="CrossTenant",
            slug="crosstenant",
            build_strategy="dockerfile",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            created_by=self.user,
        )
        deployment = models.Deployment.objects.create(
            app=cross_tenant_app,
            git_ref="main",
            git_commit_sha="",
            git_commit_message="",
            image_tag="crosstenant-test",
            status=models.Deployment.Status.PENDING,
            status_message="",
            created_by=self.user,
        )

        with patch("humanityrules_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_mock:
            success = app_deployment_executor.run_deployment(deployment_id=str(deployment.id))

        self.assertFalse(success)
        deploy_mock.assert_not_called()

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertIn("Refused", deployment.status_message)

    def test_sandbox_slug_reservation_rejects_cross_org_slug(self) -> None:
        """A slug another org already holds in the shared sandbox is rejected by the slug
        reservation before any Deployment is created. Every deploy path (template and CLI)
        reserves via this same aclaim_sandbox_app_slug."""
        self.aws_account.is_humr_sandbox = True
        self.aws_account.save(update_fields=["is_humr_sandbox"])
        other_org = models.Organization.objects.create(name="Other Sandbox Org", slug="other-sb")
        models.SandboxSlugClaim.objects.create(slug=self.app.slug, organization=other_org)

        with self.assertRaises(ValueError):
            async_to_sync(sandbox_service.aclaim_sandbox_app_slug)(
                app_slug=self.app.slug,
                organization_id=self.app.organization_id,
                environment=self.environment,
            )

        # Rejected before creating a Deployment row for this org's app.
        self.assertFalse(models.Deployment.objects.filter(app=self.app).exists())
