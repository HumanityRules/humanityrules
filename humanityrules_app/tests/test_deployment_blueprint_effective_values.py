"""Tests for effective deployment blueprint value resolution."""

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings

import humanityrules_app.models as models
import humanityrules_app.services.agent.agent_build_prompt as agent_build_prompt
import humanityrules_app.services.agent.tools as agent_tools
import humanityrules_app.services.deployment_blueprint_effective_values as deployment_blueprint_effective_values


@override_settings(HUMR_DEBUG_DEPLOYMENTS=False)
class TestDeploymentBlueprintEffectiveValues(TestCase):
    """Verify effective blueprint values resolve consistently across UI and tool paths."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Blueprint Org", slug="blueprint-org")
        self.user = models.User.objects.create_user(
            username="blueprint-user",
            password="x",
            current_organization=self.organization,
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Blueprint AWS")
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider="github",
            name="repo",
            full_name="org/repo",
            default_branch="master",
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
        )
        self.conversation = models.Conversation.objects.create(
            user=self.user,
            organization=self.organization,
            context_repository=self.repository,
            context_workspace=self.workspace,
            context_app=self.app,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
        )

    def _create_environment(self, name: str, slug: str, hosted_zone: str) -> models.Environment:
        """Create a ready environment for subdomain resolution tests."""
        return models.Environment.objects.create(
            aws_account=self.aws_account,
            name=name,
            slug=slug,
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone=hosted_zone,
        )

    def _create_active_deployment(
        self,
        app: models.App,
        environment: models.Environment,
        subdomain: str,
    ) -> models.Deployment:
        """Create a succeeded deployment occupying a hostname."""
        blueprint = models.DeploymentBlueprint.objects.create(
            app=app,
            environment=environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
            branch="",
            cpu=256,
            memory=512,
            subdomain=subdomain,
            created_by=self.user,
        )
        return models.Deployment.objects.create(
            blueprint=blueprint,
            app=app,
            environment=environment,
            subdomain=subdomain,
            git_ref="main",
            image_tag=f"{app.slug}-main-20260310",
            status=models.Deployment.Status.SUCCEEDED,
            created_by=self.user,
        )

    def test_resolve_deployment_blueprint_effective_values_uses_fallback_values(self) -> None:
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.DRAFT,
            branch="",
            cpu=256,
            memory=512,
            subdomain="",
            created_by=self.user,
        )

        effective_values = deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
            app=self.app,
            blueprint=blueprint,
        )

        self.assertEqual(effective_values.branch, "master")
        self.assertEqual(effective_values.cpu_display, "256 units (0.25 vCPU)")
        self.assertEqual(effective_values.subdomain, "myapp")
        self.assertEqual(effective_values.url, "")

    def test_resolve_deployment_blueprint_effective_values_formats_larger_cpu_values(self) -> None:
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.DRAFT,
            branch="",
            cpu=2048,
            memory=4096,
            subdomain="",
            created_by=self.user,
        )

        effective_values = deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
            app=self.app,
            blueprint=blueprint,
        )

        self.assertEqual(effective_values.cpu_display, "2048 units (2 vCPU)")

    def test_resolve_deployment_blueprint_effective_values_errors_on_default_conflict(self) -> None:
        dev_environment = self._create_environment(name="Dev", slug="dev", hosted_zone="example.com")
        self.environment.shared_alb_hosted_zone = "example.com"
        self.environment.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self._create_active_deployment(app=self.app, environment=dev_environment, subdomain="myapp")
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.DRAFT,
            branch="",
            cpu=256,
            memory=512,
            subdomain="",
            created_by=self.user,
        )

        with self.assertRaisesMessage(
            ValueError,
            "Subdomain 'myapp.example.com' is already in use. Please specify an explicit subdomain.",
        ):
            deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
                app=self.app,
                blueprint=blueprint,
            )

    def test_resolve_deployment_blueprint_effective_values_excludes_same_app_same_environment(self) -> None:
        self.environment.shared_alb_hosted_zone = "example.com"
        self.environment.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self._create_active_deployment(app=self.app, environment=self.environment, subdomain="myapp")
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.DRAFT,
            branch="",
            cpu=256,
            memory=512,
            subdomain="",
            created_by=self.user,
        )

        effective_values = deployment_blueprint_effective_values.resolve_deployment_blueprint_effective_values(
            app=self.app,
            blueprint=blueprint,
        )

        self.assertEqual(effective_values.subdomain, "myapp")
        self.assertEqual(effective_values.url, "https://myapp.example.com")

    def test_save_blueprint_returns_effective_values(self) -> None:
        result = async_to_sync(agent_tools.save_blueprint)(
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

        self.assertEqual(result.branch, "master")
        self.assertEqual(result.subdomain, "myapp")

    def test_save_blueprint_errors_on_default_conflict_without_persisting(self) -> None:
        dev_environment = self._create_environment(name="Dev", slug="dev", hosted_zone="example.com")
        self.environment.shared_alb_hosted_zone = "example.com"
        self.environment.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self._create_active_deployment(app=self.app, environment=dev_environment, subdomain="myapp")

        with self.assertRaisesMessage(ValueError, "Please specify an explicit subdomain"):
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

        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.context_deployment_blueprint_id)

    def test_deploy_blueprint_persists_explicit_subdomain_after_default_conflict(self) -> None:
        dev_environment = self._create_environment(name="Dev", slug="dev", hosted_zone="example.com")
        self.environment.shared_alb_hosted_zone = "example.com"
        self.environment.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        self._create_active_deployment(app=self.app, environment=dev_environment, subdomain="myapp")

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
            subdomain="myappstaging",
        )
        deploy_result = async_to_sync(agent_tools.deploy_blueprint)(conversation=self.conversation)

        self.assertEqual(deploy_result.git_ref, "master")
        created_deployment = models.Deployment.objects.get(id=deploy_result.deployment_id)
        self.assertEqual(created_deployment.subdomain, "myappstaging")

    def test_save_blueprint_rejects_explicit_conflicting_subdomain_without_persisting(self) -> None:
        dev_environment = self._create_environment(name="Dev", slug="dev", hosted_zone="example.com")
        self.environment.shared_alb_hosted_zone = "example.com"
        self.environment.save(update_fields=["shared_alb_hosted_zone", "updated_at"])
        other_app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            repository=self.repository,
            name="OtherApp",
            slug="otherapp",
            app_type="web",
            build_strategy="dockerfile",
            branch="main",
            container_port=8001,
            health_check_path="/health",
        )
        self._create_active_deployment(app=other_app, environment=dev_environment, subdomain="takenname")

        with self.assertRaisesMessage(ValueError, "Subdomain 'takenname.example.com' is already in use"):
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
                subdomain="takenname",
            )

        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.context_deployment_blueprint_id)

    def test_save_blueprint_rejects_invalid_explicit_subdomain_without_persisting(self) -> None:
        with self.assertRaisesMessage(
            ValueError,
            "Agent hostname labels must contain lowercase letters and digits only.",
        ):
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
                subdomain="invalid-subdomain",
            )

        self.conversation.refresh_from_db()
        self.assertIsNone(self.conversation.context_deployment_blueprint_id)

    def test_save_blueprint_rejects_ambiguous_environment_slug_across_accounts(self) -> None:
        second_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Second AWS",
        )
        models.Environment.objects.create(
            aws_account=second_account,
            name="Staging Copy",
            slug=self.environment.slug,
            aws_region="us-west-2",
            status=models.Environment.Status.READY,
        )

        with self.assertRaisesMessage(ValueError, "Multiple environments share the slug 'staging'"):
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

    def test_app_deployment_prompt_requires_saved_draft_review_before_deploy(self) -> None:
        prompt = async_to_sync(agent_build_prompt.build_system_prompt)(conversation=self.conversation)

        self.assertIn("call `save_app` and `save_blueprint` immediately", prompt)
        self.assertIn("Everything looks good. Deploy this draft now?", prompt)
        self.assertIn("Do NOT call `deploy_blueprint` in the same turn as `save_blueprint`", prompt)
