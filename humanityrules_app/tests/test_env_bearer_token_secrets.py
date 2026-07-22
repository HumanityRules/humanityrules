"""Tests for the per-env bearer-token and auth-config secrets helpers in infra_customer.secrets_utils."""

import hashlib
import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from django.test import TestCase, override_settings

from humanityrules_app.models import AWSAccount, Environment, EnvironmentBearerToken, Organization
from humanityrules_app.services.infra_customer import secrets_utils
from humanityrules_app.services.infra_customer.appconfig import AppConfig


def _not_found_error() -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": "ResourceNotFoundException", "Message": "nope"}},
        operation_name="DescribeSecret",
    )


class FakeSecretsManager:
    """In-memory stub implementing the subset of Secrets Manager we call."""

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    # describe_secret: used by _secret_exists
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


def _session_with(fake: FakeSecretsManager) -> MagicMock:
    session = MagicMock()
    session.client.return_value = fake
    return session


class EnvBearerTestBase(TestCase):

    def setUp(self) -> None:
        self.org = Organization.objects.create(
            name="Sec Org", slug="sec-org",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://okta.example.com/oauth2/default",
            oidc_client_id="client-abc",
            oidc_client_secret="secret-xyz",
        )
        self.aws_account = AWSAccount.objects.create(
            organization=self.org, name="Account",
        )
        self.env = Environment.objects.create(
            aws_account=self.aws_account, name="staging", slug="staging",
            aws_region="us-east-1",
        )


# -----------------------------------------------------------------------------
# ensure_env_bearer_token_exists
# -----------------------------------------------------------------------------


