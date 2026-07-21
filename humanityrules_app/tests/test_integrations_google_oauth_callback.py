"""Tests for /integrations/user/google/callback/ — persists refresh_token + grants on HUMR."""

import base64
import hashlib
import json
from unittest.mock import MagicMock, patch

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


def _fake_id_token(claims: dict) -> str:
    """Build an unsigned JWT whose payload is *claims* (the callback never verifies signatures)."""
    segment = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{segment}."


GOOGLE_TOKEN_RESPONSE = {
    "access_token": "ya29.access",
    "refresh_token": "1//refresh",
    "scope": "https://www.googleapis.com/auth/gmail.readonly openid email",
    "token_type": "Bearer",
    "expires_in": 3599,
    "id_token": _fake_id_token(claims={"email": "vmendi@gmail.com", "sub": "google-sub-1"}),
}


class _CallbackTestBase(TestCase):

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

        IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=VALID_WEB_CONFIG,
        )

    def _seed_flow(self, state: str, rd: str, env_id: str, app_slug: str, owner_username: str,
                   mode: str = "connect", prior_row_id: str = "", prior_token: str = "") -> None:
        session = self.client.session
        flows = session.get("google_oauth_flows", {})
        flows[state] = {
            "rd": rd,
            "env_id": env_id,
            "app_slug": app_slug,
            "owner_username": owner_username,
            "mode": mode,
            "selection": {"gmail": "read"},
            "requested_scopes": ["openid", "email", "https://www.googleapis.com/auth/gmail.readonly"],
            "prior_row_id": prior_row_id,
            "prior_token_sha256": hashlib.sha256(prior_token.encode()).hexdigest() if prior_token else "",
            "created_at": 0.0,
        }
        session["google_oauth_flows"] = flows
        session.save()

    def _patched_google(self, body: dict | None):
        """Context manager that stubs Google's token-exchange POST."""
        token_response = MagicMock()
        token_response.json.return_value = body if body is not None else GOOGLE_TOKEN_RESPONSE
        token_response.raise_for_status.return_value = None
        return patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=token_response,
        )


