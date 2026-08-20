"""Tests for dashless agent hostname labels."""

import uuid
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

import humanityrules_app.management.commands.seed_app_templates as seed_app_templates
import humanityrules_app.management.commands.seed_local_app as seed_local_app
from humanityrules_app import app_slugs
from humanityrules_app import models
from humanityrules_app.services import template_deploy_service
from humanityrules_app.tests import app_test_factories
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

    def test_app_slug_field_rejects_invalid_hostname_labels(self) -> None:
        field = models.App._meta.get_field("slug")

        with self.assertRaisesMessage(ValidationError, app_slugs.APP_HOSTNAME_LABEL_ERROR):
            field.clean(value="invalid-label", model_instance=None)

    def test_template_deploy_service_rejects_invalid_explicit_slug(self) -> None:
        placeholder = MagicMock()

        with self.assertRaisesMessage(ValueError, app_slugs.APP_HOSTNAME_LABEL_ERROR):
            template_deploy_service.deploy_from_template(
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
                    "image_source": "template",
                    "template_path": "hermes_agent",
                    "container_port": 8000,
                }
            ],
        )
        self.other_app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=self.template,
            name="Other App",
            slug="takenlabel",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )

    def test_template_deploy_aborts_before_persisting_on_label_conflict(self) -> None:
        with self.assertRaisesMessage(ValueError, "'takenlabel.apps.example.com' is already in use. Please choose a different name."):
            template_deploy_service.deploy_from_template(
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

        self.assertEqual(
            models.App.objects.filter(organization=self.organization, slug="takenlabel").count(), 1,
        )  # only the pre-existing app holds the label
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="takenlabel").exists())


class SaveAppSlugTests(TestCase):
    """Verify creating an App from a display name derives a dashless slug."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="App Slug Org", slug="app-slug-org")
        self.user = models.User.objects.create_user(
            username="app-slug-user",
            password="x",
            current_organization=self.organization,
        )
        self.workspace = models.Workspace.objects.select_related("organization").get(
            organization=self.organization,
            slug="default",
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="App Slug AWS")
        self.environment = models.Environment.objects.create(
            aws_account=self.aws_account,
            name="Default",
            slug="default",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        self.source_template = app_test_factories.make_source_template()

    def test_app_creation_derives_dashless_slug(self) -> None:
        slug = app_slugs.derive_app_slug(value="Café Agent-007")
        app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=self.source_template,
            name="Café Agent-007",
            slug=slug,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            created_by=self.user,
        )

        self.assertEqual(app.slug, "cafeagent007")
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

    def _create_redeploy_source(self) -> models.App:
        """Create a deployed, idle app for CLI redeploy tests."""
        return models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.environment,
            source_template=self.template,
            name="Slug Agent",
            slug="slugagent",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
            live_state=models.App.LiveState.DEPLOYED,
            last_attempt_id=uuid.uuid7(),
        )

    def test_template_deploy_form_surfaces_service_value_error(self) -> None:
        self.client.force_login(self.user)
        deploy_mock = Mock(side_effect=ValueError("Simulated deploy failure."))

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
        self.assertContains(response, "Simulated deploy failure.")
        self.assertFalse(models.App.objects.filter(organization=self.organization, slug="myagent").exists())
        self.assertEqual(deploy_mock.call_args.kwargs["app_slug"], "myagent")

    def test_template_deploy_form_success_redirects_via_htmx_header(self) -> None:
        self.client.force_login(self.user)
        app = self._create_redeploy_source()
        deploy_mock = Mock(return_value=app)

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
        self.assertEqual(response["HX-Redirect"], reverse("app_detail", kwargs={"app_slug": app.slug}))

    def test_humr_control_deploy_template_writes_service_value_error_to_stderr(self) -> None:
        stderr = StringIO()
        deploy_mock = Mock(side_effect=ValueError("Simulated deploy failure."))

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

        self.assertIn("Simulated deploy failure.", stderr.getvalue())
        self.assertEqual(deploy_mock.call_args.kwargs["app_slug"], "myagent")

    def test_humr_control_redeploy_queues_a_fresh_deploy_attempt(self) -> None:
        app = self._create_redeploy_source()
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "redeploy-app",
            app=app.slug,
            created_by=self.user.username,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(stderr.getvalue(), "")
        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.DEPLOY_PENDING)
        self.assertTrue(
            models.DeploymentRecord.objects.filter(
                app=app,
                attempt_id=app.last_attempt_id,
                event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
            ).exists()
        )

    def test_humr_control_redeploy_is_blocked_while_teardown_is_unsettled(self) -> None:
        app = self._create_redeploy_source()
        app.job_status = models.App.JobStatus.TEARDOWN_PENDING
        app.save(update_fields=["job_status", "updated_at"])
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "humr_control",
            "redeploy-app",
            app=app.slug,
            created_by=self.user.username,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertIn("has a job in progress", stderr.getvalue())
        app.refresh_from_db()
        self.assertEqual(app.job_status, models.App.JobStatus.TEARDOWN_PENDING)
        self.assertFalse(
            models.DeploymentRecord.objects.filter(
                app=app, event_type=models.DeploymentRecord.EventType.DEPLOY_STARTED,
            ).exists()
        )


class SeedLocalAppEnvMismatchTests(TestCase):
    """seed_local_app must refuse an existing app whose env differs from the requested one."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Seed Org", slug="seed-org")
        self.workspace = models.Workspace.objects.get(organization=self.organization, slug="default")
        self.user = models.User.objects.create_user(
            username="seed@example.com",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        self.aws_account = models.AWSAccount.objects.create(organization=self.organization, name="Seed AWS")
        self.env_default = models.Environment.objects.create(
            aws_account=self.aws_account, name="Default", slug="default",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )
        self.env_sandbox = models.Environment.objects.create(
            aws_account=self.aws_account, name="Sandbox", slug="sandbox",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )
        self.app = models.App.objects.create(
            organization=self.organization,
            workspace=self.workspace,
            environment=self.env_default,
            source_template=app_test_factories.make_source_template(),
            name="seedapp",
            slug="seedapp",
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )

    def test_existing_app_in_other_env_raises(self) -> None:
        command = seed_local_app.Command()
        with self.assertRaisesMessage(CommandError, "exists in env 'default', not 'sandbox'"):
            command._ensure_local_app_stub(
                org=self.organization,
                environment=self.env_sandbox,
                app_slug="seedapp",
                owner_username=self.user.username,
                template_slug="unused",
                workspace_slug="default",
            )

    def test_existing_app_in_same_env_is_accepted(self) -> None:
        command = seed_local_app.Command()
        command.stdout = StringIO()
        command._ensure_local_app_stub(
            org=self.organization,
            environment=self.env_default,
            app_slug="seedapp",
            owner_username=self.user.username,
            template_slug="unused",
            workspace_slug="default",
        )
        self.assertTrue(
            models.ResourceTag.objects.filter(app=self.app, key="owner", value=self.user.username).exists()
        )
