"""Tests for the central env-SSO endpoints in views/auth_env_sso.py."""

import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings
from django.urls import reverse

from humanityrules_app.models import AWSAccount, Environment, Organization


def _make_keypair_pem() -> tuple[bytes, bytes]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


_PRIVATE_PEM, _PUBLIC_PEM = _make_keypair_pem()
_TEST_KID = "test-kid"


def _settings_overrides() -> dict:
    return {
        "DOH_ENV_SESSION_JWT_PRIVATE_KEY": _PRIVATE_PEM.decode("ascii"),
        "DOH_ENV_SESSION_JWT_KID": _TEST_KID,
        "WORKOS_API_KEY": "test-workos-key",
        "WORKOS_CLIENT_ID": "test-workos-client",
    }


@override_settings(**_settings_overrides())
class EnvSsoTestBase(TestCase):

    def setUp(self) -> None:
        self.workos_org = Organization.objects.create(
            name="WorkOS Org", slug="workos-org",
            auth_provider=Organization.AuthProvider.WORKOS,
        )
        self.oidc_org = Organization.objects.create(
            name="OIDC Org", slug="oidc-org",
            auth_provider=Organization.AuthProvider.OIDC,
            oidc_issuer_url="https://idp.example.com",
            oidc_client_id="cid",
            oidc_client_secret="csec",
        )
        self.workos_account = AWSAccount.objects.create(
            organization=self.workos_org, name="workos-account",
        )
        self.oidc_account = AWSAccount.objects.create(
            organization=self.oidc_org, name="oidc-account",
        )
        self.workos_env = Environment.objects.create(
            aws_account=self.workos_account, name="prod", slug="workos-prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.workos-customer.com",
        )
        self.oidc_env = Environment.objects.create(
            aws_account=self.oidc_account, name="prod", slug="oidc-prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.oidc-customer.com",
        )


