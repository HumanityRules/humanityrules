"""Tests for the control-plane connect-a-login flow (shared device-flow OAuth credentials).

Covers org-admin gating, the Codex two-step device handshake and the Nous one-step
handshake (both upstream-mocked), self-poll pending/timeout, store-on-the-right-table
routing, and the platform-owner gate on the global "all customers" scope.
"""

import base64
import json
import time
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from humanityrules_app.models import (
    IntegrationSharedCredential,
    Organization,
    PlatformSharedCredential,
    User,
)
from humanityrules_app.services import abac_service
from humanityrules_app.views.integrations import provider_nous, provider_openai_codex, shared_credential_store
from humanityrules_app.views.integrations.org_shared_keys import SESSION_KEY

HTMX = {"HTTP_HX_REQUEST": "true"}
# The connect-a-login flow now starts from the unified add dialog (dispatched by provider kind).
CONNECT_URL = "/integrations/org/provider-keys/add/"
POLL_URL = "/integrations/org/shared-logins/poll/"
CANCEL_URL = "/integrations/org/shared-logins/cancel/"


def _resp(status_code: int, body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


def _codex_jwt(account_id: str) -> str:
    """Build a fake (unsigned) Codex access-token JWT carrying the account-id claim."""
    payload = {"exp": int(time.time()) + 3600, "https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"hdr.{encoded}.sig"


# Codex device handshake: usercode (start) → deviceauth/token (approved) → oauth/token (tokens).
CODEX_USERCODE = _resp(200, {"user_code": "ABCD-1234", "device_auth_id": "dev-1", "interval": 5})
CODEX_APPROVED = _resp(200, {"authorization_code": "ac-1", "code_verifier": "cv-1"})
CODEX_TOKENS = _resp(200, {"refresh_token": "rt-codex", "access_token": _codex_jwt("acct_123")})
CODEX_PENDING = _resp(403, {})

# Nous device handshake: device/code (start) → token (approved, one step).
NOUS_DEVICE_CODE = _resp(200, {
    "device_code": "dc-1", "user_code": "NOUS-1",
    "verification_uri_complete": "https://portal.nousresearch.com/device?code=NOUS-1",
    "expires_in": 600, "interval": 5,
})
NOUS_TOKENS = _resp(200, {"access_token": "at-nous", "refresh_token": "rt-nous"})


class ConnectTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Login Org", slug="login-org")
        self.admin = User.objects.create_user(username="admin", password="pw", current_organization=self.org)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)
        self.member = User.objects.create_user(username="bob", password="pw", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.member, role="member")

    def _seed_session(self, provider: str, ui_scope: str, expires_offset: int) -> None:
        """Put a valid in-flight device-login session on the test client (bypasses connect)."""
        session = self.client.session
        session[SESSION_KEY] = {
            "provider": provider, "ui_scope": ui_scope, "target_user_id": "", "target_workspace_id": "",
            "edit_credential_id": "", "opaque": {"device_auth_id": "dev-1", "user_code": "ABCD-1234", "device_code": "dc-1"},
            "user_code": "ABCD-1234", "verification_uri": "https://example.test/device", "interval": 3,
            "expires_at": time.time() + expires_offset,
        }
        session.save()


class TestAccessControl(ConnectTestBase):

    def test_member_forbidden(self) -> None:
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(CONNECT_URL, **HTMX).status_code, 403)

    def test_admin_sees_form(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(CONNECT_URL, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Add shared credential", response.content.decode())


class TestCodexConnect(ConnectTestBase):

    def test_start_renders_user_code_and_sets_session(self) -> None:
        self.client.force_login(self.admin)
        with patch.object(provider_openai_codex.httpx, "post", return_value=CODEX_USERCODE):
            response = self.client.post(CONNECT_URL, data={"provider": "openai-codex", "scope": "everyone"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("ABCD-1234", response.content.decode())
        self.assertIn(SESSION_KEY, self.client.session)

    def test_poll_completes_and_stores_everyone_share(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="everyone", expires_offset=600)
        with patch.object(provider_openai_codex.httpx, "post", side_effect=[CODEX_APPROVED, CODEX_TOKENS]):
            response = self.client.get(POLL_URL, **HTMX)
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openai-codex")
        self.assertEqual(cred.scope, "everyone")
        self.assertEqual(cred.credentials, {"refresh_token": "rt-codex"})
        self.assertEqual(cred.metadata["chatgpt_account_id"], "acct_123")
        self.assertIn("connected_at", cred.metadata)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_poll_pending_keeps_polling(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="everyone", expires_offset=600)
        with patch.object(provider_openai_codex.httpx, "post", return_value=CODEX_PENDING):
            response = self.client.get(POLL_URL, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn("Waiting for approval", response.content.decode())
        self.assertFalse(IntegrationSharedCredential.objects.exists())
        self.assertIn(SESSION_KEY, self.client.session)

    def test_poll_timeout_shows_error(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="everyone", expires_offset=-10)
        response = self.client.get(POLL_URL, **HTMX)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn("timed out", response.content.decode())
        self.assertFalse(IntegrationSharedCredential.objects.exists())
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_poll_missing_session_shows_error(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(POLL_URL, **HTMX)
        self.assertIn("expired", response.content.decode())


class TestNousConnect(ConnectTestBase):

    def test_poll_completes_and_stores_share(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="nous", ui_scope="everyone", expires_offset=600)
        with patch.object(provider_nous.httpx, "post", return_value=NOUS_TOKENS):
            response = self.client.get(POLL_URL, **HTMX)
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="nous")
        self.assertEqual(cred.credentials, {"refresh_token": "rt-nous"})

    def test_start_renders_user_code(self) -> None:
        self.client.force_login(self.admin)
        with patch.object(provider_nous.httpx, "post", return_value=NOUS_DEVICE_CODE):
            response = self.client.post(CONNECT_URL, data={"provider": "nous", "scope": "everyone"})
        self.assertIn("NOUS-1", response.content.decode())


class TestPlatformGate(ConnectTestBase):

    def test_non_owner_cannot_start_platform_connect(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(CONNECT_URL, data={"provider": "openai-codex", "scope": "platform"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("cannot create platform-wide", response.content.decode())
        self.assertNotIn(SESSION_KEY, self.client.session)

    @override_settings(HUMR_PLATFORM_OWNER_ORG_SLUG="login-org")
    def test_owner_poll_stores_platform_credential(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="platform", expires_offset=600)
        with patch.object(provider_openai_codex.httpx, "post", side_effect=[CODEX_APPROVED, CODEX_TOKENS]):
            response = self.client.get(POLL_URL, **HTMX)
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        self.assertFalse(IntegrationSharedCredential.objects.exists())
        cred = PlatformSharedCredential.objects.get(provider="openai-codex")
        self.assertEqual(cred.credentials, {"refresh_token": "rt-codex"})
        self.assertTrue(cred.enabled)

    @override_settings(HUMR_PLATFORM_OWNER_ORG_SLUG="login-org")
    def test_owner_poll_for_platform_blocked_after_losing_ownership(self) -> None:
        """Defense in depth: the gate is re-checked at poll time, not just at start."""
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="platform", expires_offset=600)
        # Ownership revoked between start and the completing poll.
        with override_settings(HUMR_PLATFORM_OWNER_ORG_SLUG="someone-else"):
            with patch.object(provider_openai_codex.httpx, "post", side_effect=[CODEX_APPROVED, CODEX_TOKENS]):
                response = self.client.get(POLL_URL, **HTMX)
        self.assertNotIn("HX-Trigger", response)
        self.assertFalse(PlatformSharedCredential.objects.exists())


class TestCancel(ConnectTestBase):

    def test_cancel_clears_session(self) -> None:
        self.client.force_login(self.admin)
        self._seed_session(provider="openai-codex", ui_scope="everyone", expires_offset=600)
        response = self.client.post(CANCEL_URL)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_KEY, self.client.session)


class TestReconnectClearsTokenCache(ConnectTestBase):
    """A reconnect rotates the shared refresh_token, so the cached access token now
    belongs to the old account. ``upsert_share`` must drop ``token_cache`` — otherwise
    ``run_shared_refresh_exchange`` (which serves the cache before re-reading the
    refresh_token) fans the wrong account's token to every broker until it lapses."""

    def _cred_with_cache(self) -> IntegrationSharedCredential:
        return IntegrationSharedCredential.objects.create(
            organization=self.org,
            provider="openai-codex",
            scope=IntegrationSharedCredential.Scope.EVERYONE,
            credentials={"refresh_token": "rt-old"},
            token_cache={"secrets": {"access_token": "stale"}, "expires_at": time.time() + 3600},
            created_by=self.admin,
        )

    def test_rotated_refresh_token_clears_cache(self) -> None:
        cred = self._cred_with_cache()
        target = shared_credential_store.ShareTarget(
            is_platform=False, scope=IntegrationSharedCredential.Scope.EVERYONE, target_user=None, target_workspace=None,
        )
        row, error = shared_credential_store.upsert_share(
            organization=self.org, provider="openai-codex", target=target,
            credentials={"refresh_token": "rt-new"}, metadata={}, created_by=self.admin, existing=cred,
        )
        self.assertIsNone(error)
        row.refresh_from_db()
        self.assertEqual(row.credentials, {"refresh_token": "rt-new"})
        self.assertEqual(row.token_cache, {})

    def test_retarget_without_secret_change_keeps_cache(self) -> None:
        cred = self._cred_with_cache()
        target = shared_credential_store.ShareTarget(
            is_platform=False, scope=IntegrationSharedCredential.Scope.USER, target_user=self.member, target_workspace=None,
        )
        # Same refresh_token, new audience (the re-target path): the cache is still valid.
        row, error = shared_credential_store.upsert_share(
            organization=self.org, provider="openai-codex", target=target,
            credentials={"refresh_token": "rt-old"}, metadata={}, created_by=self.admin, existing=cred,
        )
        self.assertIsNone(error)
        row.refresh_from_db()
        self.assertEqual(row.scope, IntegrationSharedCredential.Scope.USER)
        self.assertEqual(row.token_cache["secrets"], {"access_token": "stale"})