class TestEnsureEnvBearerToken(EnvBearerTestBase):

    def test_creates_token_and_row_when_neither_exists(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        arn = secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)
        self.assertTrue(arn.endswith("shared-secrets-AAAA"))

        # Secrets Manager side.
        secret = json.loads(fake.store["humr/staging/shared-secrets"]["SecretString"])
        raw_token = secret["HUMR_ENV_BEARER"]
        self.assertEqual(len(raw_token), 64)

        # DB side.
        row = EnvironmentBearerToken.objects.get(environment=self.env)
        self.assertEqual(row.token_hash, hashlib.sha256(raw_token.encode()).hexdigest())

    def test_noop_when_row_already_consistent_with_secret(self) -> None:
        # Row hash already matches the secret's token → nothing changes.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        raw_token = "pre-existing-raw-token-of-reasonable-length-0123456789012345"
        fake.create_secret(
            Name="humr/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({"HUMR_ENV_BEARER": raw_token, "OTHER_KEY": "keep-me"}),
        )
        EnvironmentBearerToken.objects.create(
            environment=self.env, token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        )
        original_secret = fake.store["humr/staging/shared-secrets"]["SecretString"]
        original_hash = EnvironmentBearerToken.objects.get(environment=self.env).token_hash

        arn = secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)
        self.assertIn("shared-secrets", arn)
        self.assertEqual(fake.store["humr/staging/shared-secrets"]["SecretString"], original_secret)
        self.assertEqual(EnvironmentBearerToken.objects.get(environment=self.env).token_hash, original_hash)

    def test_adopts_existing_secret_token_when_row_missing(self) -> None:
        # The shared secret already has a token but this DB has no row (e.g. a
        # freshly recreated env). We must ADOPT the secret's token — hashing it
        # into a new row — not mint a new one, which would clobber the
        # account-shared secret and strand every other DB on a stale hash.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        raw_token = "account-shared-raw-token-of-reasonable-length-13579246801234"
        fake.create_secret(
            Name="humr/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({"HUMR_ENV_BEARER": raw_token, "OTHER_KEY": "keep-me"}),
        )
        original_secret = fake.store["humr/staging/shared-secrets"]["SecretString"]

        secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)

        # Secret untouched; row adopted the secret's token.
        self.assertEqual(fake.store["humr/staging/shared-secrets"]["SecretString"], original_secret)
        row = EnvironmentBearerToken.objects.get(environment=self.env)
        self.assertEqual(row.token_hash, hashlib.sha256(raw_token.encode()).hexdigest())

    def test_resyncs_drifted_row_to_secret_without_rewriting_secret(self) -> None:
        # Row hash disagrees with the secret's token (another DB rotated the
        # shared secret) → heal the row to match; the secret is NOT rewritten.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        raw_token = "live-shared-raw-token-of-reasonable-length-9876543210987654"
        fake.create_secret(
            Name="humr/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({"HUMR_ENV_BEARER": raw_token}),
        )
        EnvironmentBearerToken.objects.create(environment=self.env, token_hash="stale-drifted-hash")
        original_secret = fake.store["humr/staging/shared-secrets"]["SecretString"]

        secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)

        self.assertEqual(fake.store["humr/staging/shared-secrets"]["SecretString"], original_secret)
        self.assertEqual(
            EnvironmentBearerToken.objects.get(environment=self.env).token_hash,
            hashlib.sha256(raw_token.encode()).hexdigest(),
        )

    def test_regenerates_when_row_exists_but_secret_missing(self) -> None:
        # Simulates a corrupted state where the DB row was created but the
        # AWS secret doesn't exist. We regenerate both in lockstep.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        EnvironmentBearerToken.objects.create(environment=self.env, token_hash="stale-hash")

        secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)

        secret = json.loads(fake.store["humr/staging/shared-secrets"]["SecretString"])
        raw = secret["HUMR_ENV_BEARER"]
        new_hash = EnvironmentBearerToken.objects.get(environment=self.env).token_hash
        self.assertEqual(new_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotEqual(new_hash, "stale-hash")

    def test_preserves_other_shared_secret_keys(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="humr/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({"SOMETHING_ELSE": "keep-me"}),
        )

        secrets_utils.ensure_env_bearer_token_exists(session=session, env=self.env)

        secret = json.loads(fake.store["humr/staging/shared-secrets"]["SecretString"])
        self.assertEqual(secret["SOMETHING_ELSE"], "keep-me")
        self.assertIn("HUMR_ENV_BEARER", secret)


# -----------------------------------------------------------------------------
# shared-secrets namespacing (audit fix #2: per-org sandbox bag)
# -----------------------------------------------------------------------------


# Neutralize the Organization post_save signal that auto-creates a "Humanity Rules Sandbox"
# account when sandbox env vars are present — we build the sandbox accounts ourselves here.
@override_settings(HUMR_SANDBOX_AWS_ACCOUNT_ID="", HUMR_SANDBOX_EXTERNAL_ID="")
class TestSharedSecretsNamespace(TestCase):
    """The shared-secrets bag (and its env bearer) is namespaced per org in the HumR sandbox."""

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

    def test_distinct_sandbox_orgs_get_distinct_secrets_and_tokens(self) -> None:
        # Both orgs' sandbox envs live in ONE AWS account → ONE Secrets Manager store; the
        # per-org namespace is what keeps them from colliding on a single bag/token.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        env_a = self._make_sandbox_env(org_slug="org-a")
        env_b = self._make_sandbox_env(org_slug="org-b")

        secrets_utils.ensure_env_bearer_token_exists(session=session, env=env_a)
        secrets_utils.ensure_env_bearer_token_exists(session=session, env=env_b)

        self.assertIn("humr/sandbox/org-a/shared-secrets", fake.store)
        self.assertIn("humr/sandbox/org-b/shared-secrets", fake.store)
        token_a = json.loads(fake.store["humr/sandbox/org-a/shared-secrets"]["SecretString"])["HUMR_ENV_BEARER"]
        token_b = json.loads(fake.store["humr/sandbox/org-b/shared-secrets"]["SecretString"])["HUMR_ENV_BEARER"]
        self.assertNotEqual(token_a, token_b)

        hash_a = EnvironmentBearerToken.objects.get(environment=env_a).token_hash
        hash_b = EnvironmentBearerToken.objects.get(environment=env_b).token_hash
        self.assertNotEqual(hash_a, hash_b)
        self.assertEqual(hash_a, hashlib.sha256(token_a.encode()).hexdigest())
        self.assertEqual(hash_b, hashlib.sha256(token_b.encode()).hexdigest())


# -----------------------------------------------------------------------------
# ensure_env_policy_proxy_secrets_exist (umbrella)
# -----------------------------------------------------------------------------


class TestEnsureEnvPolicyProxySecrets(EnvBearerTestBase):

    def test_returns_arns_on_fresh_env(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        result = secrets_utils.ensure_env_policy_proxy_secrets_exist(session=session, env=self.env)
        self.assertIn("shared_secrets_arn", result)
        self.assertIn("humr/staging/shared-secrets", fake.store)

        # Side effect: the EnvironmentBearerToken row exists too.
        self.assertTrue(EnvironmentBearerToken.objects.filter(environment=self.env).exists())


# -----------------------------------------------------------------------------
# ensure_app_secrets_exist
# -----------------------------------------------------------------------------


def _make_app_config(app_secrets: dict[str, str | None] | None) -> AppConfig:
    from humanityrules_app.services.infra_customer.appconfig import ContainerConfig, ImageSource
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
