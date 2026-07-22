"""Tests for the name-your-first-agent onboarding step in views/onboarding.py."""

from unittest.mock import AsyncMock, MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from humanityrules_app.models import Environment, User


@override_settings(
    DEBUG=True,
    HUMR_SANDBOX_AWS_ACCOUNT_ID="123456789012",
    HUMR_SANDBOX_EXTERNAL_ID="00000000-0000-0000-0000-000000000001",
    HUMR_SANDBOX_REGION="us-east-1",
    HUMR_SANDBOX_HOSTED_ZONE="sandbox.humr.io",
)
class OnboardingFirstAgentTests(TestCase):

    def setUp(self) -> None:
        call_command("seed_app_templates")

    def _onboard(self, email: str, org_name: str) -> User:
        """Run the full new-user flow: dev-login, then create the org via step 1."""
        self.client.get(reverse("dev_login"), {"email": email})
        self.client.post(reverse("onboarding"), {"organization_name": org_name})
        return User.objects.get(email=email)

    def test_org_creation_redirects_to_agent_step_and_sets_flag(self) -> None:
        self.client.get(reverse("dev_login"), {"email": "founder@test.com"})
        response = self.client.post(reverse("onboarding"), {"organization_name": "Founder Co"})
        self.assertRedirects(response, "/onboarding/agent/", fetch_redirect_response=False)
        self.assertTrue(self.client.session["onboarding_first_agent"])

    def test_get_renders_prefilled_name_and_hosted_zone(self) -> None:
        self._onboard(email="founder@test.com", org_name="Founder Co")
        response = self.client.get(reverse("onboarding_agent"))
        self.assertEqual(response.status_code, 200)
        # Dev-login users get first_name "Test"; the generated prefill is dashless.
        self.assertEqual(response.context["agent_name"], "test")
        self.assertEqual(response.context["hosted_zone"], "sandbox.humr.io")
        # The sandbox footnote only renders for the HumR-run sandbox account.
        self.assertContains(response, "an account we run")

    def test_get_normalizes_dashed_first_name_for_prefill(self) -> None:
        user = self._onboard(email="founder@test.com", org_name="Founder Co")
        user.first_name = "Mary-Jane"
        user.save(update_fields=["first_name"])

        response = self.client.get(reverse("onboarding_agent"))

        self.assertEqual(response.context["agent_name"], "maryjane")

    def test_get_uses_dashless_fallback_when_first_name_cannot_be_normalized(self) -> None:
        user = self._onboard(email="founder@test.com", org_name="Founder Co")
        user.first_name = "李"
        user.save(update_fields=["first_name"])

        response = self.client.get(reverse("onboarding_agent"))

        self.assertEqual(response.context["agent_name"], "myagent")

    def test_get_preview_derives_a_dashless_agent_slug(self) -> None:
        self._onboard(email="founder@test.com", org_name="Founder Co")

        response = self.client.get(reverse("onboarding_agent"))

        self.assertContains(response, "function deriveAgentSlug(value)")
        self.assertContains(response, '.replace(/[^a-z0-9]/g, "")')
        self.assertNotContains(response, "function slugify(value)")

    def test_get_without_flag_redirects_to_dashboard(self) -> None:
        user = self._onboard(email="founder@test.com", org_name="Founder Co")
        # A fresh session from a later login has no onboarding flag.
        self.client.logout()
        self.client.get(reverse("dev_login"), {"email": user.email})
        response = self.client.get(reverse("onboarding_agent"))
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_get_without_ready_environment_redirects_to_dashboard(self) -> None:
        self._onboard(email="founder@test.com", org_name="Founder Co")
        Environment.objects.all().delete()
        response = self.client.get(reverse("onboarding_agent"))
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertNotIn("onboarding_first_agent", self.client.session)

    def test_post_name_without_alphanumerics_shows_error(self) -> None:
        self._onboard(email="founder@test.com", org_name="Founder Co")
        response = self.client.post(reverse("onboarding_agent"), {"agent_name": "!!!"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one letter or number")

    def test_post_deploys_and_redirects_to_app_detail(self) -> None:
        user = self._onboard(email="founder@test.com", org_name="Founder Co")
        fake_app = MagicMock()
        fake_app.slug = "myagent007"
        deploy_mock = AsyncMock(return_value=fake_app)
        with patch("humanityrules_app.views.onboarding.template_deploy_service.deploy_from_template", new=deploy_mock):
            response = self.client.post(reverse("onboarding_agent"), {"agent_name": "Mý Agent-007"})
        self.assertRedirects(response, "/apps/myagent007/?welcome=1", fetch_redirect_response=False)
        self.assertNotIn("onboarding_first_agent", self.client.session)
        deploy_mock.assert_awaited_once()
        kwargs = deploy_mock.await_args.kwargs
        self.assertEqual(kwargs["app_name"], "Mý Agent-007")
        self.assertEqual(kwargs["app_slug"], "myagent007")
        self.assertEqual(kwargs["owner_username"], user.username)
        self.assertEqual(kwargs["template"].slug, "hermes-personal")
        self.assertEqual(kwargs["workspace"].organization, user.current_organization)
        self.assertEqual(kwargs["environment"].status, Environment.Status.READY)

    def test_post_deploy_value_error_shows_message_and_keeps_flag(self) -> None:
        self._onboard(email="founder@test.com", org_name="Founder Co")
        deploy_mock = AsyncMock(side_effect=ValueError("That name is already taken in the sandbox."))
        with patch("humanityrules_app.views.onboarding.template_deploy_service.deploy_from_template", new=deploy_mock):
            response = self.client.post(reverse("onboarding_agent"), {"agent_name": "My Agent"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already taken in the sandbox")
        self.assertTrue(self.client.session["onboarding_first_agent"])
