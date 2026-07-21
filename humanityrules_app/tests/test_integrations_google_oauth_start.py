"""Tests for /integrations/user/google/start/ — the OAuth kickoff view."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationConfig,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
)


VALID_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": [
        "http://testserver/integrations/user/google/callback/",
        "https://humanityrules.io/integrations/user/google/callback/",
    ],
}


class _GoogleStartTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="NextOrg",
            slug="nextorg",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org,
            name="Prod",
            aws_account_id="111122223333",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            shared_alb_hosted_zone="dev.example.com",
        )
        self.user = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.client.force_login(self.user)
        self.repository = Repository.objects.create(
            organization=self.org,
            provider="github",
            name="hermes",
            full_name="org/hermes",
            default_branch="main",
            clone_url="https://github.com/org/hermes.git",
        )
        self.workspace = Workspace.objects.create(
            organization=self.org,
            name="Engineering",
            slug="engineering",
        )
        self.app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            repository=self.repository,
            name="Hermes",
            slug="hermes",
            build_strategy=App.BuildStrategy.DOCKERFILE,
            environment=self.env,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
        )
        ResourceTag.objects.create(
            organization=self.org,
            resource_type=ResourceTag.ResourceType.APP,
            app=self.app,
            key="owner",
            value=self.user.username,
        )

        self.google_config = IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )

    def _params(self, rd: str) -> dict:
        return {"rd": rd, "app_slug": self.app.slug}


class TestIntegrationsGoogleStart(_GoogleStartTestBase):

    def test_login_required_when_unauthenticated(self) -> None:
        self.client.logout()
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        # The global LOGIN_URL points at WorkOS — we don't care about the destination,
        # just that the decorator kicked in.
        self.assertNotIn("accounts.google.com", response["Location"])

    def test_redirects_to_google_with_expected_params(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/settings/connections"),
        )
        self.assertEqual(response.status_code, 302)

        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "accounts.google.com")
        self.assertEqual(parsed.path, "/o/oauth2/auth")

        params = parse_qs(parsed.query)
        self.assertEqual(params["client_id"], ["cid-123.apps.googleusercontent.com"])
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["redirect_uri"], ["http://testserver/integrations/user/google/callback/"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", params["scope"][0])
        self.assertIn("openid", params["scope"][0])

    def test_stashes_state_and_payload_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        parsed = urlparse(response["Location"])
        state = parse_qs(parsed.query)["state"][0]

        payload = self.client.session["google_oauth_flows"][state]
        self.assertEqual(payload["rd"], "https://hermes.dev.example.com/x")
        self.assertEqual(payload["env_id"], str(self.env.id))
        self.assertEqual(payload["app_slug"], "hermes")
        self.assertEqual(payload["owner_username"], "vmendi")
        self.assertEqual(payload["mode"], "connect")
        self.assertIn("https://www.googleapis.com/auth/gmail.readonly", payload["requested_scopes"])

    def test_two_starts_keep_both_pending_flows(self) -> None:
        first = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        second = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        states = {
            parse_qs(urlparse(response["Location"]).query)["state"][0]
            for response in (first, second)
        }
        self.assertEqual(len(states), 2)
        self.assertEqual(set(self.client.session["google_oauth_flows"]), states)

    def test_exact_zone_host_matches(self) -> None:
        # rd host == env zone (no subdomain) should still match.
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_rejects_rd_with_unknown_host(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.other-domain.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session.get("google_oauth_flows", {}), {})

    def test_rejects_rd_with_suffix_lookalike(self) -> None:
        # "evil-dev.example.com" ends with the zone string "dev.example.com"
        # but is not a subdomain — must be rejected.
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://evil-dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_http_scheme(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="javascript:alert(1)"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_missing_rd(self) -> None:
        response = self.client.get(reverse("integrations_user_google_start"))
        self.assertEqual(response.status_code, 400)

    def test_errors_when_google_integration_not_configured(self) -> None:
        self.google_config.delete()
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("setup_google_oauth_client", response.content.decode())

    def test_fails_when_no_redirect_uri_matches_request_host(self) -> None:
        # OAuth client has registered URIs, but none match this request's host.
        self.google_config.config = {
            **self.google_config.config,
            "redirect_uris": ["https://some-other-host.example.com/integrations/user/google/callback/"],
        }
        self.google_config.save()

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("redirect_uri", response.content.decode())

    def test_picks_matching_redirect_uri_by_request_host(self) -> None:
        # Multiple URIs registered; picks the one matching the request host.
        self.google_config.config = {
            **self.google_config.config,
            "redirect_uris": [
                "https://humanityrules.io/integrations/user/google/callback/",
                "http://testserver/integrations/user/google/callback/",
                "https://humanityrules.ngrok.io/integrations/user/google/callback/",
            ],
        }
        self.google_config.save()

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.dev.example.com/x"),
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        params = parse_qs(parsed.query)
        self.assertEqual(params["redirect_uri"], ["http://testserver/integrations/user/google/callback/"])

    def test_ignores_envs_with_blank_hosted_zone(self) -> None:
        # A second env exists but has no hosted zone — it must not match any rd.
        Environment.objects.create(
            aws_account=self.aws_account,
            name="Dev",
            slug="dev",
            aws_region="us-east-1",
            shared_alb_hosted_zone="",
        )
        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://anything.com/x"),
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_rd_pointing_at_stranger_org_env(self) -> None:
        # An env exists in another org whose zone the rd would suffix-match.
        # The authenticated user is not a member of that org, so the start view
        # must refuse to bind the OAuth flow to it — otherwise the credential
        # row would be written against a stranger env (orphan, unreachable
        # post-fix, but still pollution + audit confusion).
        other_org = Organization.objects.create(name="OtherOrg", slug="otherorg")
        other_aws = AWSAccount.objects.create(
            organization=other_org, name="OtherProd", aws_account_id="999988887777",
        )
        Environment.objects.create(
            aws_account=other_aws,
            name="OtherStaging",
            slug="other-staging",
            aws_region="us-east-1",
            shared_alb_hosted_zone="stranger.example.com",
        )

        response = self.client.get(
            reverse("integrations_user_google_start"),
            self._params(rd="https://hermes.stranger.example.com/x"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session.get("google_oauth_flows", {}), {})


class TestIntegrationsGoogleStartScopeSelection(_GoogleStartTestBase):

    def _start(self, products: str | None) -> HttpResponse:
        params = self._params(rd="https://hermes.dev.example.com/x")
        if products is not None:
            params["products"] = products
        return self.client.get(reverse("integrations_user_google_start"), params)

    def _scope_of(self, response) -> str:
        return parse_qs(urlparse(response["Location"]).query)["scope"][0]

    def test_absent_products_param_requests_legacy_all_read(self) -> None:
        response = self._start(products=None)
        self.assertEqual(response.status_code, 302)
        scope = self._scope_of(response)
        for fragment in ("gmail.readonly", "calendar.readonly", "drive.readonly",
                         "contacts.readonly", "spreadsheets.readonly", "documents.readonly"):
            self.assertIn(fragment, scope)
        self.assertNotIn("gmail.modify", scope)

    def test_selection_limits_requested_scopes(self) -> None:
        response = self._start(products="gmail:read,calendar:read")
        self.assertEqual(response.status_code, 302)
        scope = self._scope_of(response)
        self.assertIn("gmail.readonly", scope)
        self.assertIn("calendar.readonly", scope)
        self.assertNotIn("drive", scope)
        self.assertNotIn("documents", scope)

    def test_write_level_swaps_in_write_scopes(self) -> None:
        response = self._start(products="gmail:write,sheets:write,calendar:write")
        self.assertEqual(response.status_code, 302)
        scope = self._scope_of(response)
        self.assertIn("gmail.modify", scope)
        self.assertNotIn("gmail.readonly", scope)
        self.assertIn("auth/spreadsheets", scope)
        self.assertNotIn("spreadsheets.readonly", scope)
        # Calendar write keeps readonly (read everything) and adds events write,
        # NOT the full calendar-management scope.
        self.assertIn("calendar.readonly", scope)
        self.assertIn("calendar.events", scope)
        self.assertNotIn("auth/calendar ", scope + " ")

    def test_first_connect_uses_incremental_auth(self) -> None:
        response = self._start(products="gmail:read")
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(params["include_granted_scopes"], ["true"])

    def test_invalid_products_values_return_400(self) -> None:
        # Present-but-invalid must never fall back to a wider request.
        for invalid in ("", "gmail", "gmail:", "gmail:admin", "unknown:read",
                        "gmail:read,gmail:write", "gmail:off"):
            with self.subTest(products=invalid):
                response = self._start(products=invalid)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.session.get("google_oauth_flows", {}), {})


class TestIntegrationsGoogleNarrowFlow(_GoogleStartTestBase):

    def setUp(self) -> None:
        super().setUp()
        self.row = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "1//live"},
            config={"scope": (
                "https://www.googleapis.com/auth/gmail.modify "
                "https://www.googleapis.com/auth/calendar.readonly openid email"
            )},
            metadata={"google_email": "vmendi@gmail.com"},
        )

    def _narrow_post(self, products: str) -> HttpResponse:
        return self.client.post(reverse("integrations_user_google_narrow"), {
            "rd": "https://hermes.dev.example.com/x",
            "app_slug": self.app.slug,
            "products": products,
        })

    def test_narrowing_start_renders_confirmation_instead_of_redirecting(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_start"),
            {**self._params(rd="https://hermes.dev.example.com/x"), "products": "gmail:read,calendar:read"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("google/narrow/", body)
        self.assertIn("Gmail", body)  # the reduced product is named
        self.assertIn("vmendi@gmail.com", body)
        # No consent flow was stashed and nothing was revoked or deleted.
        self.assertEqual(self.client.session.get("google_oauth_flows", {}), {})
        self.assertTrue(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())

    def test_upgrade_is_not_narrowing(self) -> None:
        # calendar read → write with gmail staying at write: pure expansion.
        response = self.client.get(
            reverse("integrations_user_google_start"),
            {**self._params(rd="https://hermes.dev.example.com/x"), "products": "gmail:write,calendar:write"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_narrow_post_revokes_tombstones_and_redirects_without_incremental_auth(self) -> None:
        revoke_response = MagicMock()
        revoke_response.status_code = 200
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=revoke_response,
        ) as post_mock:
            response = self._narrow_post(products="gmail:read,calendar:read")

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.kwargs["data"]["token"], "1//live")
        # The row survives as a tombstone: blanked credentials plus the
        # revoked_at_epoch generation marker that invalidates older flows.
        tombstone = IntegrationUserCredential.objects.get(pk=self.row.pk)
        self.assertEqual(tombstone.credentials, {})
        self.assertEqual(tombstone.config, {"scope": ""})
        self.assertIn("revoked_at_epoch", tombstone.metadata)

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.netloc, "accounts.google.com")
        params = parse_qs(parsed.query)
        self.assertNotIn("include_granted_scopes", params)
        self.assertIn("gmail.readonly", params["scope"][0])
        state = params["state"][0]
        self.assertEqual(self.client.session["google_oauth_flows"][state]["mode"], "narrow")

    def test_narrow_post_keeps_row_when_google_rejects_revoke(self) -> None:
        revoke_response = MagicMock()
        revoke_response.status_code = 500
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=revoke_response,
        ):
            response = self._narrow_post(products="gmail:read,calendar:read")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())
        self.assertEqual(self.client.session.get("google_oauth_flows", {}), {})

    def test_narrow_post_without_narrowing_selection_reroutes_as_connect(self) -> None:
        # A stale confirmation (row already narrower) must not revoke anything —
        # it becomes an ordinary expansion: incremental auth ON, prior row
        # captured so an omitted refresh_token can still be preserved.
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
        ) as post_mock:
            response = self._narrow_post(products="gmail:write,calendar:write")

        post_mock.assert_not_called()
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.netloc, "accounts.google.com")
        params = parse_qs(parsed.query)
        self.assertEqual(params["include_granted_scopes"], ["true"])
        flow = self.client.session["google_oauth_flows"][params["state"][0]]
        self.assertEqual(flow["mode"], "connect")
        self.assertEqual(flow["prior_row_id"], str(self.row.id))
        self.assertTrue(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())

    def test_narrow_post_preflights_redirect_uri_before_revoking(self) -> None:
        # A config error must fail BEFORE the destructive revoke, not after.
        self.google_config.config = {
            **self.google_config.config,
            "redirect_uris": ["https://some-other-host.example.com/integrations/user/google/callback/"],
        }
        self.google_config.save()

        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
        ) as post_mock:
            response = self._narrow_post(products="gmail:read,calendar:read")

        post_mock.assert_not_called()
        self.assertEqual(response.status_code, 400)
        self.assertTrue(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())

    def test_narrow_post_requires_csrf_token(self) -> None:
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(reverse("integrations_user_google_narrow"), {
            "rd": "https://hermes.dev.example.com/x",
            "app_slug": self.app.slug,
            "products": "gmail:read,calendar:read",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())

    def test_drive_only_selection_dropping_docs_is_not_narrowing(self) -> None:
        # Granted: drive read (which implies docs/sheets read). Requesting
        # docs:off while keeping drive:read removes no actual capability.
        self.row.config = {"scope": (
            "https://www.googleapis.com/auth/drive.readonly "
            "https://www.googleapis.com/auth/documents.readonly openid email"
        )}
        self.row.save()
        response = self.client.get(
            reverse("integrations_user_google_start"),
            {**self._params(rd="https://hermes.dev.example.com/x"), "products": "drive:read"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts.google.com", response["Location"])

    def test_narrow_get_is_not_allowed(self) -> None:
        response = self.client.get(reverse("integrations_user_google_narrow"), {
            "rd": "https://hermes.dev.example.com/x",
            "app_slug": self.app.slug,
            "products": "gmail:read",
        })
        self.assertEqual(response.status_code, 405)
