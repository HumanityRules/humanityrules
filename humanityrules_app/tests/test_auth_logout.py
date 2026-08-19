"""Verify that signing out ends both HumR's Django session and its WorkOS session."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
from django.test import TestCase
from django.urls import reverse

from humanityrules_app import models


def _workos_access_token(session_id: str) -> str:
    """Build a signed test token containing the WorkOS session claim."""
    return jwt.encode(payload={"sid": session_id}, key="test-key", algorithm="HS256")


class WorkOSLogoutTests(TestCase):
    def setUp(self) -> None:
        organization = models.Organization.objects.create(name="Acme", slug="acme")
        self.user = models.User.objects.create_user(
            username="person@example.com",
            email="person@example.com",
            password="unused",
            workos_user_id="workos-user",
            current_organization=organization,
        )

    @patch("humanityrules_app.views.auth._get_workos_client")
    def test_callback_remembers_workos_session_id(self, get_workos_client: MagicMock) -> None:
        get_workos_client.return_value.user_management.authenticate_with_code.return_value = SimpleNamespace(
            access_token=_workos_access_token(session_id="session-from-token"),
            user=SimpleNamespace(
                id="workos-user",
                email="person@example.com",
                first_name="Test",
                last_name="Person",
            ),
        )

        response = self.client.get(path=reverse("auth_callback"), data={"code": "test-code"})

        self.assertRedirects(response=response, expected_url="/dashboard/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["workos_session_id"], "session-from-token")

    @patch("humanityrules_app.views.auth._get_workos_client")
    def test_logout_redirects_browser_through_workos(self, get_workos_client: MagicMock) -> None:
        self.client.force_login(user=self.user)
        session = self.client.session
        session["workos_session_id"] = "session-to-end"
        session.save()
        workos_logout_url = "https://api.workos.com/user_management/sessions/logout?session_id=session-to-end"
        get_workos_client.return_value.user_management.get_logout_url.return_value = workos_logout_url

        response = self.client.get(path=reverse("logout"))

        self.assertRedirects(response=response, expected_url=workos_logout_url, fetch_redirect_response=False)
        get_workos_client.return_value.user_management.get_logout_url.assert_called_once_with(
            session_id="session-to-end",
            return_to="http://testserver/",
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    @patch("humanityrules_app.views.auth._get_workos_client")
    def test_logout_without_workos_session_id_stays_local(self, get_workos_client: MagicMock) -> None:
        self.client.force_login(user=self.user)

        response = self.client.get(path=reverse("logout"))

        self.assertRedirects(response=response, expected_url="/", fetch_redirect_response=False)
        get_workos_client.assert_not_called()
        self.assertNotIn("_auth_user_id", self.client.session)
