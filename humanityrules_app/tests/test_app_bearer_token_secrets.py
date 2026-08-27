"""Tests for the per-app bearer-token mint in infra_customer.secrets_utils.

The mint is "DB wins": the control plane only ever accepts the hash it stored,
so the single no-op case is a row whose hash already matches the value in the
app's Secrets Manager bag. Everything else — no row, no bag, no key, or a
mismatch — re-mints and writes both sides together.

AWS is faked with the same in-memory FakeSecretsManager the env-bearer suite
uses, injected through the `session` parameter; no moto, no patching.
"""

import hashlib
import json

from django.test import TestCase

from humanityrules_app.models import App, AppBearerToken, AWSAccount, Environment, Organization, Workspace
from humanityrules_app.services.infra_customer import secrets_utils
from humanityrules_app.services.jobs import app_config_builder
from humanityrules_app.services.infra_customer.appconfig import ContainerConfig, ImageSource
from humanityrules_app.tests.app_test_factories import make_source_template
from humanityrules_app.tests.test_env_bearer_token_secrets import FakeSecretsManager, _session_with


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
