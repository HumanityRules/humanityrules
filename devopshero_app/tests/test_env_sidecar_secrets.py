"""Tests for the per-env sidecar secrets helpers in infra_customer.secrets_utils."""

import hashlib
import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
from django.test import TestCase

from devopshero_app.models import AWSAccount, Environment, Organization, SidecarToken
from devopshero_app.services.infra_customer import secrets_utils
from devopshero_app.services.infra_customer.appconfig import AppConfig


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


class SidecarSecretsTestBase(TestCase):

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
# ensure_env_sidecar_token_exists
# -----------------------------------------------------------------------------


class TestEnsureEnvSidecarToken(SidecarSecretsTestBase):

    def test_creates_token_and_row_when_neither_exists(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        arn = secrets_utils.ensure_env_sidecar_token_exists(session=session, env=self.env)
        self.assertTrue(arn.endswith("shared-secrets-AAAA"))

        # Secrets Manager side.
        secret = json.loads(fake.store["devopshero/staging/shared-secrets"]["SecretString"])
        raw_token = secret["DOH_SIDECAR_TOKEN"]
        self.assertEqual(len(raw_token), 64)

        # DB side.
        row = SidecarToken.objects.get(environment=self.env)
        self.assertEqual(row.token_hash, hashlib.sha256(raw_token.encode()).hexdigest())

    def test_noop_when_both_sides_present(self) -> None:
        # Pre-seed both sides with consistent state, then call: nothing should change.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="devopshero/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({
                "DOH_SIDECAR_TOKEN": "pre-existing-raw-token-of-reasonable-length-0123456789012345",
                "OTHER_KEY": "keep-me",
            }),
        )
        SidecarToken.objects.create(environment=self.env, token_hash="does-not-match-but-we-dont-check-here")

        original_secret = fake.store["devopshero/staging/shared-secrets"]["SecretString"]
        original_hash = SidecarToken.objects.get(environment=self.env).token_hash

        arn = secrets_utils.ensure_env_sidecar_token_exists(session=session, env=self.env)
        self.assertIn("shared-secrets", arn)
        self.assertEqual(fake.store["devopshero/staging/shared-secrets"]["SecretString"], original_secret)
        self.assertEqual(SidecarToken.objects.get(environment=self.env).token_hash, original_hash)

    def test_regenerates_when_row_exists_but_secret_missing(self) -> None:
        # Simulates a corrupted state where the DB row was created but the
        # AWS secret doesn't exist. We regenerate both in lockstep.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        SidecarToken.objects.create(environment=self.env, token_hash="stale-hash")

        secrets_utils.ensure_env_sidecar_token_exists(session=session, env=self.env)

        secret = json.loads(fake.store["devopshero/staging/shared-secrets"]["SecretString"])
        raw = secret["DOH_SIDECAR_TOKEN"]
        new_hash = SidecarToken.objects.get(environment=self.env).token_hash
        self.assertEqual(new_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotEqual(new_hash, "stale-hash")

    def test_preserves_other_shared_secret_keys(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="devopshero/staging/shared-secrets",
            Description="seed",
            SecretString=json.dumps({"SOMETHING_ELSE": "keep-me"}),
        )

        secrets_utils.ensure_env_sidecar_token_exists(session=session, env=self.env)

        secret = json.loads(fake.store["devopshero/staging/shared-secrets"]["SecretString"])
        self.assertEqual(secret["SOMETHING_ELSE"], "keep-me")
        self.assertIn("DOH_SIDECAR_TOKEN", secret)


# -----------------------------------------------------------------------------
# ensure_env_sidecar_jwt_key_exists
# -----------------------------------------------------------------------------


class TestEnsureEnvJwtKey(SidecarSecretsTestBase):

    def test_creates_keypair_when_missing(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        arn = secrets_utils.ensure_env_sidecar_jwt_key_exists(session=session, env_slug="staging")
        self.assertIn("sidecar-jwt-key", arn)

        payload = json.loads(fake.store["devopshero/staging/sidecar-jwt-key"]["SecretString"])
        self.assertIn("private_pem", payload)
        self.assertIn("public_pem", payload)
        self.assertTrue(payload["kid"].startswith("staging-"))
        # Sanity: private_pem is a valid PKCS8 PEM header.
        self.assertIn("BEGIN PRIVATE KEY", payload["private_pem"])
        self.assertIn("BEGIN PUBLIC KEY", payload["public_pem"])

    def test_noop_when_already_present(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="devopshero/staging/sidecar-jwt-key",
            Description="seed",
            SecretString=json.dumps({
                "private_pem": "pre", "public_pem": "pub", "kid": "seed-kid",
            }),
        )
        original = fake.store["devopshero/staging/sidecar-jwt-key"]["SecretString"]

        secrets_utils.ensure_env_sidecar_jwt_key_exists(session=session, env_slug="staging")

        self.assertEqual(fake.store["devopshero/staging/sidecar-jwt-key"]["SecretString"], original)


# -----------------------------------------------------------------------------
# ensure_env_oidc_config_secret_exists
# -----------------------------------------------------------------------------


class TestEnsureEnvOidcConfig(SidecarSecretsTestBase):

    def test_creates_oidc_secret_from_organization(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        arn = secrets_utils.ensure_env_oidc_config_secret_exists(session=session, env=self.env)
        self.assertIn("oidc-config", arn)
        payload = json.loads(fake.store["devopshero/staging/oidc-config"]["SecretString"])
        self.assertEqual(payload["issuer_url"], "https://okta.example.com/oauth2/default")
        self.assertEqual(payload["client_id"], "client-abc")
        self.assertEqual(payload["client_secret"], "secret-xyz")

    def test_rotating_client_secret_is_propagated(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        secrets_utils.ensure_env_oidc_config_secret_exists(session=session, env=self.env)

        # Simulate rotation on the Organization, then re-run.
        self.org.oidc_client_secret = "new-secret-123"
        self.org.save()
        secrets_utils.ensure_env_oidc_config_secret_exists(session=session, env=self.env)

        payload = json.loads(fake.store["devopshero/staging/oidc-config"]["SecretString"])
        self.assertEqual(payload["client_secret"], "new-secret-123")

    def test_raises_when_org_has_no_oidc(self) -> None:
        self.org.oidc_issuer_url = ""
        self.org.oidc_client_id = ""
        self.org.oidc_client_secret = ""
        self.org.save()
        fake = FakeSecretsManager()
        session = _session_with(fake)

        with self.assertRaises(RuntimeError) as cm:
            secrets_utils.ensure_env_oidc_config_secret_exists(session=session, env=self.env)
        self.assertIn("no OIDC config", str(cm.exception))


# -----------------------------------------------------------------------------
# ensure_env_sidecar_secrets_exist (umbrella)
# -----------------------------------------------------------------------------


class TestEnsureEnvSidecarSecrets(SidecarSecretsTestBase):

    def test_returns_all_three_arns_on_fresh_env(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)

        result = secrets_utils.ensure_env_sidecar_secrets_exist(session=session, env=self.env)
        self.assertIn("shared_secrets_arn", result)
        self.assertIn("jwt_key_arn", result)
        self.assertIn("oidc_config_arn", result)
        self.assertIn("devopshero/staging/shared-secrets", fake.store)
        self.assertIn("devopshero/staging/sidecar-jwt-key", fake.store)
        self.assertIn("devopshero/staging/oidc-config", fake.store)

        # Side effect: the SidecarToken row exists too.
        self.assertTrue(SidecarToken.objects.filter(environment=self.env).exists())


# -----------------------------------------------------------------------------
# ensure_app_secrets_exist
# -----------------------------------------------------------------------------


def _make_app_config(app_secrets: dict[str, str | None] | None) -> AppConfig:
    return AppConfig(
        app_name="simple-dashboard",
        ecr_repo_name="doh/staging/simple-dashboard",
        container_port=8000,
        cpu=256,
        memory=512,
        health_check_path="/health",
        health_check_command=None,
        environment_variables=[],
        app_source_path=None,
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

        self.assertIn("devopshero/staging/simple-dashboard/secrets", fake.store)
        payload = json.loads(fake.store["devopshero/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["slack_token"], "literal-token")
        self.assertEqual(len(payload["secret_key_base"]), 64)

    def test_merges_missing_keys_into_existing_secret(self) -> None:
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="devopshero/staging/simple-dashboard/secrets",
            Description="seed",
            SecretString=json.dumps({"slack_token": "existing-value"}),
        )
        app_config = _make_app_config({"slack_token": "NEW-IGNORED", "secret_key_base": None})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config, shared_secrets={},
        )

        payload = json.loads(fake.store["devopshero/staging/simple-dashboard/secrets"]["SecretString"])
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

        payload = json.loads(fake.store["devopshero/staging/simple-dashboard/secrets"]["SecretString"])
        self.assertEqual(payload["OPENAI_API_KEY"], "sk-from-shared")

    def test_heals_empty_existing_value_from_shared_secrets(self) -> None:
        # Simulates first-deploy having created the app secret with empty
        # placeholders because shared-secrets wasn't yet populated; a later
        # shared-set + redeploy must backfill the stored values.
        fake = FakeSecretsManager()
        session = _session_with(fake)
        fake.create_secret(
            Name="devopshero/staging/simple-dashboard/secrets",
            Description="seed",
            SecretString=json.dumps({"AWS_BEDROCK_ACCESS_KEY_ID": "", "HERMES_WEBUI_PASSWORD": "kept"}),
        )
        app_config = _make_app_config({"AWS_BEDROCK_ACCESS_KEY_ID": "", "HERMES_WEBUI_PASSWORD": ""})

        secrets_utils.ensure_app_secrets_exist(
            session=session, env_slug="staging", app_config=app_config,
            shared_secrets={"AWS_BEDROCK_ACCESS_KEY_ID": "AKIA-from-shared"},
        )

        payload = json.loads(fake.store["devopshero/staging/simple-dashboard/secrets"]["SecretString"])
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
