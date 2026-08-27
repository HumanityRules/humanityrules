"""Tests for the Slack vault flow (schema, two-token submit, refresh outcome)."""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

from humanityrules_app.models import (
    AWSAccount,
    App,
    AppTemplate,
    Environment,
    IntegrationUserCredential,
    Organization,
    OrganizationMembership,
    ResourceTag,
    User,
    Workspace,
)
from humanityrules_app.tests import bearer_test_helpers
from humanityrules_app.tests.app_test_factories import make_source_template
from humanityrules_app.views.integrations import provider_slack


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
        self.workspace = Workspace.objects.create(organization=self.org, name="Eng", slug="eng")
        self.app = App.objects.create(
            organization=self.org,
            workspace=self.workspace,
            source_template=make_source_template(),
            name="Hermes",
            slug="hermes",
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
        self.raw_token = bearer_test_helpers.make_app_bearer(app=self.app, raw="v" * 64)
        self.client = Client()

    def _post_setup_session(self) -> tuple[int, dict]:
        response = self.client.post(
            "/api/integrations/credentials/setup-session",
            data=json.dumps({
                "provider": IntegrationUserCredential.Provider.SLACK,
                "public_origin": "https://hermes.dev.example.com",
            }),
            content_type="application/json",
            **bearer_test_helpers.auth_header(raw=self.raw_token),
        )
        return response.status_code, response.json()

    def _ok_response(self, payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, **payload}
        return resp

    def _err_response(self, error: str) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": False, "error": error}
        return resp

    def _patched_slack_api(self, lookup_user=None, lookup_error=None):
        """Patch httpx.post so auth.test / apps.connections.open / lookupByEmail succeed.

        `lookup_user` overrides the users.lookupByEmail user object (personal
        mode); `lookup_error` makes that one call return ok=false with the code.
        """
        user = lookup_user if lookup_user is not None else {"id": "UOWNER", "real_name": "Jane Doe", "name": "jane"}

        def _fake_post(url, **kwargs):
            if url.endswith("/auth.test"):
                return self._ok_response({"team": "Acme", "team_id": "T1", "user_id": "U1"})
            if url.endswith("/users.lookupByEmail"):
                if lookup_error is not None:
                    return self._err_response(lookup_error)
                return self._ok_response({"user": user})
            if url.endswith("/conversations.open"):
                return self._ok_response({"channel": {"id": "DOWNER"}})
            return self._ok_response({"url": "wss://example"})
        return patch("humanityrules_app.views.integrations.provider_slack.httpx.post", side_effect=_fake_post)


class TestSlackSetupSession(_SlackVaultTestBase):

    def test_schema_carries_modes_and_manifests(self) -> None:
        status, body = self._post_setup_session()
        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["provider"], "slack")
        self.assertEqual(schema["selected_mode"], provider_slack.MODE_COMPANY_WIDE)
        mode_values = {m["value"]: m["enabled"] for m in schema["modes"]}
        self.assertTrue(mode_values[provider_slack.MODE_COMPANY_WIDE])
        self.assertTrue(mode_values[provider_slack.MODE_PERSONAL])
        # The owner-email field is prefilled with the deploying user's HUMR email.
        self.assertEqual(schema["owner_email"], self.user.email)
        company = schema["manifests"][provider_slack.MODE_COMPANY_WIDE]
        self.assertTrue(company["settings"]["socket_mode_enabled"])
        # Company-wide subscribes to public/private channel messages (not just
        # app_mention) so the gateway can follow non-mention replies in an
        # active thread, and carries the *:read scopes users.conversations needs
        # for the directory.
        self.assertEqual(
            company["settings"]["event_subscriptions"]["bot_events"],
            ["app_mention", "message.channels", "message.groups"],
        )
        company_scopes = company["oauth_config"]["scopes"]["bot"]
        self.assertIn("channels:history", company_scopes)
        self.assertIn("groups:history", company_scopes)
        self.assertIn("channels:read", company_scopes)
        self.assertIn("groups:read", company_scopes)
        personal = schema["manifests"][provider_slack.MODE_PERSONAL]
        self.assertEqual(personal["settings"]["event_subscriptions"]["bot_events"], ["message.im"])
        # users:read.email is an extension scope; Slack rejects the manifest
        # unless its base scope users:read is also present.
        personal_scopes = personal["oauth_config"]["scopes"]["bot"]
        self.assertIn("users:read", personal_scopes)
        self.assertIn("users:read.email", personal_scopes)
        # im:write lets save_credentials open the owner's DM (conversations.open)
        # to resolve the home channel.
        self.assertIn("im:write", personal_scopes)
        # Personal mode must enable a writable Messages tab or the user has no
        # compose box and can never DM the bot (Slack's default is read-only).
        personal_home = personal["features"]["app_home"]
        self.assertTrue(personal_home["messages_tab_enabled"])
        self.assertFalse(personal_home["messages_tab_read_only_enabled"])
        # Company-wide subscribes to no DMs, so it carries no app_home block.
        self.assertNotIn("app_home", company["features"])

    def test_app_name_defaults_to_template_name_and_feeds_both_manifest_names(self) -> None:
        template = AppTemplate.objects.create(
            name="Hermes Agent",
            slug="hermes-agent",
            description="Template",
            icon="robot",
            category="ai-assistant",
            cpu=1024,
            memory=2048,
            containers=[],
            is_active=True,
        )
        self.app.source_template = template
        self.app.save(update_fields=["source_template"])

        status, body = self._post_setup_session()
        self.assertEqual(status, 200)
        schema = body["schema"]
        self.assertEqual(schema["app_name"], "Hermes Agent")
        company = schema["manifests"][provider_slack.MODE_COMPANY_WIDE]
        # The app name feeds both name fields, sanitized per Slack's rules:
        # display name verbatim, bot display_name lowercased with spaces -> '-'.
        self.assertEqual(company["display_information"]["name"], "Hermes Agent")
        self.assertEqual(company["features"]["bot_user"]["display_name"], "hermes-agent")

    def test_app_name_falls_back_when_template_name_is_blank(self) -> None:
        # A template whose name sanitizes to empty yields the generic fallback.
        template = AppTemplate.objects.create(
            name="",
            slug="blank-name",
            description="Template",
            icon="robot",
            category="ai-assistant",
            cpu=1024,
            memory=2048,
            containers=[],
            is_active=True,
        )
        self.app.source_template = template
        self.app.save(update_fields=["source_template"])

        _status, body = self._post_setup_session()
        schema = body["schema"]
        self.assertEqual(schema["app_name"], provider_slack.SLACK_DEFAULT_APP_NAME)
        company = schema["manifests"][provider_slack.MODE_COMPANY_WIDE]
        self.assertEqual(company["display_information"]["name"], "Slackbot")
        self.assertEqual(company["features"]["bot_user"]["display_name"], "slackbot")


