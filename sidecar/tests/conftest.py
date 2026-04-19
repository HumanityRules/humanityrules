"""Shared test fixtures: a test keypair, a JWT minter, and a SidecarConfig."""

import time
from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from sidecar import config as config_mod


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
        exp_offset: int = 3600,
        kid: str = TEST_KID,
    ) -> str:
        now = int(time.time())
        claims = {
            "sub": sub,
            "username": username,
            "email": email,
            "iat": now,
            "exp": now + exp_offset,
        }
        return jwt.encode(
            payload=claims, key=private_key, algorithm="RS256",
            headers={"kid": kid},
        )

    return make


@pytest.fixture
def sidecar_config() -> config_mod.SidecarConfig:
    return config_mod.SidecarConfig(
        app_id="vmendi-hermes",
        env_slug="ch-sandbox",
        env_domain="ch-sandbox.chsandbox.com",
        auth_base_url="https://auth.ch-sandbox.chsandbox.com",
        jwks_url="https://auth.ch-sandbox.chsandbox.com/.well-known/jwks.json",
        pdp_url="https://devopshero.ai/api/pdp/evaluate",
        sidecar_token="t" * 64,
        upstream_host="127.0.0.1",
        upstream_port=8787,
        listen_port=8443,
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
