"""Tests for webapp public-access grants: PDP anonymous endpoint, grant lifecycle, CP views."""

import hashlib
import json
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import Client, TestCase
from django.utils import timezone

from humanityrules_app.models import (
    App,
    AppTemplate,
    AWSAccount,
    Deployment,
    DeploymentBlueprint,
    Environment,
    EnvironmentBearerToken,
    Organization,
    OrganizationMembership,
    Repository,
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
        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="hermes",
            full_name="org/hermes", clone_url="https://github.com/org/hermes.git",
        )
        self.template = AppTemplate.objects.create(
            name="Hermes", slug="hermes", description="agent", icon="app",
            category="agent", cpu=1024, memory=2048,
            containers=[{"name": "agent", "image_source": "app"}],
            enable_subhosting=True, is_active=True,
        )
        self.admin = User.objects.create_user(username="pub_admin", password="x", current_organization=self.org)
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role=OrganizationMembership.Role.ADMIN)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)
        self.member = User.objects.create_user(username="pub_member", password="x", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.member, role="member")

        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, repository=self.repo,
            source_template=self.template,
            name="Wolfie", slug="wolfie", app_type="web", build_strategy="dockerfile",
            branch="main", container_port=8000, health_check_path="/health",
        )
        self.blueprint = DeploymentBlueprint.objects.create(
            app=self.app, environment=self.environment, status=DeploymentBlueprint.Status.ACTIVE,
            cpu=256, memory=512, subdomain="wolfie",
        )
        Deployment.objects.create(
            blueprint=self.blueprint, app=self.app, environment=self.environment,
            subdomain="wolfie", git_ref="main", image_tag="wolfie-main-1",
            status=Deployment.Status.SUCCEEDED, status_message="Running",
        )
        self.raw_token = "t" * 64
        EnvironmentBearerToken.objects.create(
            environment=self.environment, token_hash=_hash(self.raw_token),
        )
        self.client = Client()

    def _grant(self, slug: str, expires_at=None) -> WebappPublicGrant:
        return WebappPublicGrant.objects.create(
            app=self.app, environment=self.environment, slug=slug,
            granted_by=self.admin, expires_at=expires_at,
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
        self._grant(slug="dashboard")
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"decision": "allow", "reason": "public-webapp"})

    def test_no_grant_denies(self) -> None:
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"decision": "deny", "reason": "not-public"})

    def test_revoked_grant_denies(self) -> None:
        grant = self._grant(slug="dashboard")
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body["decision"], "deny")

    def test_expired_grant_denies(self) -> None:
        self._grant(slug="dashboard", expires_at=timezone.now() - timedelta(minutes=1))
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body["decision"], "deny")

    def test_grant_on_other_environment_denies(self) -> None:
        other_env = Environment.objects.create(
            aws_account=self.aws_account, name="prod", slug="prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.example.com",
        )
        WebappPublicGrant.objects.create(
            app=self.app, environment=other_env, slug="dashboard", granted_by=self.admin,
        )
        status, body = self._pdp_public(body={"app_id": "wolfie", "webapp_slug": "dashboard", "path": "/"}, token=self.raw_token)
        self.assertEqual(body["decision"], "deny")

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
        self._grant(slug="dashboard")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._grant(slug="dashboard")

    def test_revoked_grant_frees_the_slug(self) -> None:
        grant = self._grant(slug="dashboard")
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        fresh = self._grant(slug="dashboard")
        self.assertTrue(fresh.is_live)
        self.assertEqual(WebappPublicGrant.objects.filter(slug="dashboard").count(), 2)

    def test_live_excludes_expired_and_revoked(self) -> None:
        live = self._grant(slug="live-app")
        self._grant(slug="expired-app", expires_at=timezone.now() - timedelta(minutes=1))
        revoked = self._grant(slug="revoked-app")
        revoked.revoked_at = timezone.now()
        revoked.save(update_fields=["revoked_at"])
        self.assertEqual(list(WebappPublicGrant.live().filter(app=self.app)), [live])


