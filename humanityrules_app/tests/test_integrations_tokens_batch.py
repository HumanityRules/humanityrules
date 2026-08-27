"""Tests for POST /api/integrations/tokens — the batched env-resident refresh endpoint.

The app and its owner come from the per-app bearer; the body carries only
`providers`. Anything else a client sends (an `owner_username` / `app_slug`
naming some other pair) is ignored.

These tests use TransactionTestCase rather than TestCase because the view
runs per-provider helpers in a ThreadPoolExecutor (so wall-clock at boot is
one slow upstream, not N × upstream). Worker threads open their own DB
connections, which would never see TestCase's uncommitted setUp data —
TransactionTestCase commits + truncates per-test instead.
"""

import json
import logging
from unittest.mock import MagicMock, patch

from django.test import Client, TransactionTestCase, override_settings

from humanityrules_app.models import (
    AWSAccount,
    App,
    Environment,
    IntegrationConfig,
    IntegrationSharedCredential,
    IntegrationUserCredential,
    Organization,
    PlatformSharedCredential,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.services import abac_service
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template


GOOGLE_WEB_CONFIG = {
    "client_id": "cid-123.apps.googleusercontent.com",
    "client_secret": "csecret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["https://humanityrules.io/integrations/user/google/callback/"],
}


@override_settings(
    GITHUB_APP_CLIENT_ID="gh-cid",
    GITHUB_APP_CLIENT_SECRET="gh-secret",
)
class _BatchTokensEndpointTestBase(TransactionTestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Batch Org", slug="batch-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Batch Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )
        self.user = User.objects.create_user(
            username="vmendi", password="pw", current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        IntegrationConfig.objects.create(
            provider=IntegrationConfig.Provider.GOOGLE,
            config=GOOGLE_WEB_CONFIG,
        )
        # The endpoint derives the owner from the app's ResourceTag, so seed app
        # "hermes" owned by "vmendi" — the pair every test's bearer resolves to.
        self.workspace = Workspace.objects.get(organization=self.org, slug="default")
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=make_source_template(),
            environment=self.env, name="Hermes", slug="hermes",
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        ResourceTag.objects.create(
            organization=self.org, resource_type=ResourceTag.ResourceType.APP,
            app=self.app, key="owner", value="vmendi",
        )
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="b" * 64)
        self.client = Client()

    def _post(self, body: dict, token: str | None) -> tuple[int, dict]:
        headers = {}
        if token is not None:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = self.client.post(
            "/api/integrations/tokens",
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        return response.status_code, response.json()


class TestAuth(_BatchTokensEndpointTestBase):

    def test_missing_bearer_returns_401(self) -> None:
        status, _ = self._post(
            body={"providers": ["google"]},
            token=None,
        )
        self.assertEqual(status, 401)

    def test_wrong_bearer_returns_401(self) -> None:
        status, _ = self._post(
            body={"providers": ["google"]},
            token="nope",
        )
        self.assertEqual(status, 401)


class TestValidation(_BatchTokensEndpointTestBase):

    def test_empty_providers_returns_400(self) -> None:
        status, _ = self._post(
            body={"providers": []},
            token=self.raw_token,
        )
        self.assertEqual(status, 400)

    def test_invalid_json_returns_400(self) -> None:
        response = self.client.post(
            "/api/integrations/tokens", data="{not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
        )
        self.assertEqual(response.status_code, 400)


class TestDisconnectedProvidersReturnAbsent(_BatchTokensEndpointTestBase):

    def test_every_unconnected_provider_is_absent_in_a_200_response(self) -> None:
        """No 4xx, no `INFO no integration row` — the entire reason this endpoint exists."""
        with self.assertNoLogs(logger="humanityrules_app.views.integrations.provider_google", level=logging.INFO), \
             self.assertNoLogs(logger="humanityrules_app.views.integrations.provider_github", level=logging.INFO):
            status, body = self._post(
                body={
                    "providers": ["google", "github", "telegram", "nous"],
                },
                token=self.raw_token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(
            body["results"],
            {
                "google": {"outcome": "absent"},
                "github": {"outcome": "absent"},
                "telegram": {"outcome": "absent"},
                "nous": {"outcome": "absent"},
            },
        )

    def test_unknown_provider_slug_is_absent(self) -> None:
        status, body = self._post(
            body={
                "providers": ["google", "unknown-provider"],
            },
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["results"]["unknown-provider"], {"outcome": "absent"})


class TestConnectedProvidersReturnHasToken(_BatchTokensEndpointTestBase):

    def test_google_has_token_carries_access_token_and_expires_in(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "existing-refresh"},
            config={"scope": (
                "https://www.googleapis.com/auth/gmail.modify "
                "https://www.googleapis.com/auth/calendar.readonly openid email"
            )},
            metadata={"google_email": "vmendi@gmail.com"},
        )
        http_response = MagicMock()
        http_response.status_code = 200
        http_response.json.return_value = {
            "access_token": "ya29.fresh", "expires_in": 3599, "token_type": "Bearer",
        }
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=http_response,
        ):
            status, body = self._post(
                body={"providers": ["google"]},
                token=self.raw_token,
            )

        self.assertEqual(status, 200)
        google_result = body["results"]["google"]
        self.assertEqual(google_result["outcome"], "has_token")
        self.assertEqual(google_result["secrets"], {"access_token": "ya29.fresh"})
        self.assertEqual(google_result["expires_in"], 3599)
        self.assertEqual(google_result["config"], {})
        # The granted-capability projection rides the metadata channel to the card.
        grants = google_result["metadata"]["google_grants"]
        self.assertEqual(grants["google_email"], "vmendi@gmail.com")
        self.assertEqual(grants["products"]["gmail"], "write")
        self.assertEqual(grants["products"]["calendar"], "read")
        self.assertEqual(grants["products"]["drive"], "off")
        self.assertIn("https://www.googleapis.com/auth/gmail.modify", grants["raw_scopes"])

    def test_google_refresh_response_scope_supersedes_stored_scope(self) -> None:
        """Google's refresh response reports the token's effective scope — the
        authoritative grant. A widening (e.g. project-wide merge from another
        app) must update the row and the projection the card renders."""
        row = IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "existing-refresh"},
            config={"scope": "https://www.googleapis.com/auth/gmail.readonly"},
        )
        http_response = MagicMock()
        http_response.status_code = 200
        http_response.json.return_value = {
            "access_token": "ya29.fresh", "expires_in": 3599, "token_type": "Bearer",
            "scope": (
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/calendar.events"
            ),
        }
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=http_response,
        ):
            status, body = self._post(
                body={"providers": ["google"]},
                token=self.raw_token,
            )

        self.assertEqual(status, 200)
        grants = body["results"]["google"]["metadata"]["google_grants"]
        self.assertEqual(grants["products"]["gmail"], "read")
        self.assertEqual(grants["products"]["calendar"], "write")
        row.refresh_from_db()
        self.assertIn("calendar.events", row.config["scope"])

    def test_telegram_passes_config_and_metadata_through(self) -> None:
        # The base seeds app "hermes" owned by vmendi, so the ownership guard passes; this
        # asserts config/metadata pass-through for a connected telegram credential.
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
            credentials={"bot_token": "123456:REAL"},
            config={"allowed_users": ["42", "7"]},
            metadata={"bot_id": 123456, "bot_username": "humr_bot"},
        )

        status, body = self._post(
            body={"providers": ["telegram"]},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        tg = body["results"]["telegram"]
        self.assertEqual(tg["outcome"], "has_token")
        self.assertEqual(tg["secrets"], {"bot_token": "123456:REAL"})
        self.assertEqual(tg["config"], {"allowed_users": ["42", "7"]})
        self.assertEqual(tg["metadata"], {"bot_id": 123456, "bot_username": "humr_bot"})

    def test_openrouter_has_token_carries_api_key(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-v1-real"},
            metadata={"label": "Production"},
        )

        status, body = self._post(
            body={"providers": ["openrouter"]},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        openrouter = body["results"]["openrouter"]
        self.assertEqual(openrouter["outcome"], "has_token")
        self.assertEqual(openrouter["secrets"], {"api_key": "sk-or-v1-real"})
        self.assertEqual(openrouter["config"], {})
        self.assertEqual(openrouter["metadata"], {"label": "Production"})

    def test_openai_has_token_carries_api_key(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENAI,
            credentials={"api_key": "sk-real"},
            metadata={"validated_at": "2026-06-05T00:00:00+00:00"},
        )

        status, body = self._post(
            body={"providers": ["openai-api"]},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        openai = body["results"]["openai-api"]
        self.assertEqual(openai["outcome"], "has_token")
        self.assertEqual(openai["secrets"], {"api_key": "sk-real"})
        self.assertEqual(openai["config"], {})

    def test_anthropic_has_token_carries_api_key(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.ANTHROPIC,
            credentials={"api_key": "sk-ant-real"},
            metadata={"validated_at": "2026-06-05T00:00:00+00:00"},
        )

        status, body = self._post(
            body={"providers": ["anthropic"]},
            token=self.raw_token,
        )

        self.assertEqual(status, 200)
        anthropic = body["results"]["anthropic"]
        self.assertEqual(anthropic["outcome"], "has_token")
        self.assertEqual(anthropic["secrets"], {"api_key": "sk-ant-real"})
        self.assertEqual(anthropic["config"], {})

    def test_nous_has_token_carries_access_token_and_rotates_refresh_token(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.NOUS,
            credentials={"refresh_token": "nous-refresh-old"},
        )
        http_response = MagicMock()
        http_response.status_code = 200
        http_response.json.return_value = {
            "access_token": "nous-access",
            "refresh_token": "nous-refresh-new",
            "expires_in": 1800,
            "token_type": "Bearer",
            "scope": "inference:invoke",
        }
        with patch(
            "humanityrules_app.views.integrations.provider_nous.httpx.post",
            return_value=http_response,
        ) as post_mock:
            status, body = self._post(
                body={"providers": ["nous"]},
                token=self.raw_token,
            )

        self.assertEqual(status, 200)
        nous = body["results"]["nous"]
        self.assertEqual(nous["outcome"], "has_token")
        self.assertEqual(nous["secrets"], {"access_token": "nous-access"})
        self.assertEqual(nous["expires_in"], 3600)
        post_mock.assert_called_once()
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.NOUS)
        self.assertEqual(cred.credentials["refresh_token"], "nous-refresh-new")


class TestSharedCredentials(_BatchTokensEndpointTestBase):
    """Org-provisioned shared credentials flow through the endpoint and win over personal keys."""

    def setUp(self) -> None:
        super().setUp()
        # Seed the three credential system policies + the user's username identity attribute.
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.user)
        self.ws_eng = Workspace.objects.create(organization=self.org, name="Engineering", slug="engineering")
        self.ws_sales = Workspace.objects.create(organization=self.org, name="Sales", slug="sales")
        # The base seeds app "hermes" (owned by vmendi) in the default workspace; move it to
        # Engineering so the workspace-scoped share tests resolve against it.
        self.app.workspace = self.ws_eng
        self.app.save()

    def _post_openrouter(self) -> dict:
        status, body = self._post(
            body={"providers": ["openrouter"]},
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        return body["results"]["openrouter"]

    def test_everyone_shared_key_returned(self) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone",
            credentials={"api_key": "sk-or-SHARED"}, metadata={"label": "Org Default"},
        )
        result = self._post_openrouter()
        self.assertEqual(result["outcome"], "has_token")
        self.assertEqual(result["secrets"], {"api_key": "sk-or-SHARED"})
        # The row's own metadata is preserved and the org-shared marker is added,
        # so the broker status card can render the read-only org-provided state.
        self.assertEqual(
            result["metadata"],
            {"label": "Org Default", "org_shared": True, "org_shared_scope": "everyone"},
        )

    def test_workspace_shared_key_marks_org_shared_with_scope(self) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="workspace",
            target_workspace=self.ws_eng, credentials={"api_key": "sk-or-ENG"},
        )
        metadata = self._post_openrouter()["metadata"]
        self.assertTrue(metadata["org_shared"])
        self.assertEqual(metadata["org_shared_scope"], "workspace")

    def test_personal_key_is_not_marked_org_shared(self) -> None:
        """The personal-refresh path must never carry the org-shared marker."""
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-PERSONAL"},
        )
        result = self._post_openrouter()
        self.assertEqual(result["secrets"], {"api_key": "sk-or-PERSONAL"})
        self.assertNotIn("org_shared", result.get("metadata", {}))

    def test_shared_overrides_personal_key(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-PERSONAL"},
        )
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone",
            credentials={"api_key": "sk-or-SHARED"},
        )
        result = self._post_openrouter()
        self.assertEqual(result["secrets"], {"api_key": "sk-or-SHARED"})

    def test_workspace_shared_key_matches_app_workspace(self) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="workspace",
            target_workspace=self.ws_eng, credentials={"api_key": "sk-or-ENG"},
        )
        self.assertEqual(self._post_openrouter()["secrets"], {"api_key": "sk-or-ENG"})

    def test_workspace_shared_key_for_other_workspace_falls_back_to_personal(self) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="workspace",
            target_workspace=self.ws_sales, credentials={"api_key": "sk-or-SALES"},
        )
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-PERSONAL"},
        )
        # The hermes app is in Engineering, not Sales, so the share does not apply.
        self.assertEqual(self._post_openrouter()["secrets"], {"api_key": "sk-or-PERSONAL"})

    def test_shared_key_without_secret_falls_back_to_personal(self) -> None:
        """A blank shared key must not shadow the user's own pasted key."""
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone",
            credentials={"api_key": ""},
        )
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-PERSONAL"},
        )
        result = self._post_openrouter()
        self.assertEqual(result["outcome"], "has_token")
        self.assertEqual(result["secrets"], {"api_key": "sk-or-PERSONAL"})

    def test_shared_key_without_secret_and_no_personal_is_absent(self) -> None:
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="openrouter", scope="everyone", credentials={},
        )
        self.assertEqual(self._post_openrouter(), {"outcome": "absent"})


