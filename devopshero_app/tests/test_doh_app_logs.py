"""Tests for doh_app_logs container option handling."""

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from devopshero_app.management.commands import doh_app_logs


class DohAppLogsContainerOptionTests(SimpleTestCase):

    def test_sidecar_option_maps_to_sidecar_container(self) -> None:
        requested_container = doh_app_logs._requested_container(
            options={"container": None, "sidecar": True},
        )

        self.assertEqual(requested_container, "sidecar")

    def test_container_option_passes_through(self) -> None:
        requested_container = doh_app_logs._requested_container(
            options={"container": "docker-dind", "sidecar": False},
        )

        self.assertEqual(requested_container, "docker-dind")

    def test_sidecar_and_container_options_conflict(self) -> None:
        with self.assertRaisesMessage(CommandError, "Use either --container or --sidecar, not both."):
            doh_app_logs._requested_container(
                options={"container": "hermes", "sidecar": True},
            )