class TestPublicAccessViews(PublicAccessTestBase):

    def _create(self, user: User, slug: str, expiry: str, environment: str | None) -> HttpResponse:
        """POST the publish form; environment=None targets self.environment."""
        self.client.force_login(user)
        return self.client.post(
            f"/apps/{self.app.slug}/public-access/",
            data={"slug": slug, "environment": environment if environment is not None else str(self.environment.id), "expiry": expiry},
        )

    def test_org_admin_creates_grant(self) -> None:
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 302)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertTrue(grant.is_live)
        self.assertEqual(grant.granted_by, self.admin)
        self.assertIsNotNone(grant.expires_at)
        self.assertIn(f"/apps/{self.app.slug}/public-access/{grant.id}/", response["Location"])

    def test_never_expiry_creates_open_ended_grant(self) -> None:
        self._create(user=self.admin, slug="dashboard", expiry="never", environment=None)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertIsNone(grant.expires_at)

    def test_member_cannot_create_grant(self) -> None:
        response = self._create(user=self.member, slug="dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(WebappPublicGrant.objects.filter(slug="dashboard").exists())

    def test_invalid_slug_rejected(self) -> None:
        for bad in ("__admin", "a", "has.dot", "-lead"):
            response = self._create(user=self.admin, slug=bad, expiry="24h", environment=None)
            self.assertEqual(response.status_code, 422, bad)
        self.assertFalse(WebappPublicGrant.objects.exists())

    def test_slug_input_is_lowercased(self) -> None:
        response = self._create(user=self.admin, slug="Dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(WebappPublicGrant.objects.filter(slug="dashboard").exists())

    def test_undeployed_environment_rejected(self) -> None:
        prod = Environment.objects.create(
            aws_account=self.aws_account, name="prod", slug="prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.example.com",
        )
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment=str(prod.id))
        self.assertEqual(response.status_code, 422)

    def test_non_uuid_environment_rejected(self) -> None:
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment="staging")
        self.assertEqual(response.status_code, 422)

    def test_same_slug_environment_in_other_account_is_unambiguous(self) -> None:
        other_account = AWSAccount.objects.create(organization=self.org, name="Second Account")
        Environment.objects.create(
            aws_account=other_account, name="staging", slug="staging",
            aws_region="us-east-1", shared_alb_hosted_zone="staging2.example.com",
        )
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 302)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertEqual(grant.environment, self.environment)

    def test_template_without_subhosting_rejected(self) -> None:
        self.template.enable_subhosting = False
        self.template.save(update_fields=["enable_subhosting"])
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 422)

    def test_recreate_extends_live_grant(self) -> None:
        self._create(user=self.admin, slug="dashboard", expiry="1h", environment=None)
        self._create(user=self.admin, slug="dashboard", expiry="never", environment=None)
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
            response = self._create(user=self.admin, slug="dashboard", expiry="never", environment=None)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(call_count["n"], 2)
        grant = WebappPublicGrant.objects.get(app=self.app, slug="dashboard")
        self.assertIsNone(grant.expires_at)

    def test_recreate_after_expiry_revokes_old_and_inserts_new(self) -> None:
        expired = self._grant(slug="dashboard", expires_at=timezone.now() - timedelta(minutes=1))
        response = self._create(user=self.admin, slug="dashboard", expiry="24h", environment=None)
        self.assertEqual(response.status_code, 302)
        expired.refresh_from_db()
        self.assertIsNotNone(expired.revoked_at)
        grants = WebappPublicGrant.objects.filter(app=self.app, slug="dashboard")
        self.assertEqual(grants.count(), 2)
        self.assertEqual(WebappPublicGrant.live().filter(app=self.app, slug="dashboard").count(), 1)

    def test_revoke_stamps_and_keeps_row(self) -> None:
        grant = self._grant(slug="dashboard")
        self.client.force_login(self.admin)
        response = self.client.post(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke/")
        self.assertEqual(response.status_code, 200)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(grant.revoked_by, self.admin)

    def test_revoke_confirm_uses_standard_modal_and_refreshes_panel(self) -> None:
        grant = self._grant(slug="dashboard")
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke-confirm/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, "Revoke Public Access")
        self.assertContains(response, "dashboard.staging")
        self.assertContains(response, 'hx-target="#public-access-section"')
        self.assertContains(response, 'hx-swap="outerHTML"')
        self.assertContains(response, 'hx-push-url="false"')

    def test_panel_revoke_button_opens_modal_without_browser_confirm(self) -> None:
        grant = self._grant(slug="dashboard")
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/", HTTP_HX_REQUEST="true")
        self.assertContains(response, f"/public-access/{grant.id}/revoke-confirm/")
        self.assertNotContains(response, "hx-confirm")

    def test_member_cannot_revoke(self) -> None:
        grant = self._grant(slug="dashboard")
        self.client.force_login(self.member)
        response = self.client.post(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke/")
        self.assertEqual(response.status_code, 403)
        grant.refresh_from_db()
        self.assertIsNone(grant.revoked_at)

    def test_member_cannot_open_revoke_confirm(self) -> None:
        grant = self._grant(slug="dashboard")
        self.client.force_login(self.member)
        response = self.client.get(f"/apps/{self.app.slug}/public-access/{grant.id}/revoke-confirm/")
        self.assertEqual(response.status_code, 403)

    def test_confirm_page_renders_hostname_for_admin(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/apps/{self.app.slug}/public-access/new?slug=dashboard&env=staging",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wolfie.staging.example.com")
        self.assertContains(response, 'value="dashboard"')

    def test_confirm_page_ignores_invalid_prefill_slug(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/apps/{self.app.slug}/public-access/new?slug=__admin&env=staging",
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "__admin")

    def test_panel_shows_live_grant_url(self) -> None:
        self._grant(slug="dashboard")
        self.client.force_login(self.admin)
        response = self.client.get(f"/apps/{self.app.slug}/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "https://dashboard.wolfie.staging.example.com/")
