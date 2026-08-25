"""Tests for app deployment executor behavior against the App job model."""

from unittest.mock import patch

from django.test import TestCase, override_settings

import humanityrules_app.models as models
import humanityrules_app.services.jobs.app_deployment_debug_simulator as app_deployment_debug_simulator
import humanityrules_app.services.jobs.app_deployment_executor as app_deployment_executor
from humanityrules_app.services import sandbox_service
from humanityrules_app.services.jobs import app_job_service
from humanityrules_app.tests.app_test_factories import make_source_template


class TestAppDeploymentExecutor(TestCase):
    """Verify how the executor settles a claimed deploy attempt."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Deploy Org", slug="deploy-org")
        self.user = models.User.objects.create_user(
            username="deploy-user",
            password="x",
            current_organization=self.organization,
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Deploy AWS")
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
            source_template=make_source_template(),
            name="MyApp",
            slug="myapp",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            containers=[{"name": "myapp", "environment_variables": [], "app_secrets": {}}],
        )

    def _claim_deploy(self, app: models.App) -> None:
        """Move an idle app through the queue + worker-claim transitions a deploy executor expects."""
        app_job_service.queue_deploy(app=app, created_by=self.user)
        app.job_status = models.App.JobStatus.DEPLOYING
        app.may_have_infra = True
        app.save(update_fields=["job_status", "may_have_infra", "updated_at"])

    @override_settings(HUMR_DEBUG_DEPLOYMENTS=True, HUMR_RUN_JOB_WORKER=True)
    def test_run_deployment_debug_mode_succeeds_without_external_calls(self) -> None:
        self._claim_deploy(app=self.app)

        with (
            patch("humanityrules_app.services.jobs.app_deployment_debug_simulator.time.sleep") as sleep_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_app_mock,
            patch("humanityrules_app.services.jobs.app_deployment_executor._get_aws_session") as get_aws_session_mock,
        ):
            success = app_deployment_executor.run_deployment(app_id=str(self.app.id))

        self.assertTrue(success)
        sleep_mock.assert_called_once_with(
            app_deployment_debug_simulator.DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS,
        )
        deploy_app_mock.assert_not_called()
        get_aws_session_mock.assert_not_called()

        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
        self.assertEqual(self.app.live_state, models.App.LiveState.DEPLOYED)
        self.assertEqual(self.app.service_url, "https://myapp.example.com")
        self.assertEqual(self.app.alb_dns, "myapp-staging.debug-alb.local")
        self.assertEqual(self.app.last_attempt_error, models.App.LastAttemptError.NONE)
        self.assertEqual(self.app.last_attempt_error_text, "")
        self.assertIsNotNone(self.app.last_deployed_at)

        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=self.app,
                attempt_id=self.app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.DEPLOY_SUCCEEDED,
            ).exists()
        )
        log_messages = list(
            models.DeploymentLog.objects.filter(app=self.app, attempt_id=self.app.last_attempt_id)
            .order_by("created_at")
            .values_list("message", flat=True)
        )
        self.assertTrue(
            any("skipping repository clone and AWS calls" in message for message in log_messages)
        )

    def test_failed_deploy_preserves_the_live_serving_state(self) -> None:
        """A redeploy that fails must not clobber the live_state/service_url of the running app."""
        self.app.live_state = models.App.LiveState.DEPLOYED
        self.app.service_url = "https://myapp.example.com"
        self.app.alb_dns = "myapp-staging.alb.local"
        self.app.last_deployed_at = None
        self.app.save(update_fields=["live_state", "service_url", "alb_dns", "updated_at"])
        self._claim_deploy(app=self.app)

        deploy_result = app_deployment_executor.infra_customer.deploy_app.DeployResult(
            success=False,
            service_url="",
            alb_dns="",
            error="stack rollback",
            image_hashes={},
        )
        with (
            patch("humanityrules_app.services.jobs.app_deployment_executor._get_aws_session"),
            patch("humanityrules_app.services.jobs.app_deployment_executor.app_config_builder.build_app_config_from_app"),
            patch("humanityrules_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy", return_value=deploy_result),
        ):
            success = app_deployment_executor.run_deployment(app_id=str(self.app.id))

        self.assertFalse(success)
        self.app.refresh_from_db()
        self.assertEqual(self.app.job_status, models.App.JobStatus.IDLE)
        # Serving state of the previously-deployed app is untouched.
        self.assertEqual(self.app.live_state, models.App.LiveState.DEPLOYED)
        self.assertEqual(self.app.service_url, "https://myapp.example.com")
        self.assertEqual(self.app.alb_dns, "myapp-staging.alb.local")
        # The failed attempt is recorded on the app and as an event.
        self.assertEqual(self.app.last_attempt_error, models.App.LastAttemptError.DEPLOY_FAILED)
        self.assertEqual(self.app.last_attempt_error_text, "stack rollback")
        # A failed deploy may have created infra, so the flag stays set.
        self.assertTrue(self.app.may_have_infra)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=self.app,
                attempt_id=self.app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.DEPLOY_FAILED,
            ).exists()
        )

    def test_run_deployment_refuses_cross_tenant_app(self) -> None:
        """An app whose environment belongs to another org must be refused before any AWS call."""
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
            source_template=make_source_template(),
            name="CrossTenant",
            slug="crosstenant",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            created_by=self.user,
        )
        self._claim_deploy(app=cross_tenant_app)

        with patch("humanityrules_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_mock:
            success = app_deployment_executor.run_deployment(app_id=str(cross_tenant_app.id))

        self.assertFalse(success)
        deploy_mock.assert_not_called()

        cross_tenant_app.refresh_from_db()
        self.assertEqual(cross_tenant_app.job_status, models.App.JobStatus.IDLE)
        self.assertEqual(cross_tenant_app.last_attempt_error, models.App.LastAttemptError.DEPLOY_FAILED)
        self.assertIn("Refused", cross_tenant_app.last_attempt_error_text)

    def test_sandbox_slug_reservation_rejects_cross_org_slug(self) -> None:
        """A slug another org already holds in the shared sandbox is rejected by the slug
        reservation, before any App is created for this org. Every deploy path (template and
        CLI) reserves via this same claim_sandbox_app_slug."""
        self.aws_account.is_humr_sandbox = True
        self.aws_account.save(update_fields=["is_humr_sandbox"])
        other_org = models.Organization.objects.create(name="Other Sandbox Org", slug="other-sb")
        models.SandboxSlugClaim.objects.create(slug="freshslug", organization=other_org)

        with self.assertRaises(ValueError):
            sandbox_service.claim_sandbox_app_slug(
                app_slug="freshslug",
                organization_id=self.organization.id,
                environment=self.environment,
            )

        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="freshslug", organization=self.organization).exists())
