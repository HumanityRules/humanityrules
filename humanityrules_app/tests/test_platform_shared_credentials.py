"""Tests for platform-shared integration credentials: the global resolver and the
vault providers' duck-typed packaging. The endpoint-level precedence (org-shared >
personal > platform) is covered in ``test_integrations_tokens_batch.py``.
"""

from django.test import TestCase

from humanityrules_app.models import PlatformSharedCredential
from humanityrules_app.views.integrations import platform_credential_resolver, provider_tavily


class TestResolvePlatformCredential(TestCase):

    def test_resolves_enabled_row(self) -> None:
        cred = PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-1"})
        self.assertEqual(platform_credential_resolver.resolve(provider="tavily").pk, cred.pk)

    def test_ignores_disabled_row(self) -> None:
        PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-1"}, enabled=False)
        self.assertIsNone(platform_credential_resolver.resolve(provider="tavily"))

    def test_none_when_no_row(self) -> None:
        self.assertIsNone(platform_credential_resolver.resolve(provider="tavily"))

    def test_scoped_to_provider(self) -> None:
        PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-1"})
        self.assertIsNone(platform_credential_resolver.resolve(provider="openrouter"))


class TestPlatformRefreshPackaging(TestCase):
    """The platform tier reuses each vault provider's ``refresh_outcome_from_shared``
    by duck-typing on ``.credentials``, so a PlatformSharedCredential packages the
    same way an IntegrationSharedCredential does."""

    def test_tavily_packages_has_token(self) -> None:
        cred = PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-xyz"})
        outcome = provider_tavily.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "tvly-xyz"})

    def test_blank_key_packages_absent(self) -> None:
        cred = PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": ""})
        outcome = provider_tavily.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "absent")
