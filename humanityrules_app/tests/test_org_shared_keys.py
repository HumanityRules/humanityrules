"""Tests for the org-admin Provider Keys UI (shared integration credentials CRUD).

Covers org-admin gating, the add/edit/delete flow, live key validation, the
leave-blank-to-keep edit behaviour, scope/target coherence, uniqueness, and
multi-tenant isolation. Provider key validation hits the network, so the OpenAI
`httpx.get` call is mocked throughout.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from humanityrules_app.models import (
    IntegrationSharedCredential,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service

HTMX = {"HTTP_HX_REQUEST": "true"}
LIST_URL = "/integrations/org/provider-keys/"
ADD_URL = "/integrations/org/provider-keys/add/"


def _ok_openai_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": [{"id": "gpt-4o"}]}
    return response


def _unauthorized_openai_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": {"message": "bad key"}}
    return response


def _patch_openai(response: MagicMock):
    return patch("humanityrules_app.views.integrations.provider_openai.httpx.get", return_value=response)


class SharedKeysUITestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Keys Org", slug="keys-org")
        self.admin = User.objects.create_user(username="admin", password="pw", current_organization=self.org)
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)

        self.alice = User.objects.create_user(username="alice", password="pw", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.alice, role="member")

        self.workspace = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")

    def _cred(self, provider: str, scope: str, api_key: str, target_user=None, target_workspace=None) -> IntegrationSharedCredential:
        return IntegrationSharedCredential.objects.create(
            organization=self.org, provider=provider, scope=scope,
            target_user=target_user, target_workspace=target_workspace,
            credentials={"api_key": api_key}, metadata={"validated_at": "2026-01-01T00:00:00+00:00"},
        )


class TestAccessControl(SharedKeysUITestBase):

    def test_member_forbidden(self) -> None:
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(LIST_URL, **HTMX).status_code, 403)
        self.assertEqual(self.client.get(ADD_URL).status_code, 403)

    def test_admin_allowed(self) -> None:
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(LIST_URL, **HTMX).status_code, 200)


class TestList(SharedKeysUITestBase):

    def test_lists_rows_without_exposing_secret(self) -> None:
        self.client.force_login(self.admin)
        self._cred(provider="openrouter", scope="everyone", api_key="sk-or-secret")
        response = self.client.get(LIST_URL, **HTMX)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("OpenRouter", body)
        self.assertIn("Everyone", body)
        self.assertNotIn("sk-or-secret", body)


class TestAdd(SharedKeysUITestBase):

    def test_creates_everyone_credential(self) -> None:
        self.client.force_login(self.admin)
        with _patch_openai(_ok_openai_response()):
            response = self.client.post(ADD_URL, data={"provider": "openai-api", "scope": "everyone", "api_key": "sk-real"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openai-api")
        self.assertEqual(cred.scope, "everyone")
        self.assertEqual(cred.credentials, {"api_key": "sk-real"})
        self.assertIn("validated_at", cred.metadata)
        self.assertEqual(cred.created_by, self.admin)

    def test_creates_workspace_credential(self) -> None:
        self.client.force_login(self.admin)
        with _patch_openai(_ok_openai_response()):
            response = self.client.post(ADD_URL, data={
                "provider": "openai-api", "scope": "workspace",
                "target_workspace": str(self.workspace.id), "api_key": "sk-real",
            })
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openai-api")
        self.assertEqual(cred.scope, "workspace")
        self.assertEqual(cred.target_workspace, self.workspace)

    def test_invalid_key_rerenders_with_error(self) -> None:
        self.client.force_login(self.admin)
        with _patch_openai(_unauthorized_openai_response()):
            response = self.client.post(ADD_URL, data={"provider": "openai-api", "scope": "everyone", "api_key": "sk-bad"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Trigger", response)
        self.assertIn("rejected this API key", response.content.decode())
        self.assertFalse(IntegrationSharedCredential.objects.exists())

    def test_blank_key_rejected(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.post(ADD_URL, data={"provider": "openai-api", "scope": "everyone", "api_key": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Paste an API key", response.content.decode())
        self.assertFalse(IntegrationSharedCredential.objects.exists())

    def test_user_scope_requires_target(self) -> None:
        self.client.force_login(self.admin)
        with _patch_openai(_ok_openai_response()):
            response = self.client.post(ADD_URL, data={"provider": "openai-api", "scope": "user", "api_key": "sk-real"})
        self.assertIn("member of this organization", response.content.decode())
        self.assertFalse(IntegrationSharedCredential.objects.exists())

    def test_duplicate_everyone_rejected(self) -> None:
        self.client.force_login(self.admin)
        self._cred(provider="openai-api", scope="everyone", api_key="sk-1")
        with _patch_openai(_ok_openai_response()):
            response = self.client.post(ADD_URL, data={"provider": "openai-api", "scope": "everyone", "api_key": "sk-2"})
        self.assertIn("already exists", response.content.decode())
        self.assertEqual(IntegrationSharedCredential.objects.filter(provider="openai-api").count(), 1)

    def test_create_seeds_resource_tags(self) -> None:
        self.client.force_login(self.admin)
        with _patch_openai(_ok_openai_response()):
            self.client.post(ADD_URL, data={
                "provider": "openai-api", "scope": "workspace",
                "target_workspace": str(self.workspace.id), "api_key": "sk-real",
            })
        cred = IntegrationSharedCredential.objects.get(organization=self.org, provider="openai-api")
        self.assertTrue(ResourceTag.objects.filter(credential=cred).exists())


class TestEdit(SharedKeysUITestBase):

    def _edit_url(self, cred: IntegrationSharedCredential) -> str:
        return f"/integrations/org/provider-keys/{cred.id}/edit/"

    def test_blank_key_keeps_existing_key(self) -> None:
        self.client.force_login(self.admin)
        cred = self._cred(provider="openai-api", scope="everyone", api_key="sk-old")
        response = self.client.post(self._edit_url(cred), data={
            "scope": "user", "target_user": str(self.alice.id), "api_key": "",
        })
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        cred.refresh_from_db()
        self.assertEqual(cred.scope, "user")
        self.assertEqual(cred.target_user, self.alice)
        self.assertEqual(cred.credentials, {"api_key": "sk-old"})

    def test_new_key_revalidates_and_rotates(self) -> None:
        self.client.force_login(self.admin)
        cred = self._cred(provider="openai-api", scope="everyone", api_key="sk-old")
        with _patch_openai(_ok_openai_response()) as openai_get:
            response = self.client.post(self._edit_url(cred), data={"scope": "everyone", "api_key": "sk-new"})
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        openai_get.assert_called_once()
        cred.refresh_from_db()
        self.assertEqual(cred.credentials, {"api_key": "sk-new"})


class TestDelete(SharedKeysUITestBase):

    def test_deletes_row(self) -> None:
        self.client.force_login(self.admin)
        cred = self._cred(provider="openrouter", scope="everyone", api_key="sk-or-1")
        response = self.client.post(f"/integrations/org/provider-keys/{cred.id}/delete/")
        self.assertEqual(response["HX-Trigger"], "sharedKeysChanged")
        self.assertFalse(IntegrationSharedCredential.objects.filter(id=cred.id).exists())


class TestMultiTenancy(SharedKeysUITestBase):

    def setUp(self) -> None:
        super().setUp()
        self.other_org = Organization.objects.create(name="Other Org", slug="other-org")
        self.other_cred = IntegrationSharedCredential.objects.create(
            organization=self.other_org, provider="openrouter", scope="everyone", credentials={"api_key": "sk-other"},
        )

    def test_cannot_edit_other_orgs_credential(self) -> None:
        self.client.force_login(self.admin)
        response = self.client.get(f"/integrations/org/provider-keys/{self.other_cred.id}/edit/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_delete_other_orgs_credential(self) -> None:
        self.client.force_login(self.admin)
        self.client.post(f"/integrations/org/provider-keys/{self.other_cred.id}/delete/")
        self.assertTrue(IntegrationSharedCredential.objects.filter(id=self.other_cred.id).exists())
