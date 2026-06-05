"""Tests for broker-assisted, browser-direct IntegrationUserCredential vault flows."""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

import httpx
from django.core import signing
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
from devopshero_app.views.integrations import (
    provider_anthropic,
    provider_openai,
    provider_openrouter,
    provider_telegram,
    user_credential_vault,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _CredentialVaultTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Vault Org", slug="vault-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Vault AWS")
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
        self.workspace = Workspace.objects.create(
            organization=self.org,
            name="Engineering",
            slug="engineering",
        )
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
        EnvironmentBearerToken.objects.create(
            environment=self.env,
            token_hash=_hash(self.raw_env_token),
        )
        self.client = Client()

    def _env_headers(self) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {self.raw_env_token}"}

    def _setup_payload(self) -> dict:
        return self._setup_payload_for_provider(provider=IntegrationUserCredential.Provider.TELEGRAM)

    def _setup_payload_for_provider(self, provider: str) -> dict:
        return {
            "owner_username": self.user.username,
            "app_slug": self.app.slug,
            "provider": provider,
            "public_origin": "https://hermes.dev.example.com",
        }

    def _post_setup_session(self) -> tuple[int, dict]:
        return self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.TELEGRAM)

    def _post_setup_session_for_provider(self, provider: str) -> tuple[int, dict]:
        response = self.client.post(
            "/api/integrations/credentials/setup-session",
            data=json.dumps(self._setup_payload_for_provider(provider=provider)),
            content_type="application/json",
            **self._env_headers(),
        )
        return response.status_code, response.json()

    def _patched_telegram_get_me(self):
        telegram_response = MagicMock()
        telegram_response.status_code = 200
        telegram_response.json.return_value = {
            "ok": True,
            "result": {
                "id": 123456,
                "is_bot": True,
                "first_name": "Hermes",
                "username": "hermes_bot",
            },
        }
        return patch(
            "devopshero_app.views.integrations.provider_telegram.httpx.get",
            return_value=telegram_response,
        )


