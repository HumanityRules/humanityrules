"""Shared test fixtures: a test keypair, a JWT minter, and a PolicyProxyConfig."""

import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from policy_proxy import config as config_mod


TEST_KID = "test-key"


@pytest.fixture
def rsa_keypair() -> tuple[object, object]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture
def jwt_minter(rsa_keypair):
    private_key, _ = rsa_keypair

    def make(
        sub: str = "okta|vmendi",
        username: str = "vmendi",
        email: str = "vmendi@example.com",
        provider: str = "oidc",
        exp_offset: int = 3600,
        kid: str = TEST_KID,
        aud: str = "humr-sandbox.humrsandbox.com",
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": sub,
            "username": username,
            "email": email,
            "provider": provider,
            "aud": aud,
            "iat": now,
            "exp": now + exp_offset,
        }
        return jwt.encode(
            payload=claims, key=private_key, algorithm="RS256",
            headers={"kid": kid},
        )

    return make


@pytest.fixture
def policy_proxy_config() -> config_mod.PolicyProxyConfig:
    return config_mod.PolicyProxyConfig(
        app_id="vmendi-hermes",
        env_slug="humr-sandbox",
        env_domain="humr-sandbox.humrsandbox.com",
        auth_base_url="https://auth.humr-sandbox.humrsandbox.com",
        control_plane_url="https://humanityrules.io",
        jwks_url="https://auth.humr-sandbox.humrsandbox.com/.well-known/jwks.json",
        pdp_url="https://humanityrules.io/api/pdp/evaluate",
        env_bearer_token="t" * 64,
        upstream_host="127.0.0.1",
        upstream_port=8787,
        listen_port=8443,
        pdp_cache_ttl_seconds=0,  # disabled by default; tests opt in explicitly
        activity_report_interval_seconds=300,
    )


@dataclass
class _FakeSigningKey:
    key: Any


class FakeJwksClient:
    """Minimal stand-in for jwt.PyJWKClient: serves a single test key by any kid."""

    def __init__(self, public_key: Any, known_kid: str) -> None:
        self._public_key = public_key
        self._known_kid = known_kid

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if kid != self._known_kid:
            raise jwt.PyJWKClientError(f"unknown kid {kid!r}")
        return _FakeSigningKey(key=self._public_key)


@pytest.fixture
def fake_jwks_client(rsa_keypair) -> FakeJwksClient:
    _, public_key = rsa_keypair
    return FakeJwksClient(public_key=public_key, known_kid=TEST_KID)
