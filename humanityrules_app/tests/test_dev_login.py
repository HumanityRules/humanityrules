"""Tests for the DEBUG-only dev login bypass in views/auth.py."""

from django.test import TestCase, override_settings
from django.urls import reverse

from humanityrules_app.models import Organization, User


@override_settings(DEBUG=True)
class DevLoginDebugOnTests(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Acme Corp", slug="acme-corp")
        self.user = User.objects.create(
            email="alice@test.com", username="alice@test.com",
            current_organization=self.org,
        )

    def test_no_email_renders_picker_with_existing_users(self) -> None:
        response = self.client.get(reverse("dev_login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alice@test.com")
        self.assertContains(response, "Create a new test user")

    def test_known_email_logs_in_and_redirects_to_dashboard(self) -> None:
        response = self.client.get(reverse("dev_login"), {"email": "alice@test.com"})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_known_email_is_case_insensitive(self) -> None:
        response = self.client.get(reverse("dev_login"), {"email": "ALICE@test.com"})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_known_email_respects_safe_next(self) -> None:
        response = self.client.get(
            reverse("dev_login"), {"email": "alice@test.com", "next": "/integrations/"},
        )
        self.assertRedirects(response, "/integrations/", fetch_redirect_response=False)

    def test_unknown_email_stashes_pending_user_and_redirects_to_onboarding(self) -> None:
        response = self.client.get(reverse("dev_login"), {"email": "newbie@test.com"})
        self.assertRedirects(response, "/onboarding/", fetch_redirect_response=False)
        pending = self.client.session["pending_workos_user"]
        self.assertEqual(pending["email"], "newbie@test.com")
        self.assertTrue(pending["workos_user_id"].startswith("dev_"))
        # Not authenticated yet — onboarding completes the login.
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_with_invite_next_is_stashed_for_onboarding(self) -> None:
        response = self.client.get(
            reverse("dev_login"), {"email": "newbie@test.com", "next": "/invite/abc123/"},
        )
        self.assertRedirects(response, "/onboarding/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["post_login_redirect"], "/invite/abc123/")

    def test_full_new_user_flow_creates_org_and_logs_in(self) -> None:
        self.client.get(reverse("dev_login"), {"email": "founder@test.com"})
        response = self.client.post(reverse("onboarding"), {"organization_name": "Founder Co"})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        user = User.objects.get(email="founder@test.com")
        self.assertEqual(user.current_organization.name, "Founder Co")
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_unknown_email_drops_existing_session_before_onboarding(self) -> None:
        # Log in as an existing user first, then dev-login a brand-new email.
        self.client.get(reverse("dev_login"), {"email": "alice@test.com"})
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))
        response = self.client.get(reverse("dev_login"), {"email": "fresh@test.com"})
        self.assertRedirects(response, "/onboarding/", fetch_redirect_response=False)
        # Old session must be gone so onboarding doesn't short-circuit to dashboard.
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.session["pending_workos_user"]["email"], "fresh@test.com")

    def test_already_authenticated_new_user_creates_its_own_org(self) -> None:
        # Reproduces the reported bug: an already-logged-in user dev-logins a new
        # email and must end up in a NEW org, not their current one.
        self.client.get(reverse("dev_login"), {"email": "alice@test.com"})
        self.client.get(reverse("dev_login"), {"email": "fresh@test.com"})
        response = self.client.post(reverse("onboarding"), {"organization_name": "Fresh Co"})
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        new_user = User.objects.get(email="fresh@test.com")
        self.assertEqual(new_user.current_organization.name, "Fresh Co")
        self.assertNotEqual(new_user.current_organization_id, self.org.id)
        self.assertEqual(self.client.session["_auth_user_id"], str(new_user.pk))


class DevLoginDebugOffTests(TestCase):

    def test_returns_400_when_debug_false(self) -> None:
        with override_settings(DEBUG=False):
            response = self.client.get(reverse("dev_login"))
        self.assertEqual(response.status_code, 400)
