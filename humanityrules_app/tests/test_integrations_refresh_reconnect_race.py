"""Tests for run_refresh_exchange's compare-and-swap against concurrent reconnects.

A refresh holds a stale row instance across the upstream network call. If the
OAuth callback replaces the row's refresh_token meanwhile, the stale refresh
must neither delete the fresh credential (on invalid_grant for the OLD token)
nor write the old token back over it (on success for the OLD token). The
exchange callable in these tests simulates the interleaving by mutating the
row mid-flight.
"""

import logging

from django.test import TestCase

from humanityrules_app.models import (
    AWSAccount,
    Environment,
    IntegrationUserCredential,
    Organization,
    User,
)
from humanityrules_app.views.integrations import provider_common

logger = logging.getLogger(__name__)


class TestRefreshReconnectRace(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="NextOrg", slug="nextorg")
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Prod", aws_account_id="111122223333",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="Staging", slug="staging", aws_region="us-east-1",
        )
        self.user = User.objects.create_user(
            username="vmendi", email="vmendi@example.com", password="pw", current_organization=self.org,
        )
        self.row = IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "old-token"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
        )

    def _run(self, exchange) -> dict:
        return provider_common.run_refresh_exchange(
            provider=IntegrationUserCredential.Provider.GOOGLE,
            logger=logger,
            environment=self.env,
            owner_user=self.user,
            app_slug="hermes",
            exchange=exchange,
            build_secrets=lambda access_token, response: provider_common.RefreshSecrets(
                secrets={"access_token": access_token},
                expires_in=int(response.get("expires_in", 0)),
                row_metadata={},
                row_config={},
            ),
            outcome_metadata=None,
            tombstone_on_revoke=False,
        )

    def _reconnect_concurrently(self) -> None:
        """Simulate the OAuth callback replacing the row while the exchange is in flight."""
        IntegrationUserCredential.objects.filter(pk=self.row.pk).update(
            credentials={"refresh_token": "fresh-token"},
        )

    def test_stale_invalid_grant_does_not_delete_reconnected_row(self) -> None:
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            self._reconnect_concurrently()
            return provider_common.ExchangeResult(response=None, revoked=True, error=None)

        outcome = self._run(exchange=exchange)

        # transient, NOT absent: the revoked verdict applied to the old token
        # only, and an authoritative absent would flip the freshly reconnected
        # card to disconnected on the broker.
        self.assertEqual(outcome["outcome"], "transient")
        row = IntegrationUserCredential.objects.get(pk=self.row.pk)
        self.assertEqual(row.credentials["refresh_token"], "fresh-token")

    def test_stale_success_does_not_overwrite_reconnected_token(self) -> None:
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            self._reconnect_concurrently()
            return provider_common.ExchangeResult(
                response={"access_token": "ya29.x", "expires_in": 3599, "refresh_token": "rotated-old"},
                revoked=False,
                error=None,
            )

        outcome = self._run(exchange=exchange)

        # The exchanged token is still served for this one outcome...
        self.assertEqual(outcome["outcome"], "has_token")
        # ...but the reconnected credential is left untouched.
        row = IntegrationUserCredential.objects.get(pk=self.row.pk)
        self.assertEqual(row.credentials["refresh_token"], "fresh-token")

    def test_undisturbed_refresh_still_rotates_and_stamps(self) -> None:
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            return provider_common.ExchangeResult(
                response={"access_token": "ya29.x", "expires_in": 3599, "refresh_token": "rotated"},
                revoked=False,
                error=None,
            )

        outcome = self._run(exchange=exchange)

        self.assertEqual(outcome["outcome"], "has_token")
        row = IntegrationUserCredential.objects.get(pk=self.row.pk)
        self.assertEqual(row.credentials["refresh_token"], "rotated")
        self.assertIsNotNone(row.last_refreshed_at)

    def test_undisturbed_revoked_refresh_deletes_row(self) -> None:
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            return provider_common.ExchangeResult(response=None, revoked=True, error=None)

        outcome = self._run(exchange=exchange)

        self.assertEqual(outcome["outcome"], "absent")
        self.assertFalse(IntegrationUserCredential.objects.filter(pk=self.row.pk).exists())

    def test_lost_cas_success_publishes_no_stale_metadata(self) -> None:
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            self._reconnect_concurrently()
            return provider_common.ExchangeResult(
                response={"access_token": "ya29.x", "expires_in": 3599},
                revoked=False,
                error=None,
            )

        outcome = provider_common.run_refresh_exchange(
            provider=IntegrationUserCredential.Provider.GOOGLE,
            logger=logger,
            environment=self.env,
            owner_user=self.user,
            app_slug="hermes",
            exchange=exchange,
            build_secrets=lambda access_token, response: provider_common.RefreshSecrets(
                secrets={"access_token": access_token},
                expires_in=int(response.get("expires_in", 0)),
                row_metadata={},
                row_config={},
            ),
            outcome_metadata=lambda integration: {"stale": "yes"},
            tombstone_on_revoke=False,
        )

        # The row now belongs to a different grant; metadata derived from the
        # stale in-memory view must not be published.
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["metadata"], {})

    def test_revoked_with_tombstone_flag_blanks_and_stamps(self) -> None:
        """Google's invalid_grant path preserves generation history instead of deleting."""
        def exchange(refresh_token: str) -> provider_common.ExchangeResult:
            return provider_common.ExchangeResult(response=None, revoked=True, error=None)

        outcome = provider_common.run_refresh_exchange(
            provider=IntegrationUserCredential.Provider.GOOGLE,
            logger=logger,
            environment=self.env,
            owner_user=self.user,
            app_slug="hermes",
            exchange=exchange,
            build_secrets=lambda access_token, response: provider_common.RefreshSecrets(
                secrets={"access_token": access_token},
                expires_in=int(response.get("expires_in", 0)),
                row_metadata={},
                row_config={},
            ),
            outcome_metadata=None,
            tombstone_on_revoke=True,
        )

        self.assertEqual(outcome["outcome"], "absent")
        row = IntegrationUserCredential.objects.get(pk=self.row.pk)
        self.assertEqual(row.credentials, {})
        self.assertEqual(row.config, {"scope": ""})
        self.assertIn("revoked_at_epoch", row.metadata)