class TestIntegrationsGoogleCallbackHappyPath(_CallbackTestBase):

    def test_persists_row_on_humr_and_redirects(self) -> None:
        self._seed_flow(
            state="stst",
            rd="https://hermes.dev.example.com/settings/connections",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_google(body=None) as post_mock:
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "auth-code", "state": "stst"},
            )

        # Google token exchange happened with expected shape.
        post_mock.assert_called_once()
        args, kwargs = post_mock.call_args
        self.assertEqual(args[0], "https://oauth2.googleapis.com/token")
        self.assertEqual(kwargs["data"]["code"], "auth-code")
        self.assertEqual(kwargs["data"]["grant_type"], "authorization_code")
        self.assertEqual(
            kwargs["data"]["redirect_uri"],
            "http://testserver/integrations/user/google/callback/",
        )

        # One IntegrationUserCredential row, keyed by (owner, env, app_slug, provider).
        row = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
        )
        self.assertEqual(row.credentials["refresh_token"], "1//refresh")
        self.assertIn("gmail.readonly", row.config["scope"])
        self.assertIsNone(row.last_refreshed_at)

        # Google identity from the id_token lands in row metadata.
        self.assertEqual(row.metadata["google_email"], "vmendi@gmail.com")
        self.assertEqual(row.metadata["google_sub"], "google-sub-1")

        # Redirected back to rd with ?connected=google appended, flow popped.
        self.assertEqual(response.status_code, 302)
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertIn("connected=google", response["Location"])
        self.assertEqual(self.client.session["google_oauth_flows"], {})

    def test_reconnect_updates_row_in_place(self) -> None:
        """Running through consent again should update the existing row, not duplicate."""
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "old-refresh"},
            config={"scope": "stale-scope"},
        )
        self._seed_flow(
            state="stst",
            rd="https://hermes.dev.example.com/x",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_google(body=None):
            self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )

        rows = IntegrationUserCredential.objects.filter(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().credentials["refresh_token"], "1//refresh")
        self.assertIn("gmail.readonly", rows.first().config["scope"])

    def test_expansion_without_new_refresh_token_preserves_prior_token(self) -> None:
        """Google may omit refresh_token on re-consent; an expansion keeps the live one."""
        prior = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "still-alive"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
            metadata={"google_sub": "google-sub-1"},
        )
        self._seed_flow(
            state="stst",
            rd="https://hermes.dev.example.com/x",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
            prior_row_id=str(prior.id),
            prior_token="still-alive",
        )
        body_without_refresh = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/calendar.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
            "id_token": _fake_id_token(claims={"email": "vmendi@gmail.com", "sub": "google-sub-1"}),
        }

        with self._patched_google(body=body_without_refresh):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("connected=google", response["Location"])
        row = IntegrationUserCredential.objects.get(pk=prior.pk)
        self.assertEqual(row.credentials["refresh_token"], "still-alive")
        self.assertIn("calendar.readonly", row.config["scope"])

    def test_expansion_preserve_rejects_account_switch(self) -> None:
        """No-refresh-token preservation must not stitch account A's token to account B's identity."""
        prior = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "account-A-token"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
            metadata={"google_sub": "account-A"},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        prior_row_id=str(prior.id), prior_token="account-A-token")
        body = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
            "id_token": _fake_id_token(claims={"email": "other@gmail.com", "sub": "account-B"}),
        }

        with self._patched_google(body=body):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_no_refresh_token", response["Location"])
        row = IntegrationUserCredential.objects.get(pk=prior.pk)
        self.assertEqual(row.credentials["refresh_token"], "account-A-token")
        self.assertEqual(row.metadata["google_sub"], "account-A")

    def test_two_pending_flows_coexist(self) -> None:
        """A second tab's pending flow must not invalidate the first tab's."""
        self._seed_flow(state="tab-one", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        self._seed_flow(state="tab-two", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "tab-one"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("connected=google", response["Location"])
        self.assertIn("tab-two", self.client.session["google_oauth_flows"])


class TestIntegrationsGoogleCallbackRejections(_CallbackTestBase):

    def test_rejects_state_mismatch(self) -> None:
        self._seed_flow(state="expected", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "wrong"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())
        # The legitimate pending flow survives a wrong-state callback.
        self.assertIn("expected", self.client.session["google_oauth_flows"])

    def test_rejects_missing_state_in_session(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_google_error_with_known_state_redirects_to_rd_with_sentinel(self) -> None:
        """A user cancelling at Google's screen lands back on the WebUI, not a CP error page."""
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"error": "access_denied", "state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("hermes.dev.example.com", response["Location"])
        self.assertIn("error=google_denied", response["Location"])
        self.assertEqual(self.client.session["google_oauth_flows"], {})

    def test_google_error_without_known_state_returns_400(self) -> None:
        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"error": "access_denied"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("access_denied", response.content.decode())

    def test_rejects_when_authenticated_user_mismatches_session_payload(self) -> None:
        other = User.objects.create_user(
            username="someone-else",
            email="se@example.com",
            password="pw",
            current_organization=self.org,
        )
        self.client.force_login(other)
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_env_missing_redirects_with_sentinel(self) -> None:
        """Once the payload is trusted, terminal failures ride the rd sentinel, not a CP 400."""
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id="00000000-0000-0000-0000-000000000000", app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_exchange_failed", response["Location"])

    def test_org_membership_lost_mid_flow_redirects_with_sentinel(self) -> None:
        """The env lookup is org-scoped: leaving the org between /start and callback rejects."""
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        OrganizationMembership.objects.filter(user=self.user).delete()

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_exchange_failed", response["Location"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_app_ownership_lost_mid_flow_redirects_with_sentinel(self) -> None:
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        ResourceTag.objects.filter(app=self.app, key="owner").delete()

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_exchange_failed", response["Location"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_missing_code_after_narrow_sends_disconnected_transition(self) -> None:
        """Even a code-less return after a destructive narrow must flip the card."""
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        mode="narrow")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        self.assertIn("error=google_narrow_incomplete", response["Location"])

    def test_stale_initial_connect_after_narrow_is_rejected(self) -> None:
        """Codex counterexample: an initial connect (no prior_row_id) from another
        session must not resurrect a grant a later narrow revoked."""
        import time as _time
        # A row created (by some other flow) then narrowed to a tombstone,
        # all AFTER this flow's created_at (seeded as 0.0).
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={},
            config={"scope": ""},
            metadata={"revoked_at_epoch": _time.time()},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_stale_flow", response["Location"])
        row = IntegrationUserCredential.objects.get(owner_user=self.user, app_slug="hermes")
        self.assertEqual(row.credentials, {})  # tombstone untouched

    def test_pending_narrow_callback_after_disconnect_is_rejected(self) -> None:
        """An explicit Disconnect stamps a newer revocation epoch: a narrow flow
        that was still awaiting consent must not reconnect over it."""
        import time as _time
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={},
            config={"scope": ""},
            metadata={"revoked_at_epoch": _time.time()},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        mode="narrow")

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        self.assertIn("error=google_narrow_incomplete", response["Location"])
        row = IntegrationUserCredential.objects.get(owner_user=self.user, app_slug="hermes")
        self.assertEqual(row.credentials, {})

    def test_marker_survives_successful_reconnect_and_blocks_older_flow(self) -> None:
        """Codex round-5 counterexample: flow B legitimately reconnects over a
        tombstone; the high-water mark must survive so older flow A still trips."""
        import time as _time
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={},
            config={"scope": ""},
            metadata={"revoked_at_epoch": _time.time()},
        )
        # Flow B: started AFTER the revocation → passes the guard.
        session = self.client.session
        flows = session.get("google_oauth_flows", {})
        flows["flow-b"] = {
            "rd": "https://hermes.dev.example.com/x",
            "env_id": str(self.env.id),
            "app_slug": "hermes",
            "owner_username": "vmendi",
            "mode": "connect",
            "selection": {"gmail": "read"},
            "requested_scopes": ["openid", "email"],
            "prior_row_id": "",
            "prior_token_sha256": "",
            "created_at": _time.time() + 1,
        }
        session["google_oauth_flows"] = flows
        session.save()
        with self._patched_google(body=None):
            response_b = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "b", "state": "flow-b"},
            )
        self.assertEqual(response_b.status_code, 302)
        self.assertIn("connected=google", response_b["Location"])
        row = IntegrationUserCredential.objects.get(owner_user=self.user, app_slug="hermes")
        self.assertEqual(row.credentials["refresh_token"], "1//refresh")
        self.assertIn("revoked_at_epoch", row.metadata)  # high-water mark survived

        # Flow A: started BEFORE the revocation (created_at=0) → still rejected,
        # and its freshly recreated grant gets best-effort revoked.
        self._seed_flow(state="flow-a", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        with self._patched_google(body=None) as post_mock:
            response_a = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "a", "state": "flow-a"},
            )
        self.assertEqual(response_a.status_code, 302)
        self.assertIn("error=google_stale_flow", response_a["Location"])
        row.refresh_from_db()
        self.assertEqual(row.credentials["refresh_token"], "1//refresh")  # B untouched
        revoke_calls = [c for c in post_mock.call_args_list if "revoke" in str(c.args[0] if c.args else "")]
        self.assertEqual(len(revoke_calls), 1)
        self.assertEqual(revoke_calls[0].kwargs["data"]["token"], "1//refresh")

    def test_cross_target_revocation_blocks_pending_flow(self) -> None:
        """Codex round-6 counterexample: revocation is Google-account-global, so a
        narrow on app X must also void a pending flow for app Y (same account)."""
        import time as _time
        other_app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            repository=self.repository,
            name="Hermes2",
            slug="hermes2",
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
            app=other_app,
            key="owner",
            value=self.user.username,
        )
        # App X (hermes2): tombstone from a narrow, stamped AFTER flow A started,
        # same Google account as this consent's id_token (google-sub-1).
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes2",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={},
            config={"scope": ""},
            metadata={"revoked_at_epoch": _time.time(), "google_sub": "google-sub-1"},
        )
        # Flow A targets app Y (hermes), started at created_at=0.
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with self._patched_google(body=None) as post_mock:
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_stale_flow", response["Location"])
        # No row stored for app Y, and the recreated grant was revoked upstream.
        self.assertFalse(IntegrationUserCredential.objects.filter(app_slug="hermes").exists())
        revoke_calls = [c for c in post_mock.call_args_list if "revoke" in str(c.args[0] if c.args else "")]
        self.assertEqual(len(revoke_calls), 1)

    def test_other_account_revocation_does_not_block_flow(self) -> None:
        """A tombstone for a DIFFERENT Google account must not veto this consent."""
        import time as _time
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={},
            config={"scope": ""},
            metadata={"revoked_at_epoch": _time.time(), "google_sub": "somebody-else"},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        # The consent's id_token sub (google-sub-1) differs from the tombstone's,
        # so the flow completes normally.
        self.assertEqual(response.status_code, 302)
        self.assertIn("connected=google", response["Location"])
        row = IntegrationUserCredential.objects.get(owner_user=self.user, app_slug="hermes")
        self.assertEqual(row.credentials["refresh_token"], "1//refresh")

    def test_google_error_with_mismatched_user_is_bare_400(self) -> None:
        """A provider error must not bypass the user-ownership boundary."""
        other = User.objects.create_user(
            username="someone-else", email="se2@example.com", password="pw",
            current_organization=self.org,
        )
        self.client.force_login(other)
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"error": "access_denied", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_mode_in_payload_is_rejected(self) -> None:
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        mode="bogus")
        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"code": "c", "state": "stst"},
        )
        self.assertEqual(response.status_code, 400)

    def test_stale_connect_flow_after_row_removal_is_rejected(self) -> None:
        """A consent that started against a row a narrow/disconnect since removed
        must not resurrect the credential with its pre-revocation scopes."""
        prior = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "about-to-be-narrowed"},
            config={"scope": "https://www.googleapis.com/auth/gmail.modify"},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        prior_row_id=str(prior.id), prior_token="about-to-be-narrowed")
        prior.delete()  # narrow (or disconnect) landed while the consent tab sat open

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_stale_flow", response["Location"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_token_exchange_failure_redirects_to_rd_with_sentinel(self) -> None:
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")

        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_exchange_failed", response["Location"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_no_refresh_token_on_initial_connect_redirects_with_sentinel(self) -> None:
        """Without a refresh_token (and no live prior row), HUMR can't serve tokens later."""
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi")
        body_without_refresh = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
        }

        with self._patched_google(body=body_without_refresh):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_no_refresh_token", response["Location"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_no_refresh_token_after_narrow_redirects_with_sentinel(self) -> None:
        """A narrow revoked the old token, so there is nothing to preserve — must reject."""
        prior = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "already-revoked"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
        )
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        mode="narrow", prior_row_id=str(prior.id))
        body_without_refresh = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
        }

        with self._patched_google(body=body_without_refresh):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        # A dead narrow flow really changed state (grant revoked): the WebUI
        # gets the disconnected transition plus the narrow-specific error.
        self.assertIn("disconnected=google", response["Location"])
        self.assertIn("error=google_narrow_incomplete", response["Location"])

    def test_no_refresh_token_with_stale_fingerprint_rejects(self) -> None:
        """A concurrent flow replaced the row: adopting its token would stitch two grants."""
        prior = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "token-B-from-other-flow"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
        )
        # Flow A fingerprinted a token the row no longer holds.
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        prior_row_id=str(prior.id), prior_token="token-A-long-gone")
        body_without_refresh = {
            "access_token": "ya29.access",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
            "token_type": "Bearer",
            "expires_in": 3599,
        }

        with self._patched_google(body=body_without_refresh):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=google_no_refresh_token", response["Location"])
        # The other flow's row is untouched.
        row = IntegrationUserCredential.objects.get(pk=prior.pk)
        self.assertEqual(row.credentials["refresh_token"], "token-B-from-other-flow")

    def test_cancel_after_narrow_sends_disconnected_transition(self) -> None:
        self._seed_flow(state="stst", rd="https://hermes.dev.example.com/x",
                        env_id=str(self.env.id), app_slug="hermes", owner_username="vmendi",
                        mode="narrow")

        response = self.client.get(
            reverse("integrations_user_google_callback"),
            {"error": "access_denied", "state": "stst"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("disconnected=google", response["Location"])
        self.assertIn("error=google_narrow_incomplete", response["Location"])


class TestIntegrationsGoogleCallbackRdAppend(_CallbackTestBase):

    def test_appends_connected_param_to_rd_with_existing_query(self) -> None:
        self._seed_flow(
            state="stst",
            rd="https://hermes.dev.example.com/x?foo=bar",
            env_id=str(self.env.id),
            app_slug="hermes",
            owner_username="vmendi",
        )

        with self._patched_google(body=None):
            response = self.client.get(
                reverse("integrations_user_google_callback"),
                {"code": "c", "state": "stst"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("foo=bar", response["Location"])
        self.assertIn("connected=google", response["Location"])
