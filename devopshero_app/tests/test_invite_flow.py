"""Tests for the OrganizationInvite control-plane flow.

Covers admin guard on create/revoke, the consumability checks (revoked,
expired, already-accepted, email-mismatch), and the happy path that
materializes an OrganizationMembership + org-role IdentityAttribute and
flips the user's current_organization.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from devopshero_app.models import (
    IdentityAttribute,
    Organization,
    OrganizationInvite,
    OrganizationMembership,
    User,
)
from devopshero_app.services import abac_service


class InviteFlowTestCase(TestCase):
    """Shared setup: an org with an admin and a non-admin member."""

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Inviter Org", slug="inviter-org")
        self.other_org = Organization.objects.create(name="Other Org", slug="other-org")

        self.admin = User.objects.create_user(
            username="admin@example.com",
            email="admin@example.com",
            password="x",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.admin, role=OrganizationMembership.Role.ADMIN,
        )
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)

        self.member = User.objects.create_user(
            username="member@example.com",
            email="member@example.com",
            password="x",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.member, role=OrganizationMembership.Role.MEMBER,
        )
        IdentityAttribute.objects.create(
            organization=self.org, user=self.member, key="org-role", value="member",
        )

    def _make_invite(self, **overrides) -> OrganizationInvite:
        defaults = {
            "organization": self.org,
            "email": "friend@example.com",
            "invited_by": self.admin,
            "role": OrganizationMembership.Role.MEMBER,
            "expires_at": timezone.now() + timedelta(days=7),
        }
        defaults.update(overrides)
        return OrganizationInvite.objects.create(**defaults)


class TestCreateInvite(InviteFlowTestCase):

    def test_admin_can_create_invite(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/settings/invites/create/", {"email": "friend@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/settings/organization/")

        invite = OrganizationInvite.objects.get(organization=self.org, email="friend@example.com")
        self.assertEqual(invite.role, OrganizationMembership.Role.MEMBER)
        self.assertEqual(invite.invited_by, self.admin)
        self.assertIsNone(invite.accepted_at)
        self.assertIsNone(invite.revoked_at)
        self.assertGreater(invite.expires_at, timezone.now())

    def test_create_invite_lowercases_email(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(
            "/settings/invites/create/", {"email": "  Friend@Example.COM  "},
        )
        invite = OrganizationInvite.objects.get(organization=self.org)
        self.assertEqual(invite.email, "friend@example.com")

    def test_non_admin_cannot_create_invite(self) -> None:
        self.client.force_login(self.member)
        response = self.client.post(
            "/settings/invites/create/", {"email": "friend@example.com"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OrganizationInvite.objects.exists())

    def test_anonymous_cannot_create_invite(self) -> None:
        response = self.client.post(
            "/settings/invites/create/", {"email": "friend@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login/", response["Location"])

    def test_create_invite_rejects_existing_member(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(
            "/settings/invites/create/", {"email": "MEMBER@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrganizationInvite.objects.exists())

    def test_create_invite_requires_email(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post("/settings/invites/create/", {"email": ""})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrganizationInvite.objects.exists())

    def test_create_invite_requires_post(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get("/settings/invites/create/")
        self.assertEqual(response.status_code, 405)

    def test_create_invite_rejected_for_oidc_org(self) -> None:
        self.org.auth_provider = Organization.AuthProvider.OIDC
        self.org.save(update_fields=["auth_provider"])
        self.client.force_login(self.admin)
        response = self.client.post(
            "/settings/invites/create/", {"email": "friend@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(OrganizationInvite.objects.exists())


class TestRevokeInvite(InviteFlowTestCase):

    def test_admin_can_revoke_pending_invite(self) -> None:
        invite = self._make_invite()
        self.client.force_login(self.admin)
        response = self.client.post(f"/settings/invites/{invite.id}/revoke/")
        self.assertEqual(response.status_code, 302)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.revoked_at)

    def test_non_admin_cannot_revoke(self) -> None:
        invite = self._make_invite()
        self.client.force_login(self.member)
        response = self.client.post(f"/settings/invites/{invite.id}/revoke/")
        self.assertEqual(response.status_code, 403)
        invite.refresh_from_db()
        self.assertIsNone(invite.revoked_at)

    def test_revoke_scoped_to_current_org(self) -> None:
        """An admin of one org can't revoke invites belonging to another org."""
        foreign_invite = OrganizationInvite.objects.create(
            organization=self.other_org,
            email="someone@example.com",
            role=OrganizationMembership.Role.MEMBER,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_login(self.admin)
        response = self.client.post(f"/settings/invites/{foreign_invite.id}/revoke/")
        self.assertEqual(response.status_code, 404)
        foreign_invite.refresh_from_db()
        self.assertIsNone(foreign_invite.revoked_at)

    def test_revoke_already_accepted_is_noop(self) -> None:
        invite = self._make_invite(accepted_at=timezone.now())
        self.client.force_login(self.admin)
        self.client.post(f"/settings/invites/{invite.id}/revoke/")
        invite.refresh_from_db()
        self.assertIsNone(invite.revoked_at)


class TestAcceptInvite(InviteFlowTestCase):

    def test_anonymous_sees_landing_page(self) -> None:
        invite = self._make_invite()
        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "devopshero_app/invites/invite_landing.html")
        self.assertContains(response, self.org.name)
        self.assertContains(response, f"next=/invite/{invite.token}/")

    def test_revoked_invite_rejected(self) -> None:
        invite = self._make_invite(revoked_at=timezone.now())
        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "devopshero_app/invites/invite_error.html")
        self.assertContains(response, "revoked", status_code=400)

    def test_expired_invite_rejected(self) -> None:
        invite = self._make_invite(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "expired", status_code=400)

    def test_already_accepted_invite_rejected(self) -> None:
        invite = self._make_invite(accepted_at=timezone.now())
        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already been accepted", status_code=400)

    def test_email_mismatch_rejected(self) -> None:
        invite = self._make_invite(email="someone-else@example.com")
        invitee = User.objects.create_user(
            username="wrong@example.com",
            email="wrong@example.com",
            password="x",
            current_organization=self.other_org,
        )
        OrganizationMembership.objects.create(
            organization=self.other_org, user=invitee, role=OrganizationMembership.Role.MEMBER,
        )
        self.client.force_login(invitee)

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "devopshero_app/invites/invite_error.html")
        self.assertFalse(
            OrganizationMembership.objects.filter(user=invitee, organization=self.org).exists(),
        )

    def test_happy_path_creates_membership_and_attribute(self) -> None:
        invite = self._make_invite(email="friend@example.com")
        invitee = User.objects.create_user(
            username="friend@example.com",
            email="friend@example.com",
            password="x",
            workos_user_id="user_friend",
            current_organization=self.other_org,
        )
        self.client.force_login(invitee)

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

        membership = OrganizationMembership.objects.get(user=invitee, organization=self.org)
        self.assertEqual(membership.role, OrganizationMembership.Role.MEMBER)

        attribute = IdentityAttribute.objects.get(
            organization=self.org, user=invitee, key="org-role",
        )
        self.assertEqual(attribute.value, OrganizationMembership.Role.MEMBER)

        username_attr = IdentityAttribute.objects.get(
            organization=self.org, user=invitee, key="username",
        )
        self.assertEqual(username_attr.value, invitee.username)

        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

        invitee.refresh_from_db()
        self.assertEqual(invitee.current_organization, self.org)

    def test_email_match_is_case_insensitive(self) -> None:
        invite = self._make_invite(email="friend@example.com")
        invitee = User.objects.create_user(
            username="FRIEND@example.com",
            email="FRIEND@example.com",
            password="x",
            workos_user_id="user_friend",
            current_organization=self.other_org,
        )
        self.client.force_login(invitee)

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)
        self.assertTrue(
            OrganizationMembership.objects.filter(user=invitee, organization=self.org).exists(),
        )

    def test_accept_rejects_when_org_flipped_to_oidc(self) -> None:
        """Stale invite for an org that has since switched to OIDC must not consume."""
        invite = self._make_invite(email="friend@example.com")
        invitee = User.objects.create_user(
            username="friend@example.com",
            email="friend@example.com",
            password="x",
            current_organization=self.other_org,
        )
        self.org.auth_provider = Organization.AuthProvider.OIDC
        self.org.save(update_fields=["auth_provider"])
        self.client.force_login(invitee)

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            OrganizationMembership.objects.filter(user=invitee, organization=self.org).exists(),
        )

    def test_accept_rejects_user_without_workos_user_id(self) -> None:
        # An OIDC-authed user with the right email must not be able to consume
        # a WorkOS-org invite — the policy proxy keys on workos_user_id, so the
        # membership would be unreachable from the deployed app side.
        invite = self._make_invite(email="friend@example.com")
        invitee = User.objects.create_user(
            username="friend@example.com",
            email="friend@example.com",
            password="x",
            oidc_sub="okta|friend",
            current_organization=self.other_org,
        )
        self.client.force_login(invitee)

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            OrganizationMembership.objects.filter(user=invitee, organization=self.org).exists(),
        )

    def test_replaying_accepted_invite_rejected(self) -> None:
        """After a successful accept, the same link can't be used again."""
        invite = self._make_invite(email="friend@example.com")
        invitee = User.objects.create_user(
            username="friend@example.com",
            email="friend@example.com",
            password="x",
            workos_user_id="user_friend",
            current_organization=self.other_org,
        )
        self.client.force_login(invitee)
        self.client.get(f"/invite/{invite.token}/")

        response = self.client.get(f"/invite/{invite.token}/")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already been accepted", status_code=400)


