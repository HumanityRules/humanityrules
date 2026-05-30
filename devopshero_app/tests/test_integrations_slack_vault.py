"""Tests for the Slack vault flow (schema, two-token submit, refresh outcome)."""

import hashlib
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

from devopshero_app.models import (
    AWSAccount,
    App,
    Environment,
    EnvironmentBearerToken,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    Repository,
    ResourceTag,
    User,
    Workspace,
)
from devopshero_app.views.integrations import slack_vault


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _SlackVaultTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Slack Org", slug="slack-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Slack AWS")
        self.env = Environment.objects.create(
            aws_account=self.aws_account,
            name="Staging",
            slug="staging",
            aws_region="us-east-1",
            shared_alb_hosted_zone="dev.example.com",
        )
        self.user = User.objects.create_user(
            username="vmendi",
            email="vmendi@example.com",
            password="pw",
            current_organization=self.org,
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.org, role=OrganizationMembership.Role.MEMBER,
        )
        self.repository = Repository.objects.create(
            organization=self.org,
            provider="github",
            name="hermes",
            full_name="org/hermes",
            default_branch="main",
            clone_url="https://github.com/org/hermes.git",
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="Eng", slug="eng")
        self.app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            repository=self.repository,
            name="Hermes",
            slug="hermes",
            app_type=App.AppType.WEB,
            build_strategy=App.BuildStrategy.DOCKERFILE,
            branch="main",
            container_port=8000,
            health_check_path="/health",
        )
        ResourceTag.objects.create(
            organization=self.org,
            resource_type=ResourceTag.ResourceType.APP,
            app=self.app,
            key="owner",
            value=self.user.username,
        )
        self.raw_env_token = "v" * 64
        EnvironmentBearerToken.objects.create(environment=self.env, token_hash=_hash(self.raw_env_token))
        self.client = Client()

    def _env_headers(self) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_env_token}"}

    def _post_setup_session(self) -> tuple[int, dict]:
        response = self.client.post(
            "/api/integrations/credentials/setup-session",
            data=json.dumps({
                "owner_username": self.user.username,
                "app_slug": self.app.slug,
                "provider": IntegrationUserCredential.Provider.SLACK,
                "public_origin": "https://hermes.dev.example.com",
            }),
            content_type="application/json",
            **self._env_headers(),
        )
        return response.status_code, response.json()

    def _ok_response(self, payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, **payload}
        return resp

    def _patched_slack_api(self):
        """Patch httpx.post so auth.test and apps.connections.open both succeed."""
        def _fake_post(url, **kwargs):
            if url.endswith("/auth.test"):
                return self._ok_response({"team": "Acme", "team_id": "T1", "user_id": "U1"})
            return self._ok_response({"url": "wss://example"})
        return patch("devopshero_app.views.integrations.slack_vault.httpx.post", side_effect=_fake_post)


class TestSlackSetupSession(_SlackVaultTestBase):

    def test_schema_carries_modes_and_manifests(self) -> None:
        status, body = self._post_setup_session()
        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["provider"], "slack")
        self.assertEqual(schema["selected_mode"], slack_vault.MODE_COMPANY_WIDE)
        mode_values = {m["value"]: m["enabled"] for m in schema["modes"]}
        self.assertTrue(mode_values[slack_vault.MODE_COMPANY_WIDE])
        self.assertFalse(mode_values[slack_vault.MODE_PERSONAL])
        company = schema["manifests"][slack_vault.MODE_COMPANY_WIDE]
        self.assertTrue(company["settings"]["socket_mode_enabled"])
        self.assertEqual(company["settings"]["event_subscriptions"]["bot_events"], ["app_mention"])
        personal = schema["manifests"][slack_vault.MODE_PERSONAL]
        self.assertEqual(personal["settings"]["event_subscriptions"]["bot_events"], ["message.im"])


class TestSlackSubmit(_SlackVaultTestBase):

    def test_submit_saves_both_tokens_after_validation(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": slack_vault.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.credentials, {"app_token": "xapp-abc", "bot_token": "xoxb-abc"})
        self.assertEqual(cred.config["workspace_scope"], slack_vault.MODE_COMPANY_WIDE)
        self.assertEqual(cred.metadata["team_id"], "T1")

    def test_submit_rejects_malformed_bot_token(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "not-a-token"},
                    "config": {"workspace_scope": slack_vault.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_rejects_personal_mode_while_disabled(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": slack_vault.MODE_PERSONAL},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())


class TestSlackRefreshOutcome(_SlackVaultTestBase):

    def test_refresh_returns_both_secrets(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.SLACK,
            credentials={"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
            config={"workspace_scope": slack_vault.MODE_COMPANY_WIDE},
            metadata={"team_id": "T1"},
        )
        outcome = slack_vault.refresh_slack_outcome(environment=self.env, owner_user=self.user, app_slug="hermes")
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"app_token": "xapp-abc", "bot_token": "xoxb-abc"})

    def test_refresh_absent_when_not_connected(self) -> None:
        outcome = slack_vault.refresh_slack_outcome(environment=self.env, owner_user=self.user, app_slug="hermes")
        self.assertEqual(outcome, {"outcome": "absent"})
