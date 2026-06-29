"""Tests for platform-shared integration credentials: the global resolver and the
vault providers' duck-typed packaging. The endpoint-level precedence (org-shared >
personal > platform) is covered in ``test_integrations_tokens_batch.py``.
"""

from django.contrib import admin
from django.http import HttpRequest
from django.test import RequestFactory, TestCase

from humanityrules_app.admin import PlatformSharedCredentialAdmin
from humanityrules_app.models import Organization, PlatformSharedCredential, User
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


class TestPlatformSharedCredentialAdminPermissions(TestCase):
    """Platform-wide credentials apply to every customer org, so the Django-admin model
    is superuser-only — a staff non-superuser must not view/add/change/delete them."""

    def setUp(self) -> None:
        self.model_admin = PlatformSharedCredentialAdmin(model=PlatformSharedCredential, admin_site=admin.site)
        self.org = Organization.objects.create(name="Admin Perms Org", slug="admin-perms-org")

    def _request(self, is_superuser: bool) -> HttpRequest:
        request = RequestFactory().get("/admin/")
        request.user = User.objects.create_user(
            username=f"staff-{is_superuser}", password="pw", current_organization=self.org,
            is_staff=True, is_superuser=is_superuser,
        )
        return request

    def test_non_superuser_denied(self) -> None:
        request = self._request(is_superuser=False)
        self.assertFalse(self.model_admin.has_view_permission(request))
        self.assertFalse(self.model_admin.has_add_permission(request))
        self.assertFalse(self.model_admin.has_change_permission(request))
        self.assertFalse(self.model_admin.has_delete_permission(request))

    def test_superuser_allowed(self) -> None:
        request = self._request(is_superuser=True)
        self.assertTrue(self.model_admin.has_view_permission(request))
        self.assertTrue(self.model_admin.has_add_permission(request))
        self.assertTrue(self.model_admin.has_change_permission(request))
        self.assertTrue(self.model_admin.has_delete_permission(request))
