"""Tests for the shared Humanity Rules sandbox: auto-provisioning, name collisions, teardown guard."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

import humanityrules_app.models as models
import humanityrules_app.services.jobs.environment_teardown_executor as environment_teardown_executor
from humanityrules_app.services import abac_service, sandbox_service
from humanityrules_app.tests.app_test_factories import make_source_template

HTMX = {"HTTP_HX_REQUEST": "true"}

SANDBOX_SETTINGS = {
    "HUMR_SANDBOX_AWS_ACCOUNT_ID": "555553041615",
    "HUMR_SANDBOX_EXTERNAL_ID": "a6677132-0f17-49a4-af2c-9aa3103e60e7",
    "HUMR_SANDBOX_REGION": "us-east-1",
    "HUMR_SANDBOX_HOSTED_ZONE": "sandbox.humanityrules.io",
}


def _make_app(organization: models.Organization, slug: str, environment: models.Environment) -> models.App:
    workspace = models.Workspace.objects.get(organization=organization, slug="default")
    return models.App.objects.create(
        organization=organization,
        workspace=workspace,
        environment=environment,
        source_template=make_source_template(),
        name=slug,
        slug=slug,
        container_port=8000,
        health_check_path="/health",
        cpu=256,
        memory=512,
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
        sandbox_service.claim_sandbox_app_slug(
            app_slug=slug,
            organization_id=organization.id,
            environment=self._sandbox_env(organization),
        )

    def test_conflicting_slug_from_other_org_is_rejected(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        _make_app(org_a, "demo", self._sandbox_env(org_a))
        # Org A holds "demo" by owning a committed App in its sandbox env.
        with self.assertRaises(ValueError):
            self._claim(org_b, "demo")

    def test_same_org_redeploy_is_allowed(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        _make_app(org_a, "demo", self._sandbox_env(org_a))
        # No raise: the only conflicting app belongs to the same org.
        self._claim(org_a, "demo")

    def test_non_sandbox_env_skips_check(self) -> None:
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        _make_app(org_a, "demo", self._sandbox_env(org_a))
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
        sandbox_service.claim_sandbox_app_slug(
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
        # App yet, so the friendly App pre-check passes — only the UNIQUE(slug)
        # row stops org B from also taking it.
        org_a = models.Organization.objects.create(name="Org A", slug="org-a")
        org_b = models.Organization.objects.create(name="Org B", slug="org-b")
        self._claim(org_a, "demo")
        self.assertFalse(models.App.objects.filter(slug="demo").exists())
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
        env.status = models.Environment.Status.TEARING_DOWN
        env.save(update_fields=["status", "updated_at"])
        ok = environment_teardown_executor.run_environment_teardown(environment_id=str(env.id))
        self.assertTrue(ok)
        mock_teardown.assert_not_called()
        self.assertFalse(models.Environment.objects.filter(id=env.id).exists())

    @patch.object(environment_teardown_executor.infra_customer.deploy_base, "teardown")
    def test_sandbox_env_teardown_purges_app_data_before_releasing_slug_claims(self, mock_teardown) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        app = _make_app(org, "demo", env)
        sandbox_service.claim_sandbox_app_slug(
            app_slug="demo",
            organization_id=org.id,
            environment=env,
        )
        env.status = models.Environment.Status.TEARING_DOWN
        env.save(update_fields=["status", "updated_at"])
        self.assertTrue(models.SandboxSlugClaim.objects.filter(slug="demo").exists())

        # The purge helper hits AWS; stub it while recording that it runs before the slug release.
        call_order: list[tuple[str, str]] = []

        def fake_purge(app, env):
            call_order.append(("purge", app.slug))
            return True, "ok"

        real_release = sandbox_service.release_sandbox_app_slug

        def tracking_release(app_slug, organization_id):
            call_order.append(("release", app_slug))
            return real_release(app_slug=app_slug, organization_id=organization_id)

        with patch.object(environment_teardown_executor.app_remove_executor, "purge_app_namespace_data", side_effect=fake_purge), \
             patch.object(environment_teardown_executor.sandbox_service, "release_sandbox_app_slug", side_effect=tracking_release):
            ok = environment_teardown_executor.run_environment_teardown(environment_id=str(env.id))

        self.assertTrue(ok)
        self.assertEqual(call_order, [("purge", "demo"), ("release", "demo")])
        self.assertFalse(models.App.objects.filter(id=app.id).exists())
        self.assertFalse(models.SandboxSlugClaim.objects.filter(slug="demo").exists())
        self.assertFalse(models.Environment.objects.filter(id=env.id).exists())

    @patch.object(environment_teardown_executor.infra_customer.deploy_base, "teardown")
    def test_sandbox_env_teardown_aborts_on_purge_failure_and_keeps_slug_claim(self, mock_teardown) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        app = _make_app(org, "demo", env)
        sandbox_service.claim_sandbox_app_slug(
            app_slug="demo",
            organization_id=org.id,
            environment=env,
        )
        env.status = models.Environment.Status.TEARING_DOWN
        env.save(update_fields=["status", "updated_at"])

        with patch.object(
            environment_teardown_executor.app_remove_executor,
            "purge_app_namespace_data",
            return_value=(False, "EFS cleanup failed in 'sandbox': boom"),
        ):
            ok = environment_teardown_executor.run_environment_teardown(environment_id=str(env.id))

        self.assertFalse(ok)
        # A failed purge must never release the slug or delete the app/env.
        self.assertTrue(models.App.objects.filter(id=app.id).exists())
        self.assertTrue(models.SandboxSlugClaim.objects.filter(slug="demo").exists())
        env.refresh_from_db()
        self.assertEqual(env.status, models.Environment.Status.ERROR)


@override_settings(**SANDBOX_SETTINGS)
class TestSandboxRemovalFromTheCLI(TestCase):
    def test_humr_control_queues_a_removal_for_a_sandbox_app(self) -> None:
        org = models.Organization.objects.create(name="Acme", slug="acme")
        env = models.Environment.objects.get(aws_account__organization=org, slug="sandbox")
        _make_app(org, "demo", env)

        call_command(
            "humr_control", "remove-app", "--app", "demo",
            stdout=StringIO(), stderr=StringIO(),
        )

        app = models.App.objects.get(slug="demo")
        self.assertEqual(app.job_status, models.App.JobStatus.REMOVAL_PENDING)


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

    def test_app_remove_confirm_modal_offers_no_way_to_keep_data(self) -> None:
        _make_app(self.org, "demo", self.sandbox_env)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("app_remove_confirm", kwargs={"app_slug": "demo"}), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Removing it destroys")
        self.assertNotContains(response, "<input")

    def test_sandbox_app_remove_queues_a_removal(self) -> None:
        _make_app(self.org, "demo", self.sandbox_env)
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("app_remove", kwargs={"app_slug": "demo"}), **HTMX)
        self.assertEqual(response.status_code, 200)
        app = models.App.objects.get(slug="demo")
        self.assertEqual(app.job_status, models.App.JobStatus.REMOVAL_PENDING)
