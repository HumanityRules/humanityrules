"""Tests for the shared Humanity Rules sandbox: auto-provisioning, name collisions, teardown guard."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TestCase, override_settings
from django.urls import reverse

import humanityrules_app.models as models
import humanityrules_app.services.jobs.environment_teardown_executor as environment_teardown_executor
from humanityrules_app.services import abac_service, sandbox_service

HTMX = {"HTTP_HX_REQUEST": "true"}

SANDBOX_SETTINGS = {
    "HUMR_SANDBOX_AWS_ACCOUNT_ID": "555553041615",
    "HUMR_SANDBOX_EXTERNAL_ID": "a6677132-0f17-49a4-af2c-9aa3103e60e7",
    "HUMR_SANDBOX_REGION": "us-east-1",
    "HUMR_SANDBOX_HOSTED_ZONE": "sandbox.humanityrules.io",
}


def _make_app(organization: models.Organization, slug: str) -> models.App:
    workspace = models.Workspace.objects.get(organization=organization, slug="default")
    repository = models.Repository.objects.create(
        organization=organization,
        provider="github",
        name=slug,
        full_name=f"org/{slug}",
        default_branch="main",
        clone_url=f"https://github.com/org/{slug}.git",
    )
    return models.App.objects.create(
        organization=organization,
        workspace=workspace,
        repository=repository,
        name=slug,
        slug=slug,
        app_type="web",
        build_strategy="dockerfile",
        branch="main",
        container_port=8000,
        health_check_path="/health",
    )


def _make_deployment(app: models.App, environment: models.Environment) -> models.Deployment:
    blueprint = models.DeploymentBlueprint.objects.create(
        app=app,
        environment=environment,
        branch="main",
        cpu=256,
        memory=512,
        subdomain=app.slug,
    )
    return models.Deployment.objects.create(
        blueprint=blueprint,
        app=app,
        environment=environment,
        image_tag=f"{app.slug}-x",
        status=models.Deployment.Status.SUCCEEDED,
    )


@override_settings(**SANDBOX_SETTINGS)
class TestSandboxProvisioning(TestCase):
    def test_org_creation_provisions_connected_sandbox_account_and_ready_env(self) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        account = models.AWSAccount.objects.get(organization=org, is_humr_sandbox=True)
        self.assertEqual(account.name, "Humanity Rules Sandbox")
        self.assertEqual(account.aws_account_id, "555553041615")
        self.assertEqual(account.status, models.AWSAccount.Status.CONNECTED)
        self.assertEqual(str(account.external_id), SANDBOX_SETTINGS["HUMR_SANDBOX_EXTERNAL_ID"])

        env = models.Environment.objects.get(aws_account=account)
        self.assertEqual(env.slug, sandbox_service.HUMR_SANDBOX_ENV_SLUG)
        self.assertEqual(env.status, models.Environment.Status.READY)
        self.assertEqual(env.aws_region, "us-east-1")

    def test_ensure_org_sandbox_is_idempotent(self) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        sandbox_service.ensure_org_sandbox(organization=org)
        sandbox_service.ensure_org_sandbox(organization=org)
        self.assertEqual(models.AWSAccount.objects.filter(organization=org, is_humr_sandbox=True).count(), 1)
        self.assertEqual(
            models.Environment.objects.filter(aws_account__organization=org, slug="sandbox").count(),
            1,
        )


class TestSandboxNotConfigured(TestCase):
    def test_org_creation_creates_no_sandbox_account_when_unconfigured(self) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        self.assertFalse(models.AWSAccount.objects.filter(organization=org).exists())


@override_settings(**SANDBOX_SETTINGS)
class TestSandboxAppNameCollision(TestCase):
    def _sandbox_env(self, organization: models.Organization) -> models.Environment:
        return (
            models.Environment.objects
            .select_related("aws_account")
            .get(aws_account__organization=organization, slug="sandbox")
        )

    def test_conflicting_slug_from_other_org_is_rejected(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        app_a = _make_app(org_a, "demo")
        app_b = _make_app(org_b, "demo")
        # Org A claims "demo" by having a deployment in its sandbox env.
        _make_deployment(app_a, self._sandbox_env(org_a))
        with self.assertRaises(ValueError):
            async_to_sync(sandbox_service.acheck_sandbox_app_name_available)(
                app=app_b,
                environment=self._sandbox_env(org_b),
            )

    def test_same_org_redeploy_is_allowed(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        app_a = _make_app(org_a, "demo")
        _make_deployment(app_a, self._sandbox_env(org_a))
        # No raise: the only conflicting deployment belongs to the same org.
        async_to_sync(sandbox_service.acheck_sandbox_app_name_available)(
            app=app_a,
            environment=self._sandbox_env(org_a),
        )

    def test_non_sandbox_env_skips_check(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        app_a = _make_app(org_a, "demo")
        app_b = _make_app(org_b, "demo")
        _make_deployment(app_a, self._sandbox_env(org_a))
        regular_account = models.AWSAccount.objects.create(organization=org_b, name="Own AWS")
        regular_env = (
            models.Environment.objects
            .select_related("aws_account")
            .get(id=models.Environment.objects.create(
                aws_account=regular_account,
                name="Prod",
                slug="prod",
                aws_region="us-east-1",
                status=models.Environment.Status.READY,
            ).id)
        )
        # Conflicting slug exists in the sandbox, but this deploy targets a non-sandbox env.
        async_to_sync(sandbox_service.acheck_sandbox_app_name_available)(
            app=app_b,
            environment=regular_env,
        )


@override_settings(**SANDBOX_SETTINGS)
class TestSandboxTeardownGuard(TestCase):
    @patch.object(environment_teardown_executor.infra_customer.deploy_base, "teardown")
    def test_sandbox_env_teardown_skips_shared_base_infra(self, mock_teardown) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        ok = environment_teardown_executor.run_environment_teardown(environment_id=str(env.id))
        self.assertTrue(ok)
        mock_teardown.assert_not_called()
        self.assertFalse(models.Environment.objects.filter(id=env.id).exists())


@override_settings(**SANDBOX_SETTINGS)
class TestSandboxEnvironmentTeardownUI(TestCase):
    def setUp(self) -> None:
        self.org = models.Organization.objects.create(name="Acme", slug="acme")
        self.admin_user = models.User.objects.create_user(
            username="sandbox_admin",
            password="x",
            current_organization=self.org,
        )
        models.OrganizationMembership.objects.create(
            organization=self.org,
            user=self.admin_user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin_user)
        self.sandbox_env = models.Environment.objects.select_related("aws_account").get(
            aws_account__organization=self.org,
            slug=sandbox_service.HUMR_SANDBOX_ENV_SLUG,
        )
        self.regular_account = models.AWSAccount.objects.create(
            organization=self.org,
            name="Own AWS",
        )
        self.regular_env = models.Environment.objects.create(
            aws_account=self.regular_account,
            name="Production",
            slug="production",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )

    def test_sandbox_environment_detail_hides_teardown_button(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("environment_detail", kwargs={"environment_id": self.sandbox_env.id}),
            **HTMX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Tear Down")

    def test_regular_environment_detail_shows_teardown_button(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("environment_detail", kwargs={"environment_id": self.regular_env.id}),
            **HTMX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tear Down")

    def test_sandbox_environment_teardown_confirm_returns_403(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("environment_teardown_confirm", kwargs={"environment_id": self.sandbox_env.id}),
        )
        self.assertEqual(response.status_code, 403)

    def test_sandbox_environment_teardown_returns_403(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_teardown", kwargs={"environment_id": self.sandbox_env.id}),
        )
        self.assertEqual(response.status_code, 403)
        self.sandbox_env.refresh_from_db()
        self.assertEqual(self.sandbox_env.status, models.Environment.Status.READY)