class TestSlackSubmit(_SlackVaultTestBase):

    def test_submit_saves_both_tokens_after_validation(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.credentials, {"app_token": "xapp-abc", "bot_token": "xoxb-abc"})
        self.assertEqual(cred.config["workspace_scope"], provider_slack.MODE_COMPANY_WIDE)
        # Company-wide must opt into allow-all; the gateway denies by default.
        self.assertEqual(cred.config["allow_all_users"], "true")
        self.assertEqual(cred.metadata["team_id"], "T1")
        # No home channel submitted → none stored (gateway prompts / sethome).
        self.assertNotIn("home_channel", cred.config)

    def test_submit_company_wide_stores_optional_home_channel(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE, "home_channel": "C0HOME"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["home_channel"], "C0HOME")

    def test_submit_persists_app_name_and_reopen_carries_it(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE, "app_name": "My Cool Bot"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["app_name"], "My Cool Bot")
        # Reopening the setup session seeds the saved name, not the template default.
        _status, body = self._post_setup_session()
        self.assertEqual(body["schema"]["app_name"], "My Cool Bot")

    def test_submit_rejects_malformed_bot_token(self) -> None:
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "not-a-token"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_transport_failure_is_not_reported_as_rejection(self) -> None:
        _status, session = self._post_setup_session()
        with patch(
            "humanityrules_app.views.integrations.provider_slack.httpx.post",
            side_effect=__import__("httpx").ConnectError("boom"),
        ):
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        error = response.json()["error"]
        # A network blip must not leak the sentinel or claim the token was rejected.
        self.assertNotIn("request_failed", error)
        self.assertNotIn("rejected", error)
        self.assertIn("unreachable", error)

    def _submit_personal(self, owner_email: str, lookup_user=None, lookup_error=None):
        _status, session = self._post_setup_session()
        with self._patched_slack_api(lookup_user=lookup_user, lookup_error=lookup_error):
            return self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
                    "config": {"workspace_scope": provider_slack.MODE_PERSONAL, "owner_email": owner_email},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )

    def test_submit_personal_resolves_owner_and_stores_allowlist(self) -> None:
        response = self._submit_personal(owner_email="jane@example.com")
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["workspace_scope"], provider_slack.MODE_PERSONAL)
        # Personal opens access via an owner-only allowlist, never allow-all.
        self.assertEqual(cred.config["allowed_users"], ["UOWNER"])
        self.assertNotIn("allow_all_users", cred.config)
        # The bot↔owner DM is resolved and stored as the home channel.
        self.assertEqual(cred.config["home_channel"], "DOWNER")
        # The resolved name is shown back; the email is never persisted.
        self.assertEqual(cred.metadata["owner_name"], "Jane Doe")
        self.assertEqual(cred.metadata["owner_user_id"], "UOWNER")
        self.assertNotIn("jane@example.com", json.dumps(cred.config))

    def test_submit_personal_rejects_unknown_email(self) -> None:
        response = self._submit_personal(owner_email="ghost@example.com", lookup_error="users_not_found")
        self.assertEqual(response.status_code, 400)
        self.assertIn("No Slack user found", response.json()["error"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_personal_requires_owner_email(self) -> None:
        response = self._submit_personal(owner_email="")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_submit_personal_missing_scope_explains_recreate(self) -> None:
        # A company-wide bot token reused for personal mode lacks users:read.email.
        response = self._submit_personal(owner_email="jane@example.com", lookup_error="missing_scope")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Personal link", response.json()["error"])
        self.assertFalse(IntegrationUserCredential.objects.exists())

    def test_reconfigure_personal_without_email_keeps_owner(self) -> None:
        self.assertEqual(self._submit_personal(owner_email="jane@example.com").status_code, 200)
        # Reopen: an owner is bound, so the email field is blanked and owner_name shown.
        _status, body = self._post_setup_session()
        self.assertEqual(body["schema"]["owner_email"], "")
        self.assertEqual(body["schema"]["owner_name"], "Jane Doe")
        # Save with a blank email (e.g. just renaming) keeps the bound owner —
        # no re-resolution, no silent rebind to the HUMR email.
        _status, session = self._post_setup_session()
        with patch("humanityrules_app.views.integrations.provider_slack._resolve_owner_user_id") as resolve:
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {},
                    "config": {"workspace_scope": provider_slack.MODE_PERSONAL, "owner_email": "", "app_name": "Renamed"},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        resolve.assert_not_called()
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["allowed_users"], ["UOWNER"])
        self.assertEqual(cred.metadata["owner_name"], "Jane Doe")
        self.assertEqual(cred.config["app_name"], "Renamed")
        # Owner kept → home channel resolved at first connect is preserved.
        self.assertEqual(cred.config["home_channel"], "DOWNER")

    def test_switching_personal_to_company_wide_clears_owner_and_allowlist(self) -> None:
        self.assertEqual(self._submit_personal(owner_email="jane@example.com").status_code, 200)
        _status, session = self._post_setup_session()
        # A mode switch needs a new Slack app, so fresh tokens come with it.
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-new", "bot_token": "xoxb-new"},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 200)
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["allow_all_users"], "true")
        self.assertNotIn("allowed_users", cred.config)
        # Stale owner identity must not linger, or the card keeps showing it.
        self.assertNotIn("owner_user_id", cred.metadata)
        self.assertNotIn("owner_name", cred.metadata)

    def test_switching_mode_with_kept_tokens_is_rejected(self) -> None:
        # The mode lives in the Slack app's manifest, so a switch needs a new
        # app + fresh tokens; flipping it against kept tokens would "connect" a
        # bot subscribed to the wrong events.
        self.assertEqual(self._submit_personal(owner_email="jane@example.com").status_code, 200)
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {},
                    "config": {"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("new Slack app", response.json()["error"])
        # The stored credential keeps its original personal-mode binding.
        cred = IntegrationUserCredential.objects.get(provider=IntegrationUserCredential.Provider.SLACK)
        self.assertEqual(cred.config["workspace_scope"], provider_slack.MODE_PERSONAL)

    def test_personal_token_replace_requires_fresh_owner_email(self) -> None:
        # A new bot token may be a different workspace, where the kept user_id
        # names someone else — re-entering the email is mandatory.
        self.assertEqual(self._submit_personal(owner_email="jane@example.com").status_code, 200)
        _status, session = self._post_setup_session()
        with self._patched_slack_api():
            response = self.client.post(
                "/api/integrations/credentials/submit",
                data=json.dumps({
                    "submit_token": session["submit_token"],
                    "credentials": {"app_token": "xapp-new", "bot_token": "xoxb-new"},
                    "config": {"workspace_scope": provider_slack.MODE_PERSONAL, "owner_email": ""},
                }),
                content_type="text/plain",
                HTTP_ORIGIN="https://hermes.dev.example.com",
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Slack email", response.json()["error"])


class TestSlackRefreshOutcome(_SlackVaultTestBase):

    def test_refresh_returns_both_secrets(self) -> None:
        IntegrationUserCredential.objects.create(
            owner_user=self.user,
            environment=self.env,
            app_slug="hermes",
            provider=IntegrationUserCredential.Provider.SLACK,
            credentials={"app_token": "xapp-abc", "bot_token": "xoxb-abc"},
            config={"workspace_scope": provider_slack.MODE_COMPANY_WIDE},
            metadata={"team_id": "T1"},
        )
        outcome = provider_slack.refresh_outcome(environment=self.env, owner_user=self.user, app_slug="hermes")
        self.assertEqual(outcome["outcome"], "has_token")
        self.assertEqual(outcome["secrets"], {"app_token": "xapp-abc", "bot_token": "xoxb-abc"})

    def test_refresh_absent_when_not_connected(self) -> None:
        outcome = provider_slack.refresh_outcome(environment=self.env, owner_user=self.user, app_slug="hermes")
        self.assertEqual(outcome, {"outcome": "absent"})
