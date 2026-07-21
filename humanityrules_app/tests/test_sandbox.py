"""Tests for the shared Humanity Rules sandbox: auto-provisioning, name collisions, teardown guard."""

from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.db import IntegrityError, transaction
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


def _make_app(organization: models.Organization, slug: str, environment: models.Environment) -> models.App:
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
        environment=environment,
        repository=repository,
        name=slug,
        slug=slug,
        app_type="web",
        build_strategy="dockerfile",
        container_port=8000,
        health_check_path="/health",
        cpu=256,
        memory=512,
    )


def _make_deployment(app: models.App) -> models.Deployment:
    return models.Deployment.objects.create(
        app=app,
        git_ref="main",
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

    def test_ensure_org_sandbox_enforces_eni_trunking_on_existing_rows(self) -> None:
        # Every org's sandbox env shares one cluster, so the rows must agree
        # with the SANDBOX_ENI_TRUNKING_ENABLED switch — including rows created
        # before the switch existed.
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        self.assertEqual(env.eni_trunking_enabled, sandbox_service.SANDBOX_ENI_TRUNKING_ENABLED)

        env.eni_trunking_enabled = not sandbox_service.SANDBOX_ENI_TRUNKING_ENABLED
        env.save(update_fields=["eni_trunking_enabled"])
        sandbox_service.ensure_org_sandbox(organization=org)
        env.refresh_from_db()
        self.assertEqual(env.eni_trunking_enabled, sandbox_service.SANDBOX_ENI_TRUNKING_ENABLED)


@override_settings(
    HUMR_SANDBOX_AWS_ACCOUNT_ID="",
    HUMR_SANDBOX_EXTERNAL_ID="",
    HUMR_SANDBOX_REGION="",
    HUMR_SANDBOX_HOSTED_ZONE="",
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

    def _claim(self, organization: models.Organization, slug: str) -> None:
        async_to_sync(sandbox_service.aclaim_sandbox_app_slug)(
            app_slug=slug,
            organization_id=organization.id,
            environment=self._sandbox_env(organization),
        )

    def test_conflicting_slug_from_other_org_is_rejected(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        app_a = _make_app(org_a, "demo", self._sandbox_env(org_a))
        # Org A claims "demo" by having a committed deployment in its sandbox env.
        _make_deployment(app_a)
        with self.assertRaises(ValueError):
            self._claim(org_b, "demo")

    def test_same_org_redeploy_is_allowed(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        app_a = _make_app(org_a, "demo", self._sandbox_env(org_a))
        _make_deployment(app_a)
        # No raise: the only conflicting deployment belongs to the same org.
        self._claim(org_a, "demo")

    def test_non_sandbox_env_skips_check(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        app_a = _make_app(org_a, "demo", self._sandbox_env(org_a))
        _make_deployment(app_a)
        regular_account = models.AWSAccount.objects.create(organization=org_b, name="Own AWS")
        regular_env = models.Environment.objects.create(
            aws_account=regular_account,
            name="Prod",
            slug="prod",
            aws_region="us-east-1",
            status=models.Environment.Status.READY,
        )
        # Conflicting slug exists in the sandbox, but this deploy targets a non-sandbox env:
        # no raise and no claim row is written.
        async_to_sync(sandbox_service.aclaim_sandbox_app_slug)(
            app_slug="demo",
            organization_id=org_b.id,
            environment=regular_env,
        )
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="demo").exists())

    def test_claim_row_written_on_first_reservation(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        self._claim(org_a, "demo")
        self.assertEqual(models.SandboxSlugClaim.objects.get(slug="demo").organization_id, org_a.id)

    def test_claim_blocks_other_org_before_any_deployment_exists(self) -> None:
        # The race the claim row exists to close: org A has reserved "demo" but committed no
        # Deployment yet, so the friendly Deployment pre-check passes — only the UNIQUE(slug)
        # row stops org B from also taking it.
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        self._claim(org_a, "demo")
        self.assertFalse(models.Deployment.objects.filter(app__slug="demo").exists())
        with self.assertRaises(ValueError):
            self._claim(org_b, "demo")

    def test_duplicate_slug_claim_violates_db_constraint(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        models.SandboxSlugClaim.objects.create(slug="demo", organization=org_a)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.SandboxSlugClaim.objects.create(slug="demo", organization=org_b)

    def test_release_frees_claim_for_reuse(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        self._claim(org_a, "demo")
        sandbox_service.release_sandbox_app_slug(app_slug="demo", organization_id=org_a.id)
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="demo").exists())
        # With org A's claim released and no deployment, org B can now take the slug.
        self._claim(org_b, "demo")
        self.assertEqual(models.SandboxSlugClaim.objects.get(slug="demo").organization_id, org_b.id)


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

    @patch.object(environment_teardown_executor.infra_customer.deploy_base, "teardown")
    def test_sandbox_env_teardown_deletes_apps_and_releases_slug_claims(self, mock_teardown) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        app = _make_app(org, "demo", env)
        async_to_sync(sandbox_service.aclaim_sandbox_app_slug)(
            app_slug="demo",
            organization_id=org.id,
            environment=env,
        )
        self.assertTrue(models.SandboxSlugClaim.objects.filter(slug="demo").exists())

        ok = environment_teardown_executor.run_environment_teardown(environment_id=str(env.id))

        self.assertTrue(ok)
        self.assertFalse(models.App.objects.filter(id=app.id).exists())
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="demo").exists())
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
