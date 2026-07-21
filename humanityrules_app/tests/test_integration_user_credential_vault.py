"""Tests for broker-assisted, browser-direct IntegrationUserCredential vault flows."""

import hashlib
import json
import time
from unittest.mock import MagicMock, patch

from django.core import signing
from django.test import Client, TestCase, override_settings

from humanityrules_app.models import (
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
from humanityrules_app.views.integrations import (
    provider_anthropic,
    provider_openai,
    provider_openrouter,
    provider_telegram,
    user_credential_vault,
)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


TELEGRAM_MANAGER_SETTINGS = {
    "TELEGRAM_MANAGER_BOT_TOKEN": "999999:MANAGER_SECRET",
    "TELEGRAM_MANAGER_BOT_USERNAME": "HumrManagerBot",
}


def _telegram_api_response(result: object) -> MagicMock:
    """Build a successful Bot API response carrying *result*."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": result}
    return response


def _telegram_managed_bot_update(bot_username: str, bot_id: int, creator_id: int) -> dict:
    """Build one `managed_bot` update as returned by getUpdates."""
    return {
        "update_id": 100,
        "managed_bot": {
            "user": {"id": creator_id, "is_bot": False, "first_name": "Victor", "username": "vmendi_tg"},
            "bot": {"id": bot_id, "is_bot": True, "first_name": "Hermes", "username": bot_username},
        },
    }


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
            environment=self.env,
            container_port=8000,
            health_check_path="/health",
            cpu=256,
            memory=512,
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

    def _setup_token_payload(self, session: dict) -> dict:
        return signing.loads(
            session["submit_token"],
            salt=user_credential_vault.SETUP_TOKEN_SALT,
            max_age=user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS,
        )

    def _post_poll(self, session: dict, origin: str) -> object:
        return self.client.post(
            "/api/integrations/credentials/poll",
            data=json.dumps({"submit_token": session["submit_token"]}),
            content_type="text/plain",
            HTTP_ORIGIN=origin,
        )


class TestSetupSession(_CredentialVaultTestBase):

    @override_settings(**TELEGRAM_MANAGER_SETTINGS)
    def test_setup_session_returns_link_poll_schema_and_signed_context(self) -> None:
        with patch(
            "humanityrules_app.views.integrations.provider_telegram._link_qr_data_uri",
            wraps=provider_telegram._link_qr_data_uri,
        ) as qr_builder:
            status, body = self._post_setup_session()

        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["provider"], "telegram")
        self.assertEqual(schema["status"], "not_connected")
        self.assertEqual(schema["mode"], "link_poll")
        # The per-session state travels only inside the signed token, never as
        # a browser-editable schema field.
        self.assertNotIn("signed_state", schema)
        self.assertIn("submit_token", body)
        self.assertTrue(body["poll_url"].endswith("/api/integrations/credentials/poll"))

        token_payload = signing.loads(
            body["submit_token"],
            salt=user_credential_vault.SETUP_TOKEN_SALT,
            max_age=user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS,
        )
        self.assertEqual(token_payload["owner_user_id"], str(self.user.id))
        self.assertEqual(token_payload["environment_id"], str(self.env.id))
        self.assertEqual(token_payload["app_slug"], "hermes")
        self.assertEqual(token_payload["allowed_origin"], "https://hermes.dev.example.com")

        bot_username = token_payload["provider_state"]["bot_username"]
        self.assertTrue(bot_username.startswith("hermes_"))
        self.assertTrue(bot_username.endswith("_bot"))
        # The creation deep link is delivered only as a QR (pre-rendered on
        # HUMR so the WebUI extension needs no QR library); verify what it
        # encodes via the builder call.
        self.assertTrue(schema["qr_data_uri"].startswith("data:image/svg+xml"))
        qr_builder.assert_called_once_with(
            link_url=f"https://t.me/newbot/HumrManagerBot/{bot_username}?name=Hermes",
        )

    def test_setup_session_without_manager_bot_reports_unconfigured(self) -> None:
        with override_settings(TELEGRAM_MANAGER_BOT_TOKEN=None):
            status, body = self._post_setup_session()

        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["mode"], "link_poll")
        self.assertNotIn("qr_data_uri", schema)
        self.assertEqual(schema["message"], provider_telegram.TELEGRAM_NOT_CONFIGURED_MESSAGE)

    def test_setup_session_for_connected_telegram_returns_config_form(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
            credentials={"bot_token": "4242:SECRET"},
            config={"allowed_users": ["777"]},
            metadata={"bot_id": 4242, "bot_username": "hermes_ab12cd_bot"},
        )

        status, body = self._post_setup_session()

        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["status"], "connected")
        self.assertEqual(schema["mode"], "form")
        self.assertEqual(schema["fields"][0]["name"], "allowed_users")
        self.assertEqual(schema["fields"][0]["value"], "777")

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

    def test_browseruse_setup_session_returns_generic_vault_schema(self) -> None:
        status, body = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.BROWSERUSE)

        self.assertEqual(status, 200)
        self.assertEqual(body["schema"]["provider"], "browseruse")
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


@override_settings(**TELEGRAM_MANAGER_SETTINGS)
class TestCredentialPoll(_CredentialVaultTestBase):

    _HTTPX_POST = "humanityrules_app.views.integrations.provider_telegram.httpx.post"

    def test_poll_pending_while_bot_not_created_yet(self) -> None:
        _status, session = self._post_setup_session()

        with patch(self._HTTPX_POST, return_value=_telegram_api_response(result=[])):
            response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://hermes.dev.example.com")
        self.assertEqual(response.json()["status"], "pending")
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_poll_ignores_other_bots_managed_updates(self) -> None:
        _status, session = self._post_setup_session()
        updates = [_telegram_managed_bot_update(bot_username="somebody_else_bot", bot_id=1, creator_id=2)]

        with patch(self._HTTPX_POST, return_value=_telegram_api_response(result=updates)):
            response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_poll_connects_and_stores_managed_bot_token(self) -> None:
        _status, session = self._post_setup_session()
        bot_username = self._setup_token_payload(session=session)["provider_state"]["bot_username"]
        updates = [_telegram_managed_bot_update(bot_username=bot_username, bot_id=4242, creator_id=777)]

        with patch(
            self._HTTPX_POST,
            side_effect=[
                _telegram_api_response(result=updates),
                _telegram_api_response(result="4242:NEW_BOT_SECRET"),
            ],
        ) as telegram_post:
            response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://hermes.dev.example.com")
        body = response.json()
        self.assertEqual(body["status"], "connected")
        self.assertTrue(body["restart_required"])

        # getManagedBotToken must be called as the manager bot for the new bot's id.
        token_call_args, token_call_kwargs = telegram_post.call_args_list[1]
        self.assertIn("999999:MANAGER_SECRET/getManagedBotToken", token_call_args[0])
        self.assertEqual(token_call_kwargs["json"], {"user_id": 4242})

        credential = IntegrationUserCredential.objects.get(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
        )
        self.assertEqual(credential.credentials["bot_token"], "4242:NEW_BOT_SECRET")
        self.assertEqual(credential.config["allowed_users"], ["777"])
        self.assertEqual(credential.metadata["bot_id"], 4242)
        self.assertEqual(credential.metadata["bot_username"], bot_username)
        self.assertEqual(credential.metadata["creator_telegram_id"], 777)

    def test_poll_reports_error_when_token_fetch_fails(self) -> None:
        _status, session = self._post_setup_session()
        bot_username = self._setup_token_payload(session=session)["provider_state"]["bot_username"]
        updates = [_telegram_managed_bot_update(bot_username=bot_username, bot_id=4242, creator_id=777)]
        failed_token_response = MagicMock()
        failed_token_response.status_code = 400
        failed_token_response.json.return_value = {"ok": False, "description": "Bad Request"}

        with patch(
            self._HTTPX_POST,
            side_effect=[_telegram_api_response(result=updates), failed_token_response],
        ):
            response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], provider_telegram.TELEGRAM_TOKEN_FETCH_FAILED_MESSAGE)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_poll_rejects_wrong_origin(self) -> None:
        _status, session = self._post_setup_session()

        response = self._post_poll(session=session, origin="https://attacker.example.com")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_poll_expired_token_returns_cors_readable_expiry(self) -> None:
        _status, session = self._post_setup_session()
        expired_time = time.time() + user_credential_vault.SETUP_TOKEN_MAX_AGE_SECONDS + 1

        with patch("django.core.signing.time.time", return_value=expired_time):
            response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://hermes.dev.example.com")
        self.assertEqual(response.json()["error"], user_credential_vault.EXPIRED_SETUP_TOKEN_MESSAGE)

    def test_poll_rejected_for_form_only_provider(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.OPENROUTER)

        response = self._post_poll(session=session, origin="https://hermes.dev.example.com")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "this provider does not support setup polling")


class TestTelegramRefreshOutcome(_CredentialVaultTestBase):

    def _create_managed_credential(self) -> IntegrationUserCredential:
        return IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
            credentials={"bot_token": "4242:SECRET"},
            config={"allowed_users": ["777"]},
            metadata={"bot_id": 4242, "bot_username": "hermes_ab12cd_bot"},
        )

    @override_settings(**TELEGRAM_MANAGER_SETTINGS)
    def test_refresh_is_a_plain_db_read_never_calling_telegram(self) -> None:
        """The broker's batched refresh path must stay off Telegram's API."""
        self._create_managed_credential()

        with patch("humanityrules_app.views.integrations.provider_telegram.httpx.post") as telegram_post:
            outcome = provider_telegram.refresh_outcome(
                environment=self.env, owner_user=self.user, app_slug="hermes",
            )

        telegram_post.assert_not_called()
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"bot_token": "4242:SECRET"})
        self.assertEqual(outcome["config"], {"allowed_users": ["777"]})

    def test_refresh_without_credential_row_is_absent(self) -> None:
        outcome = provider_telegram.refresh_outcome(
            environment=self.env, owner_user=self.user, app_slug="hermes",
        )

        self.assertEqual(outcome["outcome"], "absent")


class TestCredentialSubmit(_CredentialVaultTestBase):

    def test_submit_updates_allowed_users_for_connected_telegram(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.TELEGRAM,
            credentials={"bot_token": "4242:SECRET"},
            config={"allowed_users": ["111"]},
            metadata={"bot_id": 4242, "bot_username": "hermes_ab12cd_bot"},
        )
        _status, session = self._post_setup_session()

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
        # The managed token is never touched by the config form.
        self.assertEqual(credential.credentials["bot_token"], "4242:SECRET")
        self.assertEqual(credential.config["allowed_users"], ["333", "444"])

    def test_submit_rejects_telegram_config_when_not_connected(self) -> None:
        _status, session = self._post_setup_session()

        response = self.client.post(
            "/api/integrations/credentials/submit",
            data=json.dumps({
                "submit_token": session["submit_token"],
                "credentials": {},
                "config": {"allowed_users": "333"},
            }),
            content_type="text/plain",
            HTTP_ORIGIN="https://hermes.dev.example.com",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not connected", response.json()["error"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

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
            "humanityrules_app.views.integrations.provider_openrouter.httpx.get",
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
            "humanityrules_app.views.integrations.provider_openrouter.httpx.get",
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
            "humanityrules_app.views.integrations.provider_openai.httpx.get",
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
            "humanityrules_app.views.integrations.provider_openai.httpx.get",
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
            "humanityrules_app.views.integrations.provider_anthropic.httpx.get",
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
            "humanityrules_app.views.integrations.provider_anthropic.httpx.get",
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

    def test_submit_saves_browseruse_credential_without_validation(self) -> None:
        _status, session = self._post_setup_session_for_provider(provider=IntegrationUserCredential.Provider.BROWSERUSE)

        response = self.client.post(
            "/api/integrations/credentials/submit",
            data=json.dumps({
                "submit_token": session["submit_token"],
                "credentials": {"api_key": "bu-real"},
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
            provider=IntegrationUserCredential.Provider.BROWSERUSE,
        )
        self.assertEqual(credential.credentials["api_key"], "bu-real")
        self.assertEqual(credential.config, {})
        # Store-only: no upstream validation, so no validated_at stamp.
        self.assertNotIn("validated_at", credential.metadata)
