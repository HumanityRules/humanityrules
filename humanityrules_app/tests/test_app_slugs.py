"""Tests for dashless agent hostname labels."""

from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

import humanityrules_app.management.commands.seed_app_templates as seed_app_templates
import humanityrules_app.services.agent.tools as agent_tools
from humanityrules_app import app_slugs
from humanityrules_app import models
from humanityrules_app.services.app_templates import template_deploy_service
from humanityrules_app.views import template_deploy


class AgentSlugTests(SimpleTestCase):
    """Verify free-text derivation and explicit-label rejection."""

    def test_derive_app_slug_normalizes_free_text(self) -> None:
        cases = [
            ("My Agent", "myagent"),
            ("UPPER Agent", "upperagent"),
            ("Café Déjà", "cafedeja"),
            ("agent---007", "agent007"),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(app_slugs.derive_app_slug(value=value), expected)

    def test_template_slug_fallback_is_normalized(self) -> None:
        template = models.AppTemplate(slug="hermes-personal", prefill_name="")

        result = template_deploy._compute_default_app_name(
            template=template,
            org=MagicMock(),
            owner_username=None,
        )

        self.assertEqual(result, "hermespersonal")

    def test_seeded_template_prefill_pattern_is_dashless(self) -> None:
        self.assertEqual(seed_app_templates.HERMES_PERSONAL_TEMPLATE["prefill_name"], "hermes{username}{index}")

    def test_template_prefill_pattern_is_normalized_for_the_display_name(self) -> None:
        template = models.AppTemplate(slug="hermes-personal", prefill_name="hermes-{username}{index}")
        organization = MagicMock()

        with patch.object(models.App.objects, "filter") as filter_mock:
            filter_mock.return_value.exists.return_value = False
            result = template_deploy._compute_default_app_name(
                template=template,
                org=organization,
                owner_username="Vmendi@example.com",
            )

        self.assertEqual(result, "hermesvmendi00")
        filter_mock.assert_called_once_with(organization=organization, slug="hermesvmendi00")

    def test_model_fields_reject_invalid_hostname_labels(self) -> None:
        fields = [
            models.App._meta.get_field("slug"),
            models.DeploymentBlueprint._meta.get_field("subdomain"),
            models.Deployment._meta.get_field("subdomain"),
        ]
        for field in fields:
            with self.subTest(model=field.model.__name__):
                with self.assertRaisesMessage(ValidationError, app_slugs.APP_HOSTNAME_LABEL_ERROR):
                    field.clean(value="invalid-label", model_instance=None)

    def test_subdomain_fields_allow_blank(self) -> None:
        fields = [
            models.DeploymentBlueprint._meta.get_field("subdomain"),
            models.Deployment._meta.get_field("subdomain"),
        ]
        for field in fields:
            with self.subTest(model=field.model.__name__):
                self.assertEqual(field.clean(value="", model_instance=None), "")

    def test_template_deploy_service_rejects_invalid_explicit_slug(self) -> None:
        placeholder = MagicMock()

        with self.assertRaisesMessage(ValueError, app_slugs.APP_HOSTNAME_LABEL_ERROR):
            async_to_sync(template_deploy_service.deploy_from_template)(
                template=placeholder,
                organization=placeholder,
                workspace=placeholder,
                environment=placeholder,
                app_name="Invalid Agent",
                app_slug="invalid-agent",
                created_by=placeholder,
                runtime_variable_overrides=None,
                owner_username=None,
                compute_mode="fargate",
                label="",
            )

    def test_seed_local_app_cli_rejects_invalid_explicit_slug(self) -> None:
        with self.assertRaisesMessage(CommandError, app_slugs.APP_HOSTNAME_LABEL_ERROR):
            call_command(
                "seed_local_app",
                aws_account="Missing Account",
                app_slug="invalid-agent",
                owner_username="owner@example.com",
            )

    def test_humr_control_cli_rejects_name_without_slug_characters(self) -> None:
        stderr = StringIO()

        call_command(
            "humr_control",
            "deploy-app-template",
            template="missing",
            workspace="default",
            env="default",
            app_name="!!!",
            stderr=stderr,
        )

        self.assertIn("--app-name must contain at least one letter or number", stderr.getvalue())


class TemplateDeployConflictTests(TestCase):
    """Verify a hostname-label conflict aborts template deployment before anything is persisted."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Conflict Org", slug="conflict-org")
        self.user = models.User.objects.create_user(
            username="conflict-user",
            password="x",
            current_organization=self.organization,
        )
        self.workspace = models.Workspace.objects.get(organization=self.organization, slug="default")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Conflict AWS",
            is_humr_sandbox=True,
        )
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Sandbox",
            slug="sandbox",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
            shared_alb_hosted_zone="apps.example.com",
        )
        self.template = models.AppTemplate.objects.create(
            name="Conflict Template",
            slug="conflict-template",
            description="",
            icon="",
            category="ai-assistant",
            cpu=256,
            memory=512,
            default_compute_mode="fargate",
            is_active=True,
            containers=[
                {
                    "name": "app",
                    "image_source": "dockerfile",
                    "source_repo_path": "hermes_agent",
                    "container_port": 8000,
                }
            ],
        )
        self.other_repository = models.Repository.objects.create(
            organization=self.organization,
            provider=models.Repository.Provider.GITHUB,
            name="other",
            full_name="org/other",
            clone_url="https://github.com/org/other.git",
            default_branch="main",
        )
        self.other_app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            repository=self.other_repository,
            name="Other App",
            slug="otherapp",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        blueprint = models.DeploymentBlueprint.objects.create(
            app=self.other_app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
            branch="",
            cpu=256,
            memory=512,
            subdomain="takenlabel",
            created_by=self.user,
        )
        models.Deployment.objects.create(
            blueprint=blueprint,
            app=self.other_app,
            environment=self.environment,
            subdomain="takenlabel",
            git_ref="main",
            image_tag="otherapp-main-20260717",
            status=models.Deployment.Status.SUCCEEDED,
            created_by=self.user,
        )

    def test_template_deploy_aborts_before_persisting_on_label_conflict(self) -> None:
        with self.assertRaisesMessage(ValueError, "'takenlabel.apps.example.com' is already in use"):
            async_to_sync(template_deploy_service.deploy_from_template)(
                template=self.template,
                organization=self.organization,
                workspace=self.workspace,
                environment=self.environment,
                app_name="Taken Label",
                app_slug="takenlabel",
                created_by=self.user,
                runtime_variable_overrides=None,
                owner_username=None,
                compute_mode="fargate",
                label="",
            )

        self.assertFalse(models.App.objects.filter(organization=self.organization, slug="takenlabel").exists())
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="takenlabel").exists())
        self.assertFalse(
            models.Repository.objects.filter(organization=self.organization, full_name="template/conflict-template").exists()
        )
        self.assertFalse(models.DeploymentBlueprint.objects.exclude(subdomain="takenlabel").exists())


class SaveAppSlugTests(TestCase):
    """Verify the agent App creation tool uses dashless derivation."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Agent Tools", slug="agent-tools")
        self.user = models.User.objects.create_user(
            username="agent-user",
            password="x",
            current_organization=self.organization,
        )
        self.workspace = models.Workspace.objects.select_related("organization").get(
            organization=self.organization,
            slug="default",
        )
        self.repository = models.Repository.objects.create(
            organization=self.organization,
            provider=models.Repository.Provider.GITHUB,
            name="repo",
            full_name="org/repo",
            clone_url="https://github.com/org/repo.git",
            default_branch="main",
        )
        self.conversation = models.Conversation.objects.create(
            user=self.user,
            organization=self.organization,
            mode=models.Conversation.Mode.APP_DEPLOYMENT,
            context_workspace=self.workspace,
            context_repository=self.repository,
        )

    def test_save_app_derives_dashless_slug(self) -> None:
        result = async_to_sync(agent_tools.save_app)(
            conversation=self.conversation,
            workspace=self.workspace,
            repository=self.repository,
            user=self.user,
            name="Café Agent-007",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            container_port=8000,
            health_check_path="/health",
            dockerfile_path=None,
            health_check_command=None,
            repo_subpath=None,
        )

        self.assertEqual(result.slug, "cafeagent007")
        self.assertTrue(models.App.objects.filter(organization=self.organization, slug="cafeagent007").exists())