class TestSetupSession(_CredentialVaultTestBase):

    def test_setup_session_returns_schema_and_signed_context(self) -> None:
        status, body = self._post_setup_session()

        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["provider"], "telegram")
        self.assertEqual(body["schema"]["status"], "not_connected")
        self.assertIn("submit_token", body)

        token_payload = signing.loads(
            body["submit_token"],
            salt=user_credential_vault.SETUP_TOKEN_SALT,
            max_age=user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS,
        )
        self.assertEqual(token_payload["owner_user_id"], str(self.user.id))
        self.assertEqual(token_payload["environment_id"], str(self.env.id))
        self.assertEqual(token_payload["app_slug"], "hermes")
        self.assertEqual(token_payload["allowed_origin"], "https://hermes.dev.example.com")

    def test_openrouter_setup_session_returns_generic_vault_schema(self) -> None:
        status, body = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENROUTER)

        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["provider"], "openrouter")
        self.assertEqual(body["schema"]["status"], "not_connected")
        self.assertEqual(body["schema"]["fields"][0]["name"], "api_key")

    def test_openai_setup_session_returns_generic_vault_schema(self) -> None:
        status, body = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENAI)

        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["provider"], "openai-api")
        self.assertEqual(body["schema"]["status"], "not_connected")
        self.assertEqual(body["schema"]["fields"][0]["name"], "api_key")

    def test_anthropic_setup_session_returns_generic_vault_schema(self) -> None:
        status, body = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.ANTHROPIC)

        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["provider"], "anthropic")
        self.assertEqual(body["schema"]["status"], "not_connected")
        self.assertEqual(body["schema"]["fields"][0]["name"], "api_key")

    def test_setup_session_canonicalizes_origin_for_browser_submit(self) -> None:
        payload = self._setup_payload()
        payload["public_origin"] = "https://user@Hermes.Dev.Example.Com:443/settings"

        response = self.client.post(
            "/api/integrations/credentials/setup-session",
            data=json.dumps(payload),
            content_type="application/json",
            **self._env_headers(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        token_payload = signing.loads(
            body["submit_token"],
            salt=user_credential_vault.SETUP_TOKEN_SALT,
            max_age=user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS,
        )
        self.assertEqual(token_payload["allowed_origin"], "https://hermes.dev.example.com")


class TestCredentialSubmit(_CredentialVaultTestBase):

    def test_submit_saves_telegram_credential_and_sets_cors(self) -> None:
        _status, session = self._post_setup_session()

        with self._patched_telegram_get_me():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
                    "config": {"allowed_users": "111\n222"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://hermes.dev.example.com")
        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
        )
        self.assertEqual(credential.credentials["bot_token"], "123456:abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(credential.config["allowed_users"], ["111", "222"])
        self.assertEqual(credential.metadata["bot_username"], "hermes_bot")

    def test_submit_can_update_config_without_reentering_secret(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
            credentials={"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
            config={"allowed_users": ["111"]},
            metadata={"bot_username": "old_bot"},
        )
        _status, session = self._post_setup_session()

        with self._patched_telegram_get_me():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {},
                    "config": {"allowed_users": "333,444"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 200)
        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
        )
        self.assertEqual(credential.credentials["bot_token"], "123456:abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(credential.config["allowed_users"], ["333", "444"])

    def test_submit_rejects_wrong_origin(self) -> None:
        _status, session = self._post_setup_session()

        response = self.client.post(
            "/api/integrations/credentials/submit",
            data=json.dumps({
                "submit_token": session["submit_token"],
                "credentials": {"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
                "config": {},
            }),
            content_type="text/plain",
            HTTP_ORIGIN="https://attacker.example.com",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_expired_token_returns_cors_readable_expiry_for_signed_origin(self) -> None:
        _status, session = self._post_setup_session()
        expired_time = time.time() + user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS + 1

        with patch("django.core.signing.time.time", return_value=expired_time):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://hermes.dev.example.com")
        self.assertEqual(response.json()["error"], user_credential_vault.EXPIRED_SETUP_TOKEN_MESSAGE)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_expired_token_does_not_expose_cors_to_wrong_origin(self) -> None:
        _status, session = self._post_setup_session()
        expired_time = time.time() + user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS + 1

        with patch("django.core.signing.time.time", return_value=expired_time):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://attacker.example.com",
            )

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertEqual(response.json()["error"], "invalid or expired submit_token")
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_does_not_echo_telegram_token_from_transport_error(self) -> None:
        _status, session = self._post_setup_session()
        bot_token = "123456:abcdefghijklmnopqrstuvwxyz"
        with patch(
            "devopshero_app.views.integrations.provider_telegram.httpx.get",
            side_effect=httpx.ConnectError(
                f"boom https://api.telegram.org/bot{bot_token}/getMe"
            ),
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"bot_token": bot_token},
                    "config": {"allowed_users": "111"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Telegram validation failed. Please try again.")
        self.assertNotIn(bot_token, response.content.decode("utf-8"))
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_rewrites_telegram_unauthorized_error(self) -> None:
        _status, session = self._post_setup_session()
        telegram_response = MagicMock()
        telegram_response.status_code = 401
        telegram_response.json.return_value = {"ok": False, "description": "Unauthorized"}

        with patch(
            "devopshero_app.views.integrations.provider_telegram.httpx.get",
            return_value=telegram_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"bot_token": "123456:abcdefghijklmnopqrstuvwxyz"},
                    "config": {"allowed_users": "111"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            provider_telegram.TELEGRAM_INVALID_TOKEN_MESSAGE,
        )
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_saves_openrouter_credential(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENROUTER)
        openrouter_response = MagicMock()
        openrouter_response.status_code = 200
        openrouter_response.json.return_value = {
            "data": {
                "label": "Production",
                "limit": 100,
                "limit_remaining": 74.5,
                "limit_reset": "monthly",
            },
        }

        with patch(
            "devopshero_app.views.integrations.provider_openrouter.httpx.get",
            return_value=openrouter_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-or-v1-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 200)
        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENROUTER,
        )
        self.assertEqual(credential.credentials["api_key"], "sk-or-v1-real")
        self.assertEqual(credential.config, {})
        self.assertEqual(credential.metadata["label"], "Production")
        self.assertEqual(credential.metadata["limit_remaining"], 74.5)

    def test_submit_rewrites_openrouter_unauthorized_error(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENROUTER)
        openrouter_response = MagicMock()
        openrouter_response.status_code = 401
        openrouter_response.json.return_value = {"error": {"message": "No auth credentials found"}}

        with patch(
            "devopshero_app.views.integrations.provider_openrouter.httpx.get",
            return_value=openrouter_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-or-v1-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], provider_openrouter.OPENROUTER_INVALID_KEY_MESSAGE)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_saves_openai_credential(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENAI)
        openai_response = MagicMock()
        openai_response.status_code = 200
        openai_response.json.return_value = {"data": [{"id": "gpt-4o"}]}

        with patch(
            "devopshero_app.views.integrations.provider_openai.httpx.get",
            return_value=openai_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 200)
        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.OPENAI,
        )
        self.assertEqual(credential.credentials["api_key"], "sk-real")
        self.assertEqual(credential.config, {})
        self.assertIn("validated_at", credential.metadata)

    def test_submit_rewrites_openai_unauthorized_error(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENAI)
        openai_response = MagicMock()
        openai_response.status_code = 401
        openai_response.json.return_value = {"error": {"message": "Incorrect API key"}}

        with patch(
            "devopshero_app.views.integrations.provider_openai.httpx.get",
            return_value=openai_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], provider_openai.OPENAI_INVALID_KEY_MESSAGE)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_saves_anthropic_credential(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.ANTHROPIC)
        anthropic_response = MagicMock()
        anthropic_response.status_code = 200
        anthropic_response.json.return_value = {"data": [{"id": "claude-opus-4-8"}]}

        with patch(
            "devopshero_app.views.integrations.provider_anthropic.httpx.get",
            return_value=anthropic_response,
        ) as anthropic_get:
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-ant-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 200)
        # Anthropic validation uses x-api-key + anthropic-version, not Bearer.
        _args, kwargs = anthropic_get.call_args
        self.assertEqual(kwargs["headers"]["x-api-key"], "sk-ant-real")
        self.assertEqual(kwargs["headers"]["anthropic-version"], provider_anthropic.ANTHROPIC_API_VERSION)
        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.ANTHROPIC,
        )
        self.assertEqual(credential.credentials["api_key"], "sk-ant-real")
        self.assertEqual(credential.config, {})
        self.assertIn("validated_at", credential.metadata)

    def test_submit_rewrites_anthropic_unauthorized_error(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.ANTHROPIC)
        anthropic_response = MagicMock()
        anthropic_response.status_code = 401
        anthropic_response.json.return_value = {"error": {"message": "invalid x-api-key"}}

        with patch(
            "devopshero_app.views.integrations.provider_anthropic.httpx.get",
            return_value=anthropic_response,
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"api_key": "sk-ant-real"},
                    "config": {},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], provider_anthropic.ANTHROPIC_INVALID_KEY_MESSAGE)
        self.assertFalse(IntegrationUserCredential.objects.exists())
