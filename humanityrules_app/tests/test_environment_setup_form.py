"""Tests for the non-agent environment setup form, provisioning-log tab, and retry.

These cover the default state (AGENT_DEPLOYMENTS_ENABLED=False): the agent editor is
blocked, env cards route to the detail page, and provisioning is configured through a
plain form that creates a PENDING environment for the job worker to pick up.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

import humanityrules_app.models as models
from humanityrules_app.services import abac_service

HTMX = {"HTTP_HX_REQUEST": "true"}

# Default stub for the Route53 hosted-zone lookup so form-render tests don't hit AWS.
_HOSTED_ZONES_STUB = ([{"id": "", "name": "None (HTTP only)"}, {"id": "dev.example.com", "name": "dev.example.com"}], False)
_HOSTED_ZONES_PATH = "humanityrules_app.views.environments._list_hosted_zone_options"


class TestEnvironmentSetupForm(TestCase):
    """Form-based environment provisioning + provisioning-log tab with agent deployments disabled."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Env Form Org", slug="env-form-org")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization,
            name="Form AWS",
            aws_account_id="123456789012",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.admin_user = models.User.objects.create_user(
            username="env-form-admin",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.admin_user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.organization, admin_user=self.admin_user)

        self.member_user = models.User.objects.create_user(
            username="env-form-member",
            password="x",
            current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.member_user,
            role=models.OrganizationMembership.Role.MEMBER,
        )

    def _make_environment(self, status: str) -> models.Environment:
        return models.Environment.objects.create(
            aws_account=self.aws_account,
            name=f"Env {status}",
            slug=f"env-{status}",
            aws_region="us-east-1",
            status=status,
        )

    # --- Agent flow gated off (default) ---

    def test_agent_environment_editor_blocked_when_disabled(self) -> None:
        self.client.force_login(self.admin_user)
        new = self.client.get(f"{reverse('environment_editor_new')}?aws_account={self.aws_account.id}", **HTMX)
        self.assertEqual(new.status_code, 404)

        env = self._make_environment(models.Environment.Status.DRAFT)
        resume = self.client.get(reverse("environment_editor", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(resume.status_code, 404)

    def test_admin_env_cards_route_to_detail_when_disabled(self) -> None:
        env = self._make_environment(models.Environment.Status.DRAFT)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environments"), **HTMX)
        self.assertEqual(response.status_code, 200)
        environments = {e.id: e for e in response.context["environments"]}
        self.assertEqual(
            environments[env.id].primary_url,
            reverse("environment_detail", kwargs={"environment_id": env.id}),
        )

    def test_new_environment_links_directly_to_setup_form_when_disabled(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environments"), **HTMX)
        self.assertEqual(response.status_code, 200)
        # Jumps straight to the form (no account-picker modal) and the modal isn't rendered.
        self.assertContains(response, reverse("environment_setup_form"))
        self.assertNotContains(response, "aws-account-picker-modal")

    # --- Setup form ---

    @patch(_HOSTED_ZONES_PATH, return_value=_HOSTED_ZONES_STUB)
    def test_setup_form_renders(self, mock_zones) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{reverse('environment_setup_form')}?aws_account={self.aws_account.id}", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Form AWS")
        self.assertContains(response, "us-east-1")

    @patch(_HOSTED_ZONES_PATH, return_value=_HOSTED_ZONES_STUB)
    def test_setup_form_defaults_to_first_account_without_param(self, mock_zones) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_setup_form"), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Form AWS")
        self.assertEqual(response.context["selected_account_id"], str(self.aws_account.id))

    @patch(_HOSTED_ZONES_PATH, return_value=_HOSTED_ZONES_STUB)
    def test_setup_form_lists_hosted_zones_in_domain_dropdown(self, mock_zones) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{reverse('environment_setup_form')}?aws_account={self.aws_account.id}", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dev.example.com")
        self.assertContains(response, "None (HTTP only)")

    @patch(_HOSTED_ZONES_PATH, return_value=([{"id": "", "name": "None (HTTP only)"}], True))
    def test_setup_form_falls_back_to_text_input_when_lookup_fails(self, mock_zones) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(f"{reverse('environment_setup_form')}?aws_account={self.aws_account.id}", **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="text"')
        self.assertContains(response, 'name="shared_alb_hosted_zone"')

    def test_setup_form_creates_pending_environment(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_setup_form"),
            data={
                "aws_account": str(self.aws_account.id),
                "name": "My Env",
                "aws_region": "eu-west-1",
                "shared_alb_hosted_zone": "dev.example.com",
            },
            **HTMX,
        )
        self.assertEqual(response.status_code, 200)
        environment = models.Environment.objects.get(aws_account=self.aws_account, slug="my-env")
        self.assertEqual(environment.name, "My Env")
        self.assertEqual(environment.status, models.Environment.Status.PENDING)
        self.assertEqual(environment.aws_region, "eu-west-1")
        self.assertEqual(environment.shared_alb_hosted_zone, "dev.example.com")
        self.assertEqual(
            response.headers["HX-Push-Url"],
            reverse("environment_detail", kwargs={"environment_id": environment.id}),
        )

    @patch(_HOSTED_ZONES_PATH, return_value=_HOSTED_ZONES_STUB)
    def test_setup_form_requires_name(self, mock_zones) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_setup_form"),
            data={"aws_account": str(self.aws_account.id), "name": "", "aws_region": "us-east-1"},
            **HTMX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name is required.")
        self.assertFalse(models.Environment.objects.filter(aws_account=self.aws_account).exists())

    @patch(_HOSTED_ZONES_PATH, return_value=_HOSTED_ZONES_STUB)
    def test_setup_form_rejects_duplicate_slug(self, mock_zones) -> None:
        models.Environment.objects.create(
            aws_account=self.aws_account, name="Production", slug="production",
            aws_region="us-east-1", status=models.Environment.Status.READY,
        )
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("environment_setup_form"),
            data={"aws_account": str(self.aws_account.id), "name": "Production", "aws_region": "us-east-1"},
            **HTMX,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(models.Environment.objects.filter(aws_account=self.aws_account, slug="production").count(), 1)

    def test_setup_form_requires_org_admin(self) -> None:
        self.client.force_login(self.member_user)
        response = self.client.get(f"{reverse('environment_setup_form')}?aws_account={self.aws_account.id}", **HTMX)
        self.assertEqual(response.status_code, 403)

    # --- Provisioning log tab ---

    def test_log_tab_disabled_without_logs_on_settled_env(self) -> None:
        env = self._make_environment(models.Environment.Status.READY)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_detail", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["log_tab_enabled"])
        self.assertEqual(response.context["initial_tab"], "content")

    def test_log_tab_enabled_and_default_while_transient(self) -> None:
        env = self._make_environment(models.Environment.Status.PROVISIONING)
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_detail", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["log_tab_enabled"])
        self.assertEqual(response.context["initial_tab"], "logs")

    def test_log_tab_enabled_when_logs_exist(self) -> None:
        env = self._make_environment(models.Environment.Status.READY)
        models.EnvironmentLog.objects.create(
            environment=env, source=models.EnvironmentLog.Source.CDK,
            level=models.EnvironmentLog.Level.INFO, message="Deploying VPC stack",
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_detail", kwargs={"environment_id": env.id}), **HTMX)
        self.assertTrue(response.context["log_tab_enabled"])

    def test_provisioning_log_fragment_renders_lines(self) -> None:
        env = self._make_environment(models.Environment.Status.PROVISIONING)
        models.EnvironmentLog.objects.create(
            environment=env, source=models.EnvironmentLog.Source.SYSTEM,
            level=models.EnvironmentLog.Level.INFO, message="Starting provisioning",
        )
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environment_provisioning_log", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Starting provisioning")

    # --- Retry ---

    def test_retry_requeues_failed_environment(self) -> None:
        env = self._make_environment(models.Environment.Status.ERROR)
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("environment_retry", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(response.status_code, 200)
        env.refresh_from_db()
        self.assertEqual(env.status, models.Environment.Status.PENDING)

    def test_retry_rejects_non_error_environment(self) -> None:
        env = self._make_environment(models.Environment.Status.READY)
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("environment_retry", kwargs={"environment_id": env.id}), **HTMX)
        self.assertEqual(response.status_code, 422)
        env.refresh_from_db()
        self.assertEqual(env.status, models.Environment.Status.READY)


@override_settings(AGENT_DEPLOYMENTS_ENABLED=True)
class TestEnvironmentSetupFormAgentEnabled(TestCase):
    """With the agent flow on, the New Environment modal points back at the agent editor."""

    def setUp(self) -> None:
        self.organization = models.Organization.objects.create(name="Env Form Org 2", slug="env-form-org-2")
        self.aws_account = models.AWSAccount.objects.create(
            organization=self.organization, name="Form AWS 2", aws_account_id="210987654321",
            status=models.AWSAccount.Status.CONNECTED,
        )
        self.admin_user = models.User.objects.create_user(
            username="env-form-admin-2", password="x", current_organization=self.organization,
        )
        models.OrganizationMembership.objects.create(
            organization=self.organization, user=self.admin_user,
            role=models.OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.organization, admin_user=self.admin_user)

    def test_new_environment_modal_links_to_agent_editor(self) -> None:
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("environments"), **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"{reverse('environment_editor_new')}?aws_account={self.aws_account.id}")
