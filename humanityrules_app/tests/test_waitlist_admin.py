"""Waitlist admin admissions delegate invitation delivery to WorkOS.

HumR records which waitlist entries were invited, while WorkOS owns the
application-wide invitation token, email, and closed-signup bypass.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from django.contrib import admin, messages
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from humanityrules_app.admin import WaitlistSignupAdmin
from humanityrules_app.models import WaitlistSignup


@override_settings(WORKOS_API_KEY="test-workos-key", WORKOS_CLIENT_ID="test-workos-client")
class TestWaitlistSignupAdminInvitations(TestCase):
    def setUp(self) -> None:
        self.model_admin = WaitlistSignupAdmin(model=WaitlistSignup, admin_site=admin.site)
        self.request = RequestFactory().post("/admin/humanityrules_app/waitlistsignup/")
        self.model_admin.message_user = MagicMock()

    @patch("humanityrules_app.admin.WorkOSClient")
    def test_sends_application_wide_invitation_and_records_it(self, workos_client_class: MagicMock) -> None:
        signup = WaitlistSignup.objects.create(email="interesting@example.com", source="hero")
        workos_client = workos_client_class.return_value
        workos_client.user_management.send_invitation.return_value = SimpleNamespace(id="invitation_123")

        self.model_admin.invite_selected_to_humr(
            request=self.request,
            queryset=WaitlistSignup.objects.filter(pk=signup.pk),
        )

        workos_client_class.assert_called_once_with(
            api_key="test-workos-key",
            client_id="test-workos-client",
        )
        workos_client.user_management.send_invitation.assert_called_once_with(email="interesting@example.com")
        signup.refresh_from_db()
        self.assertEqual(signup.workos_invitation_id, "invitation_123")
        self.assertIsNotNone(signup.invitation_sent_at)

    @patch("humanityrules_app.admin.WorkOSClient")
    def test_skips_previously_invited_signup(self, workos_client_class: MagicMock) -> None:
        signup = WaitlistSignup.objects.create(
            email="already-invited@example.com",
            workos_invitation_id="invitation_existing",
            invitation_sent_at=timezone.now() - timedelta(days=1),
        )

        self.model_admin.invite_selected_to_humr(
            request=self.request,
            queryset=WaitlistSignup.objects.filter(pk=signup.pk),
        )

        workos_client_class.return_value.user_management.send_invitation.assert_not_called()
        self.model_admin.message_user.assert_called_once_with(
            request=self.request,
            message="Skipped 1 previously invited waitlist signup(s).",
            level=messages.INFO,
        )

    @patch("humanityrules_app.admin.WorkOSClient")
    def test_failed_invitation_leaves_signup_uninvited(self, workos_client_class: MagicMock) -> None:
        signup = WaitlistSignup.objects.create(email="failure@example.com")
        request = httpx.Request(method="POST", url="https://api.workos.com/user_management/invitations")
        workos_client_class.return_value.user_management.send_invitation.side_effect = httpx.ConnectError(
            message="WorkOS unavailable",
            request=request,
        )

        self.model_admin.invite_selected_to_humr(
            request=self.request,
            queryset=WaitlistSignup.objects.filter(pk=signup.pk),
        )

        signup.refresh_from_db()
        self.assertIsNone(signup.workos_invitation_id)
        self.assertIsNone(signup.invitation_sent_at)
        self.model_admin.message_user.assert_called_once_with(
            request=self.request,
            message="Could not invite failure@example.com through WorkOS: WorkOS unavailable",
            level=messages.ERROR,
        )

    @patch("humanityrules_app.admin.WorkOSClient")
    def test_processes_selected_signups_independently(self, workos_client_class: MagicMock) -> None:
        first = WaitlistSignup.objects.create(email="first@example.com")
        second = WaitlistSignup.objects.create(email="second@example.com")
        request = httpx.Request(method="POST", url="https://api.workos.com/user_management/invitations")
        workos_client_class.return_value.user_management.send_invitation.side_effect = [
            SimpleNamespace(id="invitation_first"),
            httpx.ConnectError(message="WorkOS unavailable", request=request),
        ]

        self.model_admin.invite_selected_to_humr(
            request=self.request,
            queryset=WaitlistSignup.objects.filter(pk__in=[first.pk, second.pk]).order_by("email"),
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.workos_invitation_id, "invitation_first")
        self.assertIsNotNone(first.invitation_sent_at)
        self.assertIsNone(second.workos_invitation_id)
        self.assertIsNone(second.invitation_sent_at)
        self.assertEqual(workos_client_class.return_value.user_management.send_invitation.call_count, 2)
