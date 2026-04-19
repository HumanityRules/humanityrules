"""Shared test fixtures: a test keypair, a JWT minter, and a SidecarConfig."""

import time
from dataclasses import replace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from sidecar import app as app_mod
from sidecar import config as config_mod
from sidecar import jwks as jwks_mod


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


@pytest.fixture
def primed_jwks_cache(rsa_keypair, sidecar_config):
    """A JwksCache pre-populated with the test public key (no network)."""
    _, public_key = rsa_keypair
    cache = jwks_mod.JwksCache.empty(
        jwks_url=sidecar_config.jwks_url, ttl_seconds=900,
    )
    # Mark it freshly populated so get_public_key doesn't trigger a refresh.
    cache.replace(keys_by_kid={TEST_KID: public_key}, now=time.time())
    return cache
