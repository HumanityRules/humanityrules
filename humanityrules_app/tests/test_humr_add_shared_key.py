"""Tests for the humr_add_shared_key management command.

The command provisions an everyone-scoped IntegrationSharedCredential after
live-validating the key through the provider's validate_shared_key. These tests
patch that validation so no network call is made.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from humanityrules_app.models import IntegrationSharedCredential, Organization, ResourceTag

VALIDATED_METADATA = {"validated_at": "2026-06-22T00:00:00+00:00", "label": "Test Key"}


class TestHumrAddSharedKey(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Course Hero", slug="course-hero")

    def _run(self, **kwargs) -> str:
        out = StringIO()
        call_command("humr_add_shared_key", stdout=out, **kwargs)
        return out.getvalue()

    @patch("humanityrules_app.views.integrations.provider_openrouter.validate_shared_key")
    def test_creates_everyone_share_and_seeds_tags(self, mock_validate) -> None:
        mock_validate.return_value = (VALIDATED_METADATA, None)
        output = self._run(org="course-hero", provider="openrouter", api_key="sk-or-NEW")

        mock_validate.assert_called_once_with(api_key="sk-or-NEW")
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openrouter")
        self.assertEqual(cred.scope, IntegrationSharedCredential.Scope.EVERYONE)
        self.assertEqual(cred.credentials, {"api_key": "sk-or-NEW"})
        self.assertEqual(cred.metadata, VALIDATED_METADATA)
        self.assertIn("Created", output)
        # The post_save signal seeds the everyone-scope ABAC tag.
        tags = set(ResourceTag.objects.filter(credential=cred).values_list("key", "value"))
        self.assertEqual(tags, {("sharing-scope", "everyone")})

    @patch("humanityrules_app.views.integrations.provider_openrouter.validate_shared_key")
    def test_replaces_existing_everyone_share(self, mock_validate) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone", credentials={"api_key": ""},
        )
        mock_validate.return_value = (VALIDATED_METADATA, None)
        output = self._run(org="course-hero", provider="openrouter", api_key="sk-or-REAL")

        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openrouter")
        self.assertEqual(cred.credentials, {"api_key": "sk-or-REAL"})
        self.assertIn("Replaced", output)
        self.assertEqual(IntegrationSharedCredential.objects.filter(organization=self.org, provider="openrouter").count(), 1)

    def test_rejects_unshareable_provider(self) -> None:
        with self.assertRaises(CommandError):
            self._run(org="course-hero", provider="google", api_key="x")
        self.assertFalse(IntegrationSharedCredential.objects.filter(organization=self.org).exists())

    @patch("humanityrules_app.views.integrations.provider_openrouter.validate_shared_key")
    def test_rejected_key_creates_no_row(self, mock_validate) -> None:
        mock_validate.return_value = (None, "OpenRouter rejected this API key.")
        with self.assertRaises(CommandError):
            self._run(org="course-hero", provider="openrouter", api_key="sk-or-BAD")
        self.assertFalse(IntegrationSharedCredential.objects.filter(organization=self.org).exists())

    def test_unknown_org_raises(self) -> None:
        with self.assertRaises(CommandError):
            self._run(org="nope", provider="openrouter", api_key="x")