class TestOnboardingViaInvite(InviteFlowTestCase):
    """The slim onboarding path used when a brand-new user clicks an invite link."""

    def _seed_pending_workos_session(self, email: str, invite_token) -> None:
        session = self.client.session
        session["pending_workos_user"] = {
            "workos_user_id": "user_test123",
            "email": email,
            "first_name": "Friendly",
            "last_name": "Stranger",
        }
        session["post_login_redirect"] = f"/invite/{invite_token}/"
        session.save()

    def test_invitee_skips_org_creation(self) -> None:
        invite = self._make_invite(email="friend@example.com")
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        # User creation + invite acceptance are collapsed into one atomic step;
        # we land on the dashboard with the membership already materialized.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")

        # No new Organization was created (still just the two from setUp).
        self.assertEqual(Organization.objects.count(), 2)

        new_user = User.objects.get(workos_user_id="user_test123")
        self.assertEqual(new_user.email, "friend@example.com")
        self.assertEqual(new_user.current_organization, self.org)

        self.assertTrue(
            OrganizationMembership.objects.filter(
                user=new_user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
            ).exists()
        )
        invite.refresh_from_db()
        self.assertIsNotNone(invite.accepted_at)

    def test_email_mismatch_blocks_onboarding(self) -> None:
        invite = self._make_invite(email="friend@example.com")
        self._seed_pending_workos_session(email="impostor@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "devopshero_app/invites/invite_error.html")
        self.assertFalse(User.objects.filter(workos_user_id="user_test123").exists())

    def test_expired_invite_falls_through_to_regular_onboarding(self) -> None:
        # An expired invite must NOT attach the new user to the inviter's org;
        # they should land on the standard "name your org" form instead.
        invite = self._make_invite(
            email="friend@example.com",
            expires_at=timezone.now() - timedelta(days=1),
        )
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "devopshero_app/onboarding.html")
        self.assertFalse(User.objects.filter(workos_user_id="user_test123").exists())

    def test_revoked_invite_falls_through_to_regular_onboarding(self) -> None:
        invite = self._make_invite(
            email="friend@example.com",
            revoked_at=timezone.now(),
        )
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "devopshero_app/onboarding.html")
        self.assertFalse(User.objects.filter(workos_user_id="user_test123").exists())

    def test_already_accepted_invite_falls_through_to_regular_onboarding(self) -> None:
        invite = self._make_invite(
            email="friend@example.com",
            accepted_at=timezone.now(),
        )
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "devopshero_app/onboarding.html")
        self.assertFalse(User.objects.filter(workos_user_id="user_test123").exists())

    def test_invitee_with_existing_oidc_row_links_workos_id(self) -> None:
        # Pre-existing OIDC user (from another org) signs in via WorkOS to
        # accept an invite. We must NOT try to create a second User row —
        # link the workos_user_id onto the existing row instead.
        invite = self._make_invite(email="friend@example.com")
        existing = User.objects.create_user(
            username="friend@example.com",
            email="friend@example.com",
            password="x",
            oidc_sub="okta|friend",
            current_organization=self.other_org,
        )
        self.assertFalse(existing.workos_user_id)
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/dashboard/")

        existing.refresh_from_db()
        self.assertEqual(existing.workos_user_id, "user_test123")
        self.assertEqual(existing.oidc_sub, "okta|friend")
        self.assertEqual(existing.current_organization, self.org)
        self.assertEqual(User.objects.filter(email__iexact="friend@example.com").count(), 1)
        self.assertTrue(
            OrganizationMembership.objects.filter(user=existing, organization=self.org).exists(),
        )

    def test_oidc_org_invite_falls_through_to_regular_onboarding(self) -> None:
        # Invite belongs to an OIDC-authenticated org (not WORKOS); the user
        # should not be auto-attached to the inviter's org.
        self.org.auth_provider = Organization.AuthProvider.OIDC
        self.org.save(update_fields=["auth_provider"])
        invite = self._make_invite(email="friend@example.com")
        self._seed_pending_workos_session(email="friend@example.com", invite_token=invite.token)

        response = self.client.get("/onboarding/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "devopshero_app/onboarding.html")
        self.assertFalse(User.objects.filter(workos_user_id="user_test123").exists())
