"""Tests for the Secrets Manager helpers in infra_customer.secrets_utils.

Three things live here:

- The per-app bearer mint. It is "DB wins": the control plane only ever
  accepts the hash it stored, so the single no-op case is a row whose hash
  already matches the value in the app's Secrets Manager bag. Everything else
  — no row, no bag, no key, or a mismatch — re-mints and writes both sides
  together.
- The shared-secrets bag naming, namespaced per org in the HumR sandbox.
- `ensure_app_secrets_exist`, the template-declared secrets that share the
  per-app bag with the bearer.

AWS is faked with an in-memory `FakeSecretsManager` injected through the
`session` parameter; no moto, no patching. Other suites (app removal) reuse the
fake to prove the bearer is purged with the rest of the app's prefix.
"""

import hashlib
import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from django.test import TestCase, override_settings

from humanityrules_app.models import App, AppBearerToken, AWSAccount, Environment, Organization, Workspace
from humanityrules_app.services.infra_customer import secrets_utils
from humanityrules_app.services.infra_customer.appconfig import AppConfig, ContainerConfig, ImageSource
from humanityrules_app.services.jobs import app_config_builder
from humanityrules_app.tests.app_test_factories import make_source_template


def _not_found_error() -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
        operation_name="DescribeSecret",
    )


class _FakeListSecretsPaginator:
    """Single-page paginator over the fake store, honouring the name-prefix filter."""

    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    def paginate(self, **kwargs: object) -> list[dict]:
        prefixes: list[str] = []
        for f in kwargs.get("Filters") or []:
            if f.get("Key") == "name":
                prefixes.extend(f.get("Values", []))
        rows = [
            {"Name": name, "ARN": row["ARN"]}
            for name, row in self._store.items()
            if not prefixes or any(name.startswith(p) for p in prefixes)
        ]
        return [{"SecretList": rows}]


