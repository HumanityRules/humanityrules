"""Tests for org-shared integration credentials: the $app ABAC path, tag seeding,
the broker resolver's precedence, and the vault providers' shared packaging."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from humanityrules_app.models import (
    App,
    AWSAccount,
    Environment,
    IntegrationSharedCredential,
    Organization,
    Policy,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.views.integrations import (
    provider_anthropic,
    provider_openai,
    provider_openrouter,
    provider_tavily,
    shared_credential_resolver,
)


class SharedCredentialTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Shared Org", slug="shared-org")
        self.admin = User.objects.create_user(
            username="admin", password="pw", current_organization=self.org,
        )
        # Seeds the three credential system policies (everyone/user/workspace).
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.admin)

        self.repo = Repository.objects.create(
            organization=self.org, provider="github", name="hermes",
            full_name="org/hermes", clone_url="https://github.com/org/hermes.git",
        )
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Shared Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging", aws_region="us-east-1",
        )
        self.ws_eng = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.ws_sales = Workspace.objects.create(organization=self.org, name="Sales", slug="sales")

        self.alice = User.objects.create_user(username="alice", password="pw", current_organization=self.org)
        self.bob = User.objects.create_user(username="bob", password="pw", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.alice, role="member")
        abac_service.materialize_membership(organization=self.org, user=self.bob, role="member")

        self.app_eng = App.objects.create(
            organization=self.org, workspace=self.ws_eng, repository=self.repo,
            environment=self.env, name="AliceHermes", slug="alice-hermes",
            build_strategy="dockerfile", container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        self.app_sales = App.objects.create(
            organization=self.org, workspace=self.ws_sales, repository=self.repo,
            environment=self.env, name="SalesHermes", slug="sales-hermes",
            build_strategy="dockerfile", container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )

    def _make_cred(self, scope: str, target_user: User | None, target_workspace: Workspace | None, api_key: str) -> IntegrationSharedCredential:
        return IntegrationSharedCredential.objects.create(
            organization=self.org,
            provider="openrouter",
            scope=scope,
            target_user=target_user,
            target_workspace=target_workspace,
            credentials={"api_key": api_key},
        )

    def _all_creds(self):
        return IntegrationSharedCredential.objects.filter(organization=self.org)


class TestSharedCredentialTagSeeding(SharedCredentialTestBase):

    def _tags(self, credential: IntegrationSharedCredential) -> set:
        return set(ResourceTag.objects.filter(credential=credential).values_list("key", "value"))

    def test_everyone_seeds_only_scope_tag(self) -> None:
        cred = self._make_cred("everyone", None, None, "sk-or-1")
        self.assertEqual(self._tags(cred), {("sharing-scope", "everyone")})

    def test_user_seeds_scope_and_username(self) -> None:
        cred = self._make_cred("user", self.alice, None, "sk-or-1")
        self.assertEqual(self._tags(cred), {("sharing-scope", "user"), ("shared-with-user", "alice")})

    def test_workspace_seeds_scope_and_slug(self) -> None:
        cred = self._make_cred("workspace", None, self.ws_eng, "sk-or-1")
        self.assertEqual(self._tags(cred), {("sharing-scope", "workspace"), ("shared-with-workspace", "engineering")})

    def test_retarget_reseeds_tags(self) -> None:
        cred = self._make_cred("user", self.alice, None, "sk-or-1")
        cred.scope = "everyone"
        cred.target_user = None
        cred.save()
        self.assertEqual(self._tags(cred), {("sharing-scope", "everyone")})


class TestFilterPermittedCredentials(SharedCredentialTestBase):

    def _permitted_pks(self, user: User, app: App | None) -> set:
        permitted = abac_service.filter_permitted_credentials(
            organization=self.org, user=user, app=app, queryset=self._all_creds(),
        )
        return {c.pk for c in permitted}

    def test_everyone_permits_any_user(self) -> None:
        cred = self._make_cred("everyone", None, None, "sk-or-1")
        self.assertEqual(self._permitted_pks(self.alice, self.app_eng), {cred.pk})
        self.assertEqual(self._permitted_pks(self.bob, self.app_sales), {cred.pk})

    def test_user_scope_permits_only_target(self) -> None:
        cred = self._make_cred("user", self.alice, None, "sk-or-1")
        self.assertEqual(self._permitted_pks(self.alice, self.app_eng), {cred.pk})
        self.assertEqual(self._permitted_pks(self.bob, self.app_eng), set())

    def test_workspace_scope_matches_app_workspace(self) -> None:
        cred = self._make_cred("workspace", None, self.ws_eng, "sk-or-1")
        self.assertEqual(self._permitted_pks(self.alice, self.app_eng), {cred.pk})
        self.assertEqual(self._permitted_pks(self.alice, self.app_sales), set())

    def test_workspace_scope_fails_closed_without_app(self) -> None:
        self._make_cred("workspace", None, self.ws_eng, "sk-or-1")
        self.assertEqual(self._permitted_pks(self.alice, None), set())


class TestResolveSharedCredential(SharedCredentialTestBase):

    def _resolve(self, user: User, app: App | None) -> IntegrationSharedCredential | None:
        return shared_credential_resolver.resolve(
            organization=self.org, user=user, app=app, provider="openrouter",
        )

    def test_resolves_everyone(self) -> None:
        cred = self._make_cred("everyone", None, None, "sk-or-1")
        self.assertEqual(self._resolve(self.alice, self.app_eng).pk, cred.pk)

    def test_user_beats_everyone(self) -> None:
        self._make_cred("everyone", None, None, "sk-or-1")
        user_cred = self._make_cred("user", self.alice, None, "sk-or-2")
        self.assertEqual(self._resolve(self.alice, self.app_eng).pk, user_cred.pk)

    def test_workspace_beats_everyone(self) -> None:
        self._make_cred("everyone", None, None, "sk-or-1")
        ws_cred = self._make_cred("workspace", None, self.ws_eng, "sk-or-2")
        self.assertEqual(self._resolve(self.alice, self.app_eng).pk, ws_cred.pk)

    def test_user_beats_workspace(self) -> None:
        self._make_cred("workspace", None, self.ws_eng, "sk-or-1")
        user_cred = self._make_cred("user", self.alice, None, "sk-or-2")
        self.assertEqual(self._resolve(self.alice, self.app_eng).pk, user_cred.pk)

    def test_resolves_none_when_nothing_applies(self) -> None:
        self._make_cred("user", self.bob, None, "sk-or-1")
        self.assertIsNone(self._resolve(self.alice, self.app_eng))


class TestRefreshOutcomeFromShared(SharedCredentialTestBase):

    def _cred(self, provider: str, api_key: str) -> IntegrationSharedCredential:
        return IntegrationSharedCredential.objects.create(
            organization=self.org, provider=provider, scope="everyone", credentials={"api_key": api_key},
        )

    def test_packages_has_token(self) -> None:
        cred = self._make_cred("everyone", None, None, "sk-or-xyz")
        outcome = provider_openrouter.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "sk-or-xyz"})

    def test_openai_packages_has_token(self) -> None:
        cred = self._cred(provider="openai-api", api_key="sk-openai")
        outcome = provider_openai.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "sk-openai"})
        self.assertEqual(outcome["expires_in"], provider_openai.OPENAI_BROKER_CACHE_SECONDS)

    def test_anthropic_packages_has_token(self) -> None:
        cred = self._cred(provider="anthropic", api_key="sk-ant-xyz")
        outcome = provider_anthropic.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "sk-ant-xyz"})
        self.assertEqual(outcome["expires_in"], provider_anthropic.ANTHROPIC_BROKER_CACHE_SECONDS)

    def test_tavily_packages_has_token(self) -> None:
        cred = self._cred(provider="tavily", api_key="tvly-xyz")
        outcome = provider_tavily.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "tvly-xyz"})
        self.assertEqual(outcome["expires_in"], provider_tavily.TAVILY_BROKER_CACHE_SECONDS)

    def test_absent_when_key_missing(self) -> None:
        cred = IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone", credentials={},
        )
        self.assertEqual(provider_openrouter.refresh_outcome_from_shared(credential=cred)["outcome"], "absent")
        for module in (provider_openai, provider_anthropic, provider_tavily):
            self.assertEqual(module.refresh_outcome_from_shared(credential=cred)["outcome"], "absent")


class TestValidateSharedKey(TestCase):
    """The admin-side live key check each vault provider exposes for org sharing."""

    def _ok_response(self) -> MagicMock:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"data": [{"id": "model"}]}
        return response

    def _unauthorized_response(self) -> MagicMock:
        response = MagicMock()
        response.status_code = 401
        response.json.return_value = {"error": {"message": "bad key"}}
        return response

    def test_openai_valid_key_returns_metadata(self) -> None:
        with patch("humanityrules_app.views.integrations.provider_openai.httpx.get", return_value=self._ok_response()):
            metadata, error = provider_openai.validate_shared_key(api_key="sk-real")
        self.assertIsNone(error)
        self.assertIn("validated_at", metadata)

    def test_anthropic_valid_key_returns_metadata(self) -> None:
        with patch("humanityrules_app.views.integrations.provider_anthropic.httpx.get", return_value=self._ok_response()):
            metadata, error = provider_anthropic.validate_shared_key(api_key="sk-ant-real")
        self.assertIsNone(error)
        self.assertIn("validated_at", metadata)

    def test_tavily_valid_key_returns_metadata(self) -> None:
        with patch("humanityrules_app.views.integrations.provider_tavily.httpx.get", return_value=self._ok_response()):
            metadata, error = provider_tavily.validate_shared_key(api_key="tvly-real")
        self.assertIsNone(error)
        self.assertIn("validated_at", metadata)

    def test_blank_key_rejected_without_network(self) -> None:
        for module in (provider_openai, provider_anthropic, provider_openrouter, provider_tavily):
            metadata, error = module.validate_shared_key(api_key="   ")
            self.assertIsNone(metadata)
            self.assertEqual(error, "api_key is required")

    def test_openai_unauthorized_key_rejected(self) -> None:
        with patch("humanityrules_app.views.integrations.provider_openai.httpx.get", return_value=self._unauthorized_response()):
            metadata, error = provider_openai.validate_shared_key(api_key="sk-bad")
        self.assertIsNone(metadata)
        self.assertEqual(error, provider_openai.OPENAI_INVALID_KEY_MESSAGE)

    def test_tavily_unauthorized_key_rejected(self) -> None:
        with patch("humanityrules_app.views.integrations.provider_tavily.httpx.get", return_value=self._unauthorized_response()):
            metadata, error = provider_tavily.validate_shared_key(api_key="tvly-bad")
        self.assertIsNone(metadata)
        self.assertEqual(error, provider_tavily.TAVILY_INVALID_KEY_MESSAGE)


class TestAppReferenceValidation(TestCase):

    def test_app_reference_valid_in_resource_condition(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "authenticated", "value": "true"}],
            resource_conditions=[{"key": "shared-with-workspace", "value": "$app.workspace-name"}],
        )

    def test_app_reference_valid_in_identity_condition(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "team", "value": "$app.workspace-name"}],
            resource_conditions=[],
        )
