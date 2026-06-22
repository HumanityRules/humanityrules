"""Tests for org-shared integration credentials: the $app ABAC path, tag seeding,
the broker resolver's precedence, and OpenRouter shared packaging."""

from django.test import TestCase

from humanityrules_app.models import (
    App,
    IntegrationSharedCredential,
    Organization,
    Policy,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.views.integrations import provider_openrouter, shared_credential_resolver


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
        self.ws_eng = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.ws_sales = Workspace.objects.create(organization=self.org, name="Sales", slug="sales")

        self.alice = User.objects.create_user(username="alice", password="pw", current_organization=self.org)
        self.bob = User.objects.create_user(username="bob", password="pw", current_organization=self.org)
        abac_service.materialize_membership(organization=self.org, user=self.alice, role="member")
        abac_service.materialize_membership(organization=self.org, user=self.bob, role="member")

        self.app_eng = App.objects.create(
            organization=self.org, workspace=self.ws_eng, repository=self.repo,
            name="AliceHermes", slug="alice-hermes", app_type="web",
            build_strategy="dockerfile", branch="main", container_port=8000,
            health_check_path="/health",
        )
        self.app_sales = App.objects.create(
            organization=self.org, workspace=self.ws_sales, repository=self.repo,
            name="SalesHermes", slug="sales-hermes", app_type="web",
            build_strategy="dockerfile", branch="main", container_port=8000,
            health_check_path="/health",
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
        self.assertEqual(self._tags(cred), {("shared-scope", "everyone")})

    def test_user_seeds_scope_and_username(self) -> None:
        cred = self._make_cred("user", self.alice, None, "sk-or-1")
        self.assertEqual(self._tags(cred), {("shared-scope", "user"), ("shared-user", "alice")})

    def test_workspace_seeds_scope_and_slug(self) -> None:
        cred = self._make_cred("workspace", None, self.ws_eng, "sk-or-1")
        self.assertEqual(self._tags(cred), {("shared-scope", "workspace"), ("shared-workspace", "engineering")})

    def test_retarget_reseeds_tags(self) -> None:
        cred = self._make_cred("user", self.alice, None, "sk-or-1")
        cred.scope = "everyone"
        cred.target_user = None
        cred.save()
        self.assertEqual(self._tags(cred), {("shared-scope", "everyone")})


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

    def test_packages_has_token(self) -> None:
        cred = self._make_cred("everyone", None, None, "sk-or-xyz")
        outcome = provider_openrouter.refresh_outcome_from_shared(credential=cred)
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"api_key": "sk-or-xyz"})

    def test_absent_when_key_missing(self) -> None:
        cred = IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone", credentials={},
        )
        self.assertEqual(provider_openrouter.refresh_outcome_from_shared(credential=cred)["outcome"], "absent")


class TestAppReferenceValidation(TestCase):

    def test_app_reference_valid_in_resource_condition(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "authenticated", "value": "true"}],
            resource_conditions=[{"key": "shared-workspace", "value": "$app.workspace-name"}],
        )

    def test_app_reference_valid_in_identity_condition(self) -> None:
        abac_service.validate_policy_conditions(
            identity_conditions=[{"key": "team", "value": "$app.workspace-name"}],
            resource_conditions=[],
        )