class FakeSecretsManager:
    """In-memory stub implementing the subset of Secrets Manager we call."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def describe_secret(self, SecretId: str) -> dict:
        if SecretId not in self.store:
            raise _not_found_error()
        return {"ARN": self.store[SecretId]["ARN"]}

    def get_secret_value(self, SecretId: str) -> dict:
        if SecretId not in self.store:
            raise _not_found_error()
        row = self.store[SecretId]
        return {"ARN": row["ARN"], "SecretString": row["SecretString"]}

    def create_secret(self, Name: str, Description: str, SecretString: str) -> dict:
        arn = f"arn:aws:secretsmanager:us-east-1:0:secret:{Name}-AAAA"
        self.store[Name] = {"ARN": arn, "SecretString": SecretString, "Description": Description}
        return {"ARN": arn}

    def put_secret_value(self, SecretId: str, SecretString: str) -> dict:
        self.store[SecretId]["SecretString"] = SecretString
        return {"ARN": self.store[SecretId]["ARN"]}

    def get_paginator(self, operation_name: str) -> _FakeListSecretsPaginator:
        assert operation_name == "list_secrets"
        return _FakeListSecretsPaginator(store=self.store)

    def delete_secret(self, SecretId: str, **kwargs: object) -> dict:
        # Accepts ForceDeleteWithoutRecovery / RecoveryWindowInDays like boto3; both delete here.
        name = next((n for n, row in self.store.items() if row["ARN"] == SecretId or n == SecretId), None)
        if name is None:
            raise _not_found_error()
        del self.store[name]
        return {"ARN": SecretId}


def _session_with(fake: FakeSecretsManager) -> MagicMock:
    session = MagicMock()
    session.client.return_value = fake
    return session


SECRET_NAME = "humr/staging/wolfie/secrets"


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AppBearerTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(name="Bearer Org", slug="bearer-org")
        self.aws_account = AWSAccount.objects.create(organization=self.org, name="Account")
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging", aws_region="us-east-1",
        )
        self.workspace = Workspace.objects.create(organization=self.org, name="PAs", slug="pas")
        self.app = App.objects.create(
            organization=self.org, workspace=self.workspace, environment=self.env,
            source_template=make_source_template(), name="Wolfie", slug="wolfie",
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )

    def _stored_secret(self, fake: FakeSecretsManager) -> dict[str, str]:
        return json.loads(fake.store[SECRET_NAME]["SecretString"])


# -----------------------------------------------------------------------------
# ensure_app_bearer_token_exists
# -----------------------------------------------------------------------------


class TestEnsureAppBearerToken(AppBearerTestBase):

    def test_creates_the_bag_when_the_app_has_no_secrets_entry(self) -> None:
        # The common case: no template declares secrets, so the bearer is the
        # first key ever written to this app's bag.
        fake = FakeSecretsManager()
        session = _session_with(fake)

        arn = secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        self.assertTrue(arn.endswith("secrets-AAAA"))
        self.assertIn(SECRET_NAME, fake.store)
        raw = self._stored_secret(fake)["HUMR_APP_BEARER"]
        self.assertEqual(len(raw), 64)
        row = AppBearerToken.objects.get(app=self.app)
        self.assertEqual(row.token_hash, _sha256(raw))

    def test_mints_into_an_existing_bag_and_preserves_template_secrets(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name=SECRET_NAME, Description="seed",
            SecretString=json.dumps({"SLACK_TOKEN": "keep-me"}),
        )

        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        stored = self._stored_secret(fake)
        self.assertEqual(stored["SLACK_TOKEN"], "keep-me")
        self.assertEqual(
            AppBearerToken.objects.get(app=self.app).token_hash,
            _sha256(stored["HUMR_APP_BEARER"]),
        )

    def test_noop_when_row_and_secret_already_agree(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        raw = "already-provisioned-raw-token-of-reasonable-length-0123456789"
        fake.create_secret(
            Name=SECRET_NAME, Description="seed",
            SecretString=json.dumps({"HUMR_APP_BEARER": raw, "SLACK_TOKEN": "keep-me"}),
        )
        AppBearerToken.objects.create(app=self.app, token_hash=_sha256(raw))
        original = fake.store[SECRET_NAME]["SecretString"]

        arn = secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        self.assertIn("wolfie", arn)
        self.assertEqual(fake.store[SECRET_NAME]["SecretString"], original)
        self.assertEqual(AppBearerToken.objects.get(app=self.app).token_hash, _sha256(raw))

    def test_remints_both_sides_when_the_hashes_disagree(self) -> None:
        # Someone edited the secret out of band. The DB is what the control
        # plane accepts, so the deploy takes both sides to a fresh value.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name=SECRET_NAME, Description="seed",
            SecretString=json.dumps({"HUMR_APP_BEARER": "out-of-band-value"}),
        )
        AppBearerToken.objects.create(app=self.app, token_hash=_sha256("what-the-db-thinks"))

        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        stored_raw = self._stored_secret(fake)["HUMR_APP_BEARER"]
        self.assertNotEqual(stored_raw, "out-of-band-value")
        self.assertEqual(AppBearerToken.objects.get(app=self.app).token_hash, _sha256(stored_raw))

    def test_remints_when_the_row_exists_but_the_bag_does_not(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        AppBearerToken.objects.create(app=self.app, token_hash=_sha256("stranded"))

        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        stored_raw = self._stored_secret(fake)["HUMR_APP_BEARER"]
        self.assertEqual(AppBearerToken.objects.get(app=self.app).token_hash, _sha256(stored_raw))
        self.assertNotEqual(stored_raw, "stranded")

    def test_two_apps_in_one_environment_get_independent_tokens(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        other = App.objects.create(
            organization=self.org, workspace=self.workspace, environment=self.env,
            source_template=make_source_template(), name="Other", slug="other",
            container_port=8000, health_check_path="/health", cpu=256, memory=512,
        )

        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)
        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=other)

        first = self._stored_secret(fake)["HUMR_APP_BEARER"]
        second = json.loads(fake.store["humr/staging/other/secrets"]["SecretString"])["HUMR_APP_BEARER"]
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            AppBearerToken.objects.get(app=self.app).token_hash,
            AppBearerToken.objects.get(app=other).token_hash,
        )

    def test_deleting_the_app_revokes_the_token(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        secrets_utils.ensure_app_bearer_token_exists(session=session, env_slug="staging", app=self.app)

        self.app.delete()

        self.assertFalse(AppBearerToken.objects.exists())


class TestReservedAppSecretName(TestCase):
    """A template may not declare the key the mint owns — it would lose on every deploy."""

    def test_template_declared_bearer_key_is_rejected(self) -> None:
        container = ContainerConfig(
            name="app",
            image_source=ImageSource.TEMPLATE,
            template_path="hermes_agent",
            container_port=8000,
            app_secrets={"HUMR_APP_BEARER": "mine-now"},
        )
        with self.assertRaises(app_config_builder.ReservedSecretName):
            app_config_builder._union_app_secrets([container])

    def test_ordinary_secret_names_are_still_allowed(self) -> None:
        container = ContainerConfig(
            name="app",
            image_source=ImageSource.TEMPLATE,
            template_path="hermes_agent",
            container_port=8000,
            app_secrets={"SLACK_TOKEN": ""},
        )
        self.assertEqual(app_config_builder._union_app_secrets([container]), {"SLACK_TOKEN": ""})


# -----------------------------------------------------------------------------
# shared-secrets namespacing (operator bag; per-org in the HumR sandbox)
# -----------------------------------------------------------------------------


# Neutralize the Organization post_save signal that auto-creates a "Humanity Rules Sandbox"
# account when sandbox env vars are present — we build the sandbox accounts ourselves here.
@override_settings(HUMR_SANDBOX_AWS_ACCOUNT_ID="", HUMR_SANDBOX_EXTERNAL_ID="")
class TestSharedSecretsNamespace(TestCase):
    """The operator's shared-secrets bag is namespaced per org in the HumR sandbox."""

    def _make_org(self, slug: str) -> Organization:
        return Organization.objects.create(
            name=slug, slug=slug,
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://okta.example.com/oauth2/default",
            oidc_client_id="client-abc",
            oidc_client_secret="secret-xyz",
        )

    def _make_sandbox_env(self, org_slug: str) -> Environment:
        org = self._make_org(slug=org_slug)
        aws_account = AWSAccount.objects.create(
            organization=org, name="Humanity Rules Sandbox", is_humr_sandbox=True,
        )
        return Environment.objects.create(
            aws_account=aws_account, name="Sandbox", slug="sandbox", aws_region="us-east-1",
        )

    def test_sandbox_namespace_is_per_org(self) -> None:
        env_a = self._make_sandbox_env(org_slug="org-a")
        env_b = self._make_sandbox_env(org_slug="org-b")
        self.assertEqual(secrets_utils.env_shared_secrets_namespace(env_a), "sandbox/org-a")
        self.assertEqual(secrets_utils.shared_secrets_secret_name(env_a), "humr/sandbox/org-a/shared-secrets")
        self.assertEqual(secrets_utils.shared_secrets_secret_name(env_b), "humr/sandbox/org-b/shared-secrets")

    def test_non_sandbox_namespace_is_env_slug(self) -> None:
        org = self._make_org(slug="dedicated-org")
        aws_account = AWSAccount.objects.create(organization=org, name="Prod Account")
        env = Environment.objects.create(
            aws_account=aws_account, name="prod", slug="prod", aws_region="us-east-1",
        )
        self.assertFalse(aws_account.is_humr_sandbox)
        self.assertEqual(secrets_utils.env_shared_secrets_namespace(env), "prod")
        self.assertEqual(secrets_utils.shared_secrets_secret_name(env), "humr/prod/shared-secrets")

    def test_get_shared_secrets_reads_the_org_bag(self) -> None:
        # Both orgs' sandbox envs live in ONE AWS account → ONE Secrets Manager store; the
        # per-org namespace is what keeps them from reading each other's operator keys.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        env_a = self._make_sandbox_env(org_slug="org-a")
        env_b = self._make_sandbox_env(org_slug="org-b")
        fake.create_secret(
            Name="humr/sandbox/org-a/shared-secrets", Description="seed",
            SecretString=json.dumps({"OPENAI_API_KEY": "sk-a"}),
        )

        self.assertEqual(secrets_utils.get_shared_secrets(session=session, env=env_a), {"OPENAI_API_KEY": "sk-a"})
        self.assertEqual(secrets_utils.get_shared_secrets(session=session, env=env_b), {})


