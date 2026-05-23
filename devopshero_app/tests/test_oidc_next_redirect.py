"""Tests for `?next=` support on /oidc/login/ and /oidc/callback/.

These are the patches the Google Workspace integration design calls for in
`docs/integrations_broker_design.md`: the OIDC entry point must honor
a validated `next` so the post-auth redirect can land on
`/integrations/google/start`. Validation uses
`django.utils.http.url_has_allowed_host_and_scheme` so `next` cannot be used as
an open redirector.
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from devopshero_app.models import Organization, OrganizationMembership, User


class TestOidcLoginNext(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="NextOrg",
            slug="nextorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )

    def test_safe_next_is_stashed_in_session(self) -> None:
        response = self.client.get(
            reverse("oidc_login"),
            {"org": self.org.slug, "next": "/integrations/google/start?rd=x"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("idp.example.com/v1/authorize", response["Location"])
        self.assertEqual(
            self.client.session["oidc_next"],
            "/integrations/google/start?rd=x",
        )

    def test_unsafe_next_is_rejected_but_login_still_proceeds(self) -> None:
        response = self.client.get(
            reverse("oidc_login"),
            {"org": self.org.slug, "next": "https://evil.example.com/steal"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("idp.example.com/v1/authorize", response["Location"])
        self.assertNotIn("oidc_next", self.client.session)

    def test_already_authenticated_honors_safe_next(self) -> None:
        user = User.objects.create_user(
            username="vmendi",
            password="pw",
            current_organization=self.org,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("oidc_login"),
            {"org": self.org.slug, "next": "/integrations/google/start?rd=x"},
        )
        self.assertRedirects(
            response,
            "/integrations/google/start?rd=x",
            fetch_redirect_response=False,
        )

    def test_already_authenticated_ignores_unsafe_next(self) -> None:
        user = User.objects.create_user(
            username="vmendi",
            password="pw",
            current_organization=self.org,
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("oidc_login"),
            {"org": self.org.slug, "next": "https://evil.example.com/steal"},
        )
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_no_next_clears_any_stale_session_value(self) -> None:
        session = self.client.session
        session["oidc_next"] = "/stale"
        session.save()

        self.client.get(reverse("oidc_login"), {"org": self.org.slug})
        self.assertNotIn("oidc_next", self.client.session)


class TestOidcCallbackNext(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="NextOrg",
            slug="nextorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )

    def _seed_callback_session(self, next_url: str | None) -> str:
        """Set up a session that looks like one in the middle of an OIDC login."""
        session = self.client.session
        session["oidc_state"] = "teststate"
        session["oidc_org_slug"] = self.org.slug
        if next_url is not None:
            session["oidc_next"] = next_url
        session.save()
        return "teststate"

    def _patched_exchange(self, sub: str, email: str):
        return patch(
            "devopshero_app.views.auth._exchange_oidc_code",
            return_value={
                "sub": sub,
                "email": email,
                "first_name": "V",
                "last_name": "M",
            },
        )

    def _create_oidc_member(self, username: str, email: str, oidc_sub: str) -> User:
        user = User.objects.create_user(
            username=username,
            email=email,
            password="pw",
            oidc_sub=oidc_sub,
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=user,
            organization=self.org,
            role=OrganizationMembership.Role.MEMBER,
        )
        return user

    def test_callback_redirects_to_safe_next(self) -> None:
        self._create_oidc_member(
            username="vmendi", email="vmendi@example.com", oidc_sub="sub-1",
        )
        state = self._seed_callback_session(next_url="/integrations/google/start?rd=x")

        with self._patched_exchange(sub="sub-1", email="vmendi@example.com"):
            response = self.client.get(
                reverse("oidc_callback"),
                {"code": "abc", "state": state},
            )

        self.assertRedirects(
            response,
            "/integrations/google/start?rd=x",
            fetch_redirect_response=False,
        )
        self.assertNotIn("oidc_next", self.client.session)

    def test_callback_without_next_falls_back_to_dashboard(self) -> None:
        self._create_oidc_member(
            username="vmendi", email="vmendi@example.com", oidc_sub="sub-1",
        )
        state = self._seed_callback_session(next_url=None)

        with self._patched_exchange(sub="sub-1", email="vmendi@example.com"):
            response = self.client.get(
                reverse("oidc_callback"),
                {"code": "abc", "state": state},
            )

        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_callback_ignores_tampered_next_in_session(self) -> None:
        """Defense in depth: even if something managed to stuff an unsafe next
        into the session, the callback revalidates before trusting it."""
        self._create_oidc_member(
            username="vmendi", email="vmendi@example.com", oidc_sub="sub-1",
        )
        state = self._seed_callback_session(next_url="https://evil.example.com/x")

        with self._patched_exchange(sub="sub-1", email="vmendi@example.com"):
            response = self.client.get(
                reverse("oidc_callback"),
                {"code": "abc", "state": state},
            )

        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_callback_rejects_oidc_sub_linked_to_another_org(self) -> None:
        other_org = Organization.objects.create(
            name="OtherOrg",
            slug="otherorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://other-idp.example.com",
            oidc_client_id="other-cid",
            oidc_client_secret="other-csec",
        )
        other_user = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="pw",
            oidc_sub="sub-1",
            current_organization=other_org,
        )
        OrganizationMembership.objects.create(
            user=other_user,
            organization=other_org,
            role=OrganizationMembership.Role.MEMBER,
        )
        state = self._seed_callback_session(next_url=None)

        with self._patched_exchange(sub="sub-1", email="vmendi@example.com"):
            response = self.client.get(
                reverse("oidc_callback"),
                {"code": "abc", "state": state},
            )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "OIDC sub belongs to a different organization",
            status_code=400,
        )