class TestPlatformSharedCredentials(_BatchTokensEndpointTestBase):
    """Platform credentials are the lowest-priority fallback: used only where no
    org-shared or personal credential applies (org-shared > personal > platform)."""

    def setUp(self) -> None:
        super().setUp()
        # Seed the credential system policies so the org-shared path is fully
        # functional for the override test; harmless to the platform-only cases.
        abac_service.bootstrap_organization(organization=self.org, admin_user=self.user)

    def _post_tavily(self) -> dict:
        status, body = self._post(
            body={"providers": ["tavily"]},
            token=self.raw_token,
        )
        self.assertEqual(status, 200)
        return body["results"]["tavily"]

    def test_platform_key_used_when_nothing_else_applies(self) -> None:
        PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-PLATFORM"})
        result = self._post_tavily()
        self.assertEqual(result["outcome"], "has_token")
        self.assertEqual(result["secrets"], {"api_key": "tvly-PLATFORM"})
        self.assertTrue(result["metadata"]["platform_shared"])

    def test_personal_key_overrides_platform(self) -> None:
        PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-PLATFORM"})
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TAVILY,
            credentials={"api_key": "tvly-PERSONAL"},
        )
        result = self._post_tavily()
        self.assertEqual(result["secrets"], {"api_key": "tvly-PERSONAL"})
        self.assertNotIn("platform_shared", result.get("metadata", {}))

    def test_org_shared_key_overrides_platform(self) -> None:
        PlatformSharedCredential.objects.create(provider="tavily", credentials={"api_key": "tvly-PLATFORM"})
        IntegrationSharedCredential.objects.create(
            organization=self.org, provider="tavily", scope="everyone",
            credentials={"api_key": "tvly-ORG"},
        )
        result = self._post_tavily()
        self.assertEqual(result["secrets"], {"api_key": "tvly-ORG"})
        self.assertTrue(result["metadata"]["org_shared"])
        self.assertNotIn("platform_shared", result["metadata"])

    def test_disabled_platform_key_is_absent(self) -> None:
        PlatformSharedCredential.objects.create(
            provider="tavily", credentials={"api_key": "tvly-PLATFORM"}, enabled=False,
        )
        self.assertEqual(self._post_tavily(), {"outcome": "absent"})

    def test_no_platform_key_is_absent(self) -> None:
        self.assertEqual(self._post_tavily(), {"outcome": "absent"})


