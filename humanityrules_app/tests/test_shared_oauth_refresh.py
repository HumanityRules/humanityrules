"""Tests for the shared-OAuth central-refresh engine (Phase 2a).

A shared refresh_token (platform- or org-provisioned) is exchanged once on the
control plane and the access token is cached on the credential row, then fanned
out to the broker swarm. Codex is the first OAuth provider to use it. The engine
itself lives in ``provider_common.run_shared_refresh_exchange``; these tests drive
it through ``provider_openai_codex.refresh_outcome_from_shared`` with the upstream
token exchange mocked.
"""

import base64
import json
import time
from unittest.mock import MagicMock, patch

from humanityrules_app.models import PlatformSharedCredential
from humanityrules_app.tests.test_integrations_tokens_batch import _BatchTokensEndpointTestBase
from humanityrules_app.views.integrations import provider_openai_codex

from django.test import TestCase


def _codex_access_token(account_id: str | None, exp_offset_seconds: int) -> str:
    """Build a fake (unsigned) Codex access-token JWT carrying the account-id claim."""
    payload: dict = {"exp": int(time.time()) + exp_offset_seconds}
    if account_id is not None:
        payload["https://api.openai.com/auth"] = {"chatgpt_account_id": account_id}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"hdr.{encoded}.sig"


def _token_response(status_code: int, body: dict) -> MagicMock:
    """Build a mock httpx.Response with the given status and JSON body."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


class TestSharedCodexRefresh(TestCase):

    def _cred(self, credentials: dict) -> PlatformSharedCredential:
        return PlatformSharedCredential.objects.create(provider="openai-codex", credentials=credentials)

    def test_exchanges_and_caches(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        token = _codex_access_token(account_id="acct_123", exp_offset_seconds=3600)
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(200, {"access_token": token})) as post:
            outcome = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"access_token": token, "chatgpt_account_id": "acct_123"})
        post.assert_called_once()
        cred.refresh_from_db()
        self.assertEqual(cred.token_cache["secrets"]["chatgpt_account_id"], "acct_123")

    def test_cache_hit_skips_exchange(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        token = _codex_access_token(account_id="acct_123", exp_offset_seconds=3600)
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(200, {"access_token": token})) as post:
            provider_openai_codex.refresh_outcome_from_shared(credential=cred)
            cred.refresh_from_db()
            second = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        post.assert_called_once()
        self.assertEqual(second["secrets"]["chatgpt_account_id"], "acct_123")

    def test_rotation_persists_new_refresh_token(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        token = _codex_access_token(account_id="acct_123", exp_offset_seconds=3600)
        body = {"access_token": token, "refresh_token": "rt-2"}
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(200, body)):
            provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        cred.refresh_from_db()
        self.assertEqual(cred.credentials["refresh_token"], "rt-2")

    def test_revoked_returns_absent_and_clears_cache(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        # Stale cache (expired) so the engine runs the exchange instead of serving it.
        PlatformSharedCredential.objects.filter(pk=cred.pk).update(
            token_cache={"secrets": {"access_token": "old"}, "expires_at": time.time() - 10},
        )
        cred.refresh_from_db()
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(400, {"error": {"code": "invalid_grant"}})):
            outcome = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome, {"outcome": "absent"})
        cred.refresh_from_db()
        self.assertEqual(cred.token_cache, {})

    def test_transient_on_server_error(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(500, {})):
            outcome = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome, {"outcome": "transient"})

    def test_transient_when_token_has_no_account_id(self) -> None:
        cred = self._cred({"refresh_token": "rt-1"})
        token = _codex_access_token(account_id=None, exp_offset_seconds=3600)
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(200, {"access_token": token})):
            outcome = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome, {"outcome": "transient"})

    def test_missing_refresh_token_is_absent(self) -> None:
        cred = self._cred({})
        outcome = provider_openai_codex.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome, {"outcome": "absent"})


class TestPlatformCodexEndpoint(_BatchTokensEndpointTestBase):
    """A platform Codex credential flows through /api/integrations/tokens as the
    lowest-priority fallback, with the access token minted centrally on the CP."""

    def test_platform_codex_central_refresh(self) -> None:
        token = _codex_access_token(account_id="acct_xyz", exp_offset_seconds=3600)
        PlatformSharedCredential.objects.create(provider="openai-codex", credentials={"refresh_token": "rt-1"})
        with patch.object(provider_openai_codex.httpx, "post", return_value=_token_response(200, {"access_token": token})):
            status, body = self._post(
                body={"owner_username": "vmendi", "app_slug": "hermes", "providers": ["openai-codex"]},
                token=self.raw_token,
            )
        self.assertEqual(status, 200)
        result = body["results"]["openai-codex"]
        self.assertEqual(result["outcome"], "has_token")
        self.assertEqual(result["secrets"], {"access_token": token, "chatgpt_account_id": "acct_xyz"})
        self.assertTrue(result["metadata"]["platform_shared"])