class TestEnvStart(EnvSsoTestBase):

    def test_missing_rd_is_rejected(self) -> None:
        response = self.client.get(reverse("env_start"))
        self.assertEqual(response.status_code, 400)

    def test_rd_outside_known_envs_is_rejected(self) -> None:
        response = self.client.get(reverse("env_start"), {"rd": "https://evil.example.com/path"})
        self.assertEqual(response.status_code, 400)

    def test_rd_with_http_scheme_is_rejected(self) -> None:
        response = self.client.get(reverse("env_start"), {"rd": "http://app.prod.workos-customer.com/"})
        self.assertEqual(response.status_code, 400)

    def test_workos_org_redirects_to_authkit(self) -> None:
        with patch("humanityrules_app.views.auth_env_sso.WorkOSClient") as mock_workos:
            mock_workos.return_value.user_management.get_authorization_url.return_value = (
                "https://api.workos.com/sso/auth?redirect=...&state=..."
            )
            response = self.client.get(
                reverse("env_start"),
                {"rd": "https://app.prod.workos-customer.com/some/path"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("https://api.workos.com/sso/auth"))
        call_kwargs = mock_workos.return_value.user_management.get_authorization_url.call_args.kwargs
        self.assertEqual(call_kwargs["provider"], "authkit")
        # state must be a JWT we can verify with our public key.
        claims = jwt.decode(
            call_kwargs["state"], key=_PUBLIC_PEM,
            algorithms=["RS256"], leeway=5,
        )
        self.assertEqual(claims["env_slug"], "workos-prod")
        self.assertEqual(claims["rd"], "https://app.prod.workos-customer.com/some/path")

    def test_oidc_org_redirects_to_issuer(self) -> None:
        response = self.client.get(
            reverse("env_start"),
            {"rd": "https://app.prod.oidc-customer.com/foo"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("idp.example.com/v1/authorize", response["Location"])
        self.assertIn("client_id=cid", response["Location"])

    def test_picks_longest_zone_match(self) -> None:
        # Sub-env carved out of the larger env's zone — must resolve to the more specific one.
        deeper_account = AWSAccount.objects.create(
            organization=self.workos_org, name="deeper",
        )
        deeper = Environment.objects.create(
            aws_account=deeper_account, name="deeper", slug="deeper",
            aws_region="us-east-1",
            shared_alb_hosted_zone="staging.prod.workos-customer.com",
        )
        with patch("humanityrules_app.views.auth_env_sso.WorkOSClient") as mock_workos:
            mock_workos.return_value.user_management.get_authorization_url.return_value = "https://api.workos.com/sso/auth"
            self.client.get(
                reverse("env_start"),
                {"rd": "https://x.staging.prod.workos-customer.com/"},
            )
        call_kwargs = mock_workos.return_value.user_management.get_authorization_url.call_args.kwargs
        claims = jwt.decode(call_kwargs["state"], key=_PUBLIC_PEM, algorithms=["RS256"], leeway=5)
        self.assertEqual(claims["env_slug"], deeper.slug)


class TestEnvCallback(EnvSsoTestBase):

    def _mint_state(self, *, rd: str, env_slug: str, exp_offset: int = 600) -> str:
        now = int(time.time())
        return jwt.encode(
            payload={
                "rd": rd, "env_slug": env_slug, "nonce": "n",
                "iat": now, "exp": now + exp_offset,
            },
            key=_PRIVATE_PEM, algorithm="RS256",
            headers={"kid": _TEST_KID},
        )

    def test_missing_code_or_state_returns_400(self) -> None:
        self.assertEqual(self.client.get(reverse("env_callback"), {"code": "x"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("env_callback"), {"state": "x"}).status_code, 400)

    def test_invalid_state_returns_400(self) -> None:
        response = self.client.get(reverse("env_callback"), {"code": "x", "state": "garbage"})
        self.assertEqual(response.status_code, 400)

    def test_expired_state_returns_400(self) -> None:
        state = self._mint_state(
            rd="https://app.prod.workos-customer.com/", env_slug="workos-prod",
            exp_offset=-120,
        )
        response = self.client.get(reverse("env_callback"), {"code": "x", "state": state})
        self.assertEqual(response.status_code, 400)

    def test_state_env_slug_must_match_resolved_env(self) -> None:
        # rd resolves to workos-prod, but state claims oidc-prod — reject.
        state = self._mint_state(
            rd="https://app.prod.workos-customer.com/", env_slug="oidc-prod",
        )
        response = self.client.get(reverse("env_callback"), {"code": "x", "state": state})
        self.assertEqual(response.status_code, 400)

    def test_workos_callback_mints_session_and_redirects_to_install(self) -> None:
        rd = "https://app.prod.workos-customer.com/dashboard?x=1"
        state = self._mint_state(rd=rd, env_slug="workos-prod")

        fake_user = type("U", (), {"id": "user_123", "email": "alice@example.com"})()
        fake_response = type("R", (), {"user": fake_user})()

        with patch("humanityrules_app.views.auth_env_sso.WorkOSClient") as mock_workos:
            mock_workos.return_value.user_management.authenticate_with_code.return_value = fake_response
            response = self.client.get(
                reverse("env_callback"), {"code": "abc", "state": state},
            )
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith("https://app.prod.workos-customer.com/__doh_session_install?"))

        # Extract token from the install URL and verify it round-trips. The audience
        # must be the env's globally unique DNS zone, not the env_slug — slugs are
        # only unique per AWS account and would allow cross-env replay under one JWKS.
        from urllib.parse import parse_qs, urlparse
        qs = parse_qs(urlparse(location).query)
        token = qs["token"][0]
        claims = jwt.decode(
            token, key=_PUBLIC_PEM, algorithms=["RS256"],
            audience="prod.workos-customer.com",
        )
        self.assertEqual(claims["sub"], "user_123")
        self.assertEqual(claims["email"], "alice@example.com")
        self.assertEqual(claims["provider"], "workos")
        self.assertEqual(claims["aud"], "prod.workos-customer.com")

    def test_session_audience_is_env_domain_not_slug(self) -> None:
        """Two envs in different AWS accounts can legally share the same env_slug.

        With one central JWKS, the session audience MUST be the globally-unique
        DNS zone (env_domain), not env_slug — otherwise a token minted for env A
        with slug='prod' verifies in env B with slug='prod' under another account.
        """
        # Second WorkOS env in a *different* AWS account, deliberately reusing
        # env.slug='prod' (allowed: unique_together is (aws_account, slug)).
        sibling_account = AWSAccount.objects.create(
            organization=self.workos_org, name="sibling-account",
        )
        sibling_env = Environment.objects.create(
            aws_account=sibling_account, name="prod", slug="workos-prod",
            aws_region="us-east-1", shared_alb_hosted_zone="prod.sibling-customer.com",
        )

        rd = "https://app.prod.workos-customer.com/foo"
        state = self._mint_state(rd=rd, env_slug="workos-prod")
        fake_user = type("U", (), {"id": "user_1", "email": "a@example.com"})()
        fake_response = type("R", (), {"user": fake_user})()
        with patch("humanityrules_app.views.auth_env_sso.WorkOSClient") as mock_workos:
            mock_workos.return_value.user_management.authenticate_with_code.return_value = fake_response
            response = self.client.get(reverse("env_callback"), {"code": "abc", "state": state})

        from urllib.parse import parse_qs, urlparse
        token = parse_qs(urlparse(response["Location"]).query)["token"][0]

        # Audience is the original env's domain — NOT the slug.
        claims = jwt.decode(
            token, key=_PUBLIC_PEM, algorithms=["RS256"],
            audience="prod.workos-customer.com",
        )
        self.assertEqual(claims["aud"], "prod.workos-customer.com")

        # Verifying with the sibling env's domain MUST fail, even though the slug matches.
        with self.assertRaises(jwt.InvalidAudienceError):
            jwt.decode(
                token, key=_PUBLIC_PEM, algorithms=["RS256"],
                audience=sibling_env.shared_alb_hosted_zone,
            )

    def test_oidc_callback_mints_session_with_userinfo(self) -> None:
        rd = "https://api.prod.oidc-customer.com/things"
        state = self._mint_state(rd=rd, env_slug="oidc-prod")

        with patch("humanityrules_app.views.auth_env_sso._exchange_oidc_code") as mock_exchange:
            mock_exchange.return_value = {"sub": "okta|bob", "email": "bob@example.com"}
            response = self.client.get(
                reverse("env_callback"), {"code": "abc", "state": state},
            )
        self.assertEqual(response.status_code, 302)
        from urllib.parse import parse_qs, urlparse
        token = parse_qs(urlparse(response["Location"]).query)["token"][0]
        claims = jwt.decode(
            token, key=_PUBLIC_PEM, algorithms=["RS256"],
            audience="prod.oidc-customer.com",
        )
        self.assertEqual(claims["sub"], "okta|bob")
        self.assertEqual(claims["provider"], "oidc")
        self.assertEqual(claims["aud"], "prod.oidc-customer.com")


class TestEnvJwks(EnvSsoTestBase):

    def test_jwks_publishes_public_key(self) -> None:
        response = self.client.get(reverse("env_jwks"))
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(len(body["keys"]), 1)
        key = body["keys"][0]
        self.assertEqual(key["kty"], "RSA")
        self.assertEqual(key["alg"], "RS256")
        self.assertEqual(key["kid"], _TEST_KID)
        self.assertIn("n", key)
        self.assertIn("e", key)
        self.assertIn("max-age=300", response["Cache-Control"])