class AgentSlugErrorSurfaceTests(TestCase):
    """Verify user-facing deploy and CLI paths report hostname-label failures."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Slug Surfaces", slug="slug-surfaces")
        self.workspace = models.Workspace.objects.get(organization=self.organization, slug="default")
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Slug AWS")
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Default",
            slug="default",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        self.user = models.User.objects.create_user(
            username="slug-admin",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        self.template = models.AppTemplate.objects.create(
            name="Slug Template",
            slug="slug-template",
            description="Template for slug tests",
            icon="S",
            category="test",
            cpu=256,
            memory=512,
            containers=[],
            is_active=True,
        )

    def _create_redeploy_source(self, subdomain: str) -> tuple[models.App, models.Deployment]:
        """Create a concluded deployment for CLI redeploy tests."""
        repository = models.Repository.objects.create(
            organization=self.organization,
            provider=models.Repository.Provider.GITHUB,
            name="slug-repo",
            full_name="slug/repo",
            clone_url="https://github.com/slug/repo.git",
            default_branch="main",
        )
        app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            repository=repository,
            name="Slug Agent",
            slug="slugagent",
            app_type=models.App.AppType.WEB,
            build_strategy=models.App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        blueprint = models.DeploymentBlueprint.objects.create(
            app=app,
            environment=self.environment,
            status=models.DeploymentBlueprint.Status.ACTIVE,
            cpu=256,
            memory=512,
            subdomain="slugagent",
        )
        source = models.Deployment.objects.create(
            blueprint=blueprint,
            app=app,
            environment=self.environment,
            subdomain=subdomain,
            git_ref="main",
            image_tag="slugagent-main-source",
            status=models.Deployment.Status.SUCCEEDED,
        )
        return app, source

    def test_template_deploy_form_surfaces_service_value_error(self) -> None:
        self.client.force_login(self.user)
        deploy_mock = AsyncMock(side_effect=ValueError("Please specify an explicit subdomain."))

        with patch(
            "humanityrules_app.views.template_deploy.template_deploy_service.deploy_from_template",
            new=deploy_mock,
        ):
            response = self.client.post(
                reverse("template_deploy_form", kwargs={"template_slug": self.template.slug}),
                {
                    "app_name": "My Agent",
                    "workspace_id": str(self.workspace.id),
                    "environment_id": str(self.environment.id),
                    "compute_mode": models.EcsComputeMode.FARGATE,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please specify an explicit subdomain.")
        self.assertFalse(models.App.objects.filter(organization=self.organization, slug="myagent").exists())
        self.assertEqual(deploy_mock.await_args.kwargs["app_slug"], "myagent")

    def test_humr_control_deploy_template_writes_service_value_error_to_stderr(self) -> None:
        stderr = StringIO()
        deploy_mock = AsyncMock(side_effect=ValueError("Please specify an explicit subdomain."))

        with patch(
            "humanityrules_app.management.commands.humr_control.template_deploy_service.deploy_from_template",
            new=deploy_mock,
        ):
            call_command(
                "humr_control",
                "deploy-app-template",
                template=self.template.slug,
                org=self.organization.slug,
                workspace=self.workspace.slug,
                env=self.environment.slug,
                app_name="My Agent",
                stderr=stderr,
            )

        self.assertIn("Please specify an explicit subdomain.", stderr.getvalue())
        self.assertEqual(deploy_mock.await_args.kwargs["app_slug"], "myagent")

    def test_humr_control_redeploy_rejects_invalid_source_subdomain(self) -> None:
        app, source = self._create_redeploy_source(subdomain="legacy-subdomain")
        stderr = StringIO()

        call_command(
            "humr_control",
            "redeploy-app",
            app=app.slug,
            deployment=str(source.id),
            stderr=stderr,
        )

        self.assertIn("Agent hostname labels must contain lowercase letters and digits only.", stderr.getvalue())
        self.assertEqual(models.Deployment.objects.filter(app=app).count(), 1)

    def test_humr_control_redeploy_allows_blank_source_subdomain_with_valid_app_slug(self) -> None:
        app, source = self._create_redeploy_source(subdomain="")
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "redeploy-app",
            app=app.slug,
            deployment=str(source.id),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertNotIn("Agent hostname labels must contain lowercase letters and digits only.", stderr.getvalue())
        pending = models.Deployment.objects.get(app=app, status=models.Deployment.Status.PENDING)
        self.assertEqual(pending.subdomain, "")
