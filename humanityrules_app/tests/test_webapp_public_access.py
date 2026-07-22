"""Tests for webapp public-access grants: PDP anonymous endpoint, grant lifecycle, CP views."""

import hashlib
import json
from datetime import datetime, timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app.models import (
    App,
    AppTemplate,
    AWSAccount,
    Environment,
    EnvironmentBearerToken,
    Organization,
    OrganizationMembership,
    User,
    WebappPublicGrant,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.views import webapp_public_access


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PublicAccessTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Pub Org", slug="pub-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Pub Account")
        self.environment = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1", shared_alb_hosted_zone="staging.example.com",
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="PAs", slug="pas")
        self.template = AppTemplate.objects.create(
            name="Hermes", slug="hermes", description="agent", icon="app",
            category="agent", cpu=1024, memory=2048,
            containers=[{"name": "agent", "image_source": "template", "template_path": "hermes_agent"}],
            enable_webapp_hosts=True, is_active=True,
        )
        self.admin = User.objects.create_user(username="pub_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)
        self.member = User.objects.create_user(username="pub_member", password="x", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.member, role="member")

        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=self.template, environment=self.environment,
            name="Wolfie", slug="wolfie",
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
            live_state=App.LiveState.DEPLOYED, service_url="https://wolfie.staging.example.com",
        )
        self.raw_token = "t" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.environment, token_hash=_hash(self.raw_token),
        )
        self.client = Client()

    def _grant(self, slug: str, expires_at: datetime | None) -> WebappPublicGrant:
        return WebappPublicGrant.objects.create(
            app=self.app, slug=slug, granted_by=self.admin, expires_at=expires_at,
        )

    def _pdp_public(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/pdp/evaluate-public",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()


class TestPdpEvaluatePublic(PublicAccessTestBase):

    def test_live_grant_allows(self) -> None:
        self._grant(slug="dashboard", expires_at=None)
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"decision": "allow", "reason": "public-webapp"})

    def test_no_grant_denies(self) -> None:
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"decision": "deny", "reason": "not-public"})

    def test_revoked_grant_denies(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body["decision"], "deny")

    def test_expired_grant_denies(self) -> None:
        self._grant(slug="dashboard", expires_at=timezone.now() - timedelta(minutes=1))
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body["decision"], "deny")

    def test_bearer_from_other_environment_denies(self) -> None:
        # A live grant exists, but the caller's env bearer belongs to a
        # different environment than the app's — deny before the grant lookup.
        other_env = Environment.objects.create(
            aws_account=self.aws_account, name="prod", slug="prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.example.com",
        )
        other_token_raw = "u" * 64
        EnvironmentBearerToken.objects.create(
            environment=other_env, token_hash=_hash(other_token_raw),
        )
        self._grant(slug="dashboard", expires_at=None)
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=other_token_raw)
        self.assertEqual(body, {"decision": "deny", "reason": "app-not-in-env"})

    def test_unknown_app_denies(self) -> None:
        status, body = self._pdp_public(body={"app_id": "ghost", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body, {"decision": "deny", "reason": "app-not-in-org"})

    def test_missing_bearer_returns_401(self) -> None:
        status, _ = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=None)
        self.assertEqual(status, 401)

    def test_missing_fields_return_400(self) -> None:
        status, _ = self._pdp_public(body={"app_id": "wolfie"}, token=self.raw_token)
        self.assertEqual(status, 400)


