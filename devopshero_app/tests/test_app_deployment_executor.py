"""Tests for app deployment executor behavior."""

from unittest.mock import call, patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

import devopshero_app.models as models
import devopshero_app.services.agent.tools as agent_tools
import devopshero_app.services.jobs.app_deployment_debug_simulator as app_deployment_debug_simulator
import devopshero_app.services.jobs.app_deployment_executor as app_deployment_executor


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
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            repository=self.repository,
            name="MyApp",
            slug="myapp",
            app_type="web",
            build_strategy="dockerfile",
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="example.com",
        )
        self.conversation = models.Conversation.objects.create(
            user=self.user,
            organization=self.organization,
            context_repository=self.repository,
            context_workspace=self.workspace,
            context_app=self.app,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
        )

    @override_settings(DOH_DEBUG_DEPLOYMENTS=True, DOH_RUN_JOB_WORKER=True)
    def test_run_deployment_debug_mode_succeeds_without_external_calls(self) -> None:
        async_to_sync(agent_tools.save_blueprint)(
            conversation=self.conversation,
            workspace=self.workspace,
            user=self.user,
            environment_slug=self.environment.slug,
            branch=None,
            cpu=256,
            memory=512,
            environment_variables=None,
            app_secrets=None,
            datastore_id=None,
            subdomain=None,
        )
        deploy_result = async_to_sync(agent_tools.deploy_blueprint)(conversation=self.conversation)

        with (
            patch("devopshero_app.services.jobs.app_deployment_debug_simulator.time.sleep") as sleep_mock,
            patch("devopshero_app.services.jobs.app_deployment_executor.repo_service.clone_repository") as clone_repository_mock,
            patch("devopshero_app.services.jobs.app_deployment_executor.repo_service.cleanup_repository") as cleanup_repository_mock,
            patch("devopshero_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_app_mock,
            patch("devopshero_app.services.jobs.app_deployment_executor._get_aws_session") as get_aws_session_mock,
        ):
            success = app_deployment_executor.run_deployment(deployment_id=deploy_result.deployment_id)

        self.assertTrue(success)
        self.assertEqual(
            sleep_mock.call_args_list,
            [
                call(app_deployment_debug_simulator.DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS),
                call(app_deployment_debug_simulator.DEBUG_DEPLOYMENT_STEP_DELAY_SECONDS),
            ],
        )
        clone_repository_mock.assert_not_called()
        cleanup_repository_mock.assert_not_called()
        deploy_app_mock.assert_not_called()
        get_aws_session_mock.assert_not_called()

        deployment = models.Deployment.objects.get(id=deploy_result.deployment_id)
        blueprint = models.DeploymentBlueprint.objects.get(id=deploy_result.blueprint_id)

        self.assertEqual(deployment.status, models.Deployment.Status.SUCCEEDED)
        self.assertEqual(deployment.status_message, "Debug deployment completed successfully")
        self.assertEqual(deployment.service_url, "https://myapp.example.com")
        self.assertEqual(deployment.alb_dns, "myapp-staging.debug-alb.local")
        self.assertIsNotNone(deployment.started_at)
        self.assertIsNotNone(deployment.completed_at)

        self.assertEqual(blueprint.status, models.DeploymentBlueprint.Status.ACTIVE)
        self.assertEqual(blueprint.status_message, "Debug deployment succeeded")

        log_messages = list(
            models.DeploymentLog.objects.filter(deployment=deployment)
            .order_by("created_at")
            .values_list("message", flat=True)
        )
        self.assertTrue(
            any("skipping repository clone and AWS calls" in message for message in log_messages)
        )

    def test_run_deployment_refuses_cross_tenant_row(self) -> None:
        """A Deployment whose app and environment belong to different orgs must be refused."""
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
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=other_env,
            status=models.DeploymentBlueprint.Status.DRAFT,
            branch="main",
            cpu=256,
            memory=512,
            containers=[{"name": "myapp", "environment_variables": [], "app_secrets": {}}],
            subdomain="",
            created_by=self.user,
        )
        deployment = models.Deployment.objects.create(
            blueprint=blueprint,
            app=self.app,
            environment=other_env,
            git_ref="main",
            git_commit_sha="",
            git_commit_message="",
            image_tag="myapp-test",
            status=models.Deployment.Status.PENDING,
            status_message="",
            created_by=self.user,
        )

        with patch("devopshero_app.services.jobs.app_deployment_executor.infra_customer.deploy_app.deploy") as deploy_mock:
            success = app_deployment_executor.run_deployment(deployment_id=str(deployment.id))

        self.assertFalse(success)
        deploy_mock.assert_not_called()

        deployment.refresh_from_db()
        self.assertEqual(deployment.status, models.Deployment.Status.FAILED)
        self.assertIn("Refused", deployment.status_message)