class TestMixedConnectedAndAbsent(_BatchTokensEndpointTestBase):

    def test_one_connected_two_absent_in_single_round_trip(self) -> None:
        """The typical Refresh-all shape: user has connected one provider."""
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.GOOGLE,
            credentials={"refresh_token": "existing-refresh"},
            config={"scope": "gmail.readonly"},
        )
        http_response = MagicMock()
        http_response.status_code = 200
        http_response.json.return_value = {
            "access_token": "ya29.fresh", "expires_in": 3599, "token_type": "Bearer",
        }
        with patch(
            "humanityrules_app.views.integrations.provider_google.httpx.post",
            return_value=http_response,
        ):
            status, body = self._post(
                body={
                    "providers": ["google", "github", "telegram"],
                },
                token=self.raw_token,
            )

        self.assertEqual(status, 200)
        self.assertEqual(body["results"]["google"]["outcome"], "has_token")
        self.assertEqual(body["results"]["github"], {"outcome": "absent"})
        self.assertEqual(body["results"]["telegram"], {"outcome": "absent"})


class TestAppOwnership(_BatchTokensEndpointTestBase):
    """The bearer names the app and the app's owner tag names the user; the body cannot
    redirect either. An app with no owner has no user to refresh for and is rejected."""

    def _make_app(self, slug: str, owner_username: str | None) -> App:
        app = App.objects.create(
            organization=self.org, workspace=self.workspace,
            source_template=make_source_template(),
            environment=self.env, name=slug, slug=slug,
            container_port=8000,
            health_check_path="/health", cpu=256, memory=512,
        )
        if owner_username is not None:
            ResourceTag.objects.create(
                organization=self.org, resource_type=ResourceTag.ResourceType.APP,
                app=app, key="owner", value=owner_username,
            )
        return app

    def test_body_naming_another_owner_and_app_is_ignored(self) -> None:
        theirs = self._make_app("theirs", owner_username="someone-else")
        IntegrationUserCredential.objects.create(
            owner_user=self.user, environment=self.env, app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
            credentials={"api_key": "sk-or-HERMES"},
        )

        status, body = self._post(
            body={"owner_username": "someone-else", "app_slug": theirs.slug, "providers": ["openrouter"]},
            token=self.raw_token,
        )

        # The bearer's own (vmendi, hermes) credential is what comes back.
        self.assertEqual(status, 200)
        self.assertEqual(body["results"]["openrouter"]["secrets"], {"api_key": "sk-or-HERMES"})

    def test_app_without_owner_returns_404(self) -> None:
        lonely = self._make_app("lonely", owner_username=None)
        lonely_token = bearer_test_helpers.make_app_bearer(app=lonely, raw="l" * 64)

        status, _ = self._post(body={"providers": ["google"]}, token=lonely_token)

        self.assertEqual(status, 404)

    def test_owner_who_left_the_org_returns_404(self) -> None:
        gone = self._make_app("gone", owner_username="departed")
        gone_token = bearer_test_helpers.make_app_bearer(app=gone, raw="g" * 64)

        status, _ = self._post(body={"providers": ["google"]}, token=gone_token)

        self.assertEqual(status, 404)