class TestGrantLifecycle(PublicAccessTestBase):

    def test_second_unrevoked_grant_violates_constraint(self) -> None:
        self._grant(slug="dashboard", expires_at=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._grant(slug="dashboard", expires_at=None)

    def test_revoked_grant_frees_the_slug(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        fresh = self._grant(slug="dashboard", expires_at=None)
        self.assertTrue(fresh.is_live)
        self.assertEqual(WebappPublicGrant.objects.filter(slug="dashboard").count(), 2)

    def test_live_excludes_expired_and_revoked(self) -> None:
        live = self._grant(slug="live-app", expires_at=None)
        self._grant(slug="expired-app", expires_at=timezone.now() - timedelta(minutes=1))
        revoked = self._grant(slug="revoked-app", expires_at=None)
        revoked.revoked_at = timezone.now()
        revoked.save(update_fields=["revoked_at"])
        self.assertEqual(list(WebappPublicGrant.live().filter(app=self.app)), [live])


class TestPublicAccessViews(PublicAccessTestBase):

    def _create(self, user: User, slug: str, expiry: str) -> HttpResponse:
        """POST the publish form for self.app."""
        self.client.force_login(user)
        return self.client.post(
            f"/apps/{self.app.slug}/public-access/",
            data={"slug": slug, "expiry": expiry},
        )

    def test_org_admin_creates_grant(self) -> None:
        response = self._create(user=self.admin, slug="dashboard", expiry="24h")
        self.assertEqual(response.status_code, 302)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertTrue(grant.is_live)
        self.assertEqual(grant.granted_by, self.admin)
        self.assertIsNotNone(grant.expires_at)
        self.assertIn(f"/apps/{self.app.slug}/public-access/{grant.id}/", response["Location"])

    def test_never_expiry_creates_open_ended_grant(self) -> None:
        self._create(user=self.admin, slug="dashboard", expiry="never")
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertIsNone(grant.expires_at)

    def test_member_cannot_create_grant(self) -> None:
        response = self._create(user=self.member, slug="dashboard", expiry="24h")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(WebappPublicGrant.objects.filter(slug="dashboard").exists())

    def test_invalid_slug_rejected(self) -> None:
        for bad in ("__admin", "a", "has.dot", "-lead"):
            response = self._create(user=self.admin, slug=bad, expiry="24h")
            self.assertEqual(response.status_code, 422, bad)
        self.assertFalse(WebappPublicGrant.objects.exists())

    def test_slug_input_is_lowercased(self) -> None:
        response = self._create(user=self.admin, slug="Dashboard", expiry="24h")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(WebappPublicGrant.objects.filter(slug="dashboard").exists())

    def test_template_without_webapp_hosts_rejected(self) -> None:
        self.template.enable_webapp_hosts = False
        self.template.save(update_fields=["enable_webapp_hosts"])
        response = self._create(user=self.admin, slug="dashboard", expiry="24h")
        self.assertEqual(response.status_code, 422)

    def test_recreate_extends_live_grant(self) -> None:
        self._create(user=self.admin, slug="dashboard", expiry="1h")
        self._create(user=self.admin, slug="dashboard", expiry="never")
        grants = WebappPublicGrant.objects.filter(app=self.app, slug="dashboard")
        self.assertEqual(grants.count(), 1)
        self.assertIsNone(grants.get().expires_at)

    def test_first_publish_race_retries_and_extends(self) -> None:
        real = webapp_public_access._create_or_extend_grant
        call_count = {"n": 0}

        def lose_race_once(**kwargs) -> WebappPublicGrant:
            call_count["n"] += 1
            if call_count["n"] == 1:
                self._grant(slug="dashboard", expires_at=timezone.now() + timedelta(hours=1))
                raise IntegrityError("unique_unrevoked_webapp_grant")
            return real(**kwargs)

        with mock.patch.object(webapp_public_access, "_create_or_extend_grant", side_effect=lose_race_once):
            response = self._create(user=self.admin, slug="dashboard", expiry="never")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(call_count["n"], 2)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertIsNone(grant.expires_at)

    def test_recreate_after_expiry_revokes_old_and_inserts_new(self) -> None:
        expired = self._grant(slug="dashboard", expires_at=timezone.now() - timedelta(minutes=1))
        response = self._create(user=self.admin, slug="dashboard", expiry="24h")
        self.assertEqual(response.status_code, 302)
        expired.refresh_from_db()
        self.assertIsNotNone(expired.revoked_at)
        grants = WebappPublicGrant.objects.filter(app=self.app, slug="dashboard")
        self.assertEqual(grants.count(), 2)
        self.assertEqual(WebappPublicGrant.live().filter(app=self.app, slug="dashboard").count(), 1)

    def test_revoke_stamps_and_keeps_row(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.admin)
        response = self.client.post(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(grant.revoked_by, self.admin)

    def test_revoke_confirm_uses_standard_modal_and_refreshes_panel(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke-confirm/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, "Revoke Public Access")
        self.assertContains(response, "dashboard on staging")
        self.assertContains(response, 'hx-target="#public-access-section"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'hx-push-url="false"')

    def test_panel_revoke_button_opens_modal_without_browser_confirm(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/", HTTP_HX_REQUEST="true")
        self.assertContains(response, f"/public-access/{grant.id}/revoke-confirm/")
        self.assertNotContains(response, "hx-confirm")

    def test_member_cannot_revoke(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.member)
        response = self.client.post(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke/")
        self.assertEqual(response.status_code, 403)
        grant.refresh_from_db()
        self.assertIsNone(grant.revoked_at)

    def test_member_cannot_open_revoke_confirm(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.member)
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke-confirm/")
        self.assertEqual(response.status_code, 403)

    def test_confirm_page_renders_hostname_for_admin(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/apps/{self.app.slug}/public-access/new?slug=dashboard",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wolfie.staging.example.com")
        self.assertContains(response, 'value="dashboard"')

    def test_confirm_page_ignores_invalid_prefill_slug(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/apps/{self.app.slug}/public-access/new?slug=__admin",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "__admin")

    def _member_without_workspace_view(self) -> User:
        """Membership row only, no org-role attribute — matches no workspace policy."""
        user = User.objects.create_user(username="pub_no_ws_view", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=user, role=OrganizationMembership.Role.MEMBER)
        return user

    def test_status_page_requires_workspace_view(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self._member_without_workspace_view())
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)

    def test_check_requires_workspace_view(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self._member_without_workspace_view())
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/check/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 403)

    def test_member_with_workspace_view_sees_status_page(self) -> None:
        grant = self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.member)
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

    def test_panel_shows_live_grant_url(self) -> None:
        self._grant(slug="dashboard", expires_at=None)
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://dashboard-wolfie.staging.example.com/")

    def test_public_url_preserves_dashed_webapp_slug(self) -> None:
        grant = self._grant(slug="my-dash-board", expires_at=None)

        self.assertEqual(
            webapp_public_access._public_url(grant=grant),
            "https://my-dash-board-wolfie.staging.example.com/",
        )
