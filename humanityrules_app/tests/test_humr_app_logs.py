"""Tests for humr_app_logs container option handling."""

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from humanityrules_app.management.commands import humr_app_logs


class HumrAppLogsContainerOptionTests(SimpleTestCase):

    def test_policy_proxy_option_maps_to_policy_proxy_container(self) -> None:
        requested_container = humr_app_logs._requested_container(
            options={"container": None, "policy_proxy": True},
        )

        self.assertEqual(requested_container, "policy-proxy")

    def test_container_option_passes_through(self) -> None:
        requested_container = humr_app_logs._requested_container(
            options={"container": "docker-dind", "policy_proxy": False},
        )

        self.assertEqual(requested_container, "docker-dind")

    def test_policy_proxy_and_container_options_conflict(self) -> None:
        with self.assertRaisesMessage(CommandError, "Use either --container or --policy-proxy, not both."):
            humr_app_logs._requested_container(
                options={"container": "hermes", "policy_proxy": True},
            )