# -----------------------------------------------------------------------------
# ensure_app_secrets_exist
# -----------------------------------------------------------------------------


def _make_app_config(app_secrets: dict[str, str | None] | None) -> AppConfig:
    return AppConfig(
        app_name="simple-dashboard",
        cpu=256,
        memory=512,
        containers=[
            ContainerConfig(
                name="app",
                image_source=ImageSource.TEMPLATE,
                template_path="simple_dashboard",
                container_port=8000,
                health_check_path="/health",
                app_secrets=dict(app_secrets or {}),
            ),
        ],
        alb_target_container="app",
        app_secrets=app_secrets,
    )


class TestEnsureAppSecretsExist(TestCase):

    def test_creates_secret_under_env_app_prefix_when_missing(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        app_config = _make_app_config({"secret_key_base": None, "slack_token": "literal-token"})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config, shared_secrets={},
        )

        self.assertIn("humr/staging/simple-dashboard/secrets", fake.store)
        payload = json.loads(fake.store["humr/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["slack_token"], "literal-token")
        self.assertEqual(len(payload["secret_key_base"]), 64)

    def test_merges_missing_keys_into_existing_secret(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="humr/staging/simple-dashboard/secrets",
            Description="seed",
            SecretString=json.dumps({"slack_token": "existing-value"}),
        )
        app_config = _make_app_config({"slack_token": "NEW-IGNORED", "secret_key_base": None})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config, shared_secrets={},
        )

        payload = json.loads(fake.store["humr/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["slack_token"], "existing-value")
        self.assertEqual(len(payload["secret_key_base"]), 64)

    def test_resolves_empty_placeholder_from_shared_secrets(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        app_config = _make_app_config({"OPENAI_API_KEY": ""})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config,
            shared_secrets={"OPENAI_API_KEY": "sk-from-shared"},
        )

        payload = json.loads(fake.store["humr/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["OPENAI_API_KEY"], "sk-from-shared")

    def test_heals_empty_existing_value_from_shared_secrets(self) -> None:
        # Simulates first-deploy having created the app secret with empty
        # placeholders because shared-secrets wasn't yet populated; a later
        # shared-set + redeploy must backfill the stored values.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="humr/staging/simple-dashboard/secrets",
            Description="seed",
            SecretString=json.dumps({"AWS_BEDROCK_ACCESS_KEY_ID": "", "HERMES_WEBUI_PASSWORD": "kept"}),
        )
        app_config = _make_app_config({"AWS_BEDROCK_ACCESS_KEY_ID": "", "HERMES_WEBUI_PASSWORD": ""})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config,
            shared_secrets={"AWS_BEDROCK_ACCESS_KEY_ID": "AKIA-from-shared"},
        )

        payload = json.loads(fake.store["humr/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["AWS_BEDROCK_ACCESS_KEY_ID"], "AKIA-from-shared")
        # Non-empty existing value is preserved even when shared has no entry.
        self.assertEqual(payload["HERMES_WEBUI_PASSWORD"], "kept")

    def test_noop_when_app_secrets_is_none(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        app_config = _make_app_config(None)

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config, shared_secrets={},
        )

        self.assertEqual(fake.store, {})
