"""Shared fixtures: generate an RSA keypair, inject it + a fake OIDC config into module state."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


# Make `import handler` work when running pytest from the lambda dir.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handler  # noqa: E402


TEST_KID = "test-kid"
TEST_ENV_DOMAIN = "ch-sandbox.chsandbox.com"


@pytest.fixture
def rsa_keypair() -> tuple[bytes, bytes]:
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


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, rsa_keypair):
    """Set env vars + seed module caches so tests don't touch Secrets Manager."""
    private_pem, public_pem = rsa_keypair

    monkeypatch.setenv("DOH_ENV_DOMAIN", TEST_ENV_DOMAIN)
    monkeypatch.setenv("DOH_AUTH_BASE_URL", f"https://auth.{TEST_ENV_DOMAIN}")
    monkeypatch.setenv("DOH_OIDC_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:0:secret:oidc")
    monkeypatch.setenv("DOH_SIDECAR_JWT_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:0:secret:jwt")

    # Reset caches and inject them directly.
    handler._cached_oidc = handler.OidcConfig(
        issuer_url="https://okta.example.com/oauth2/default",
        client_id="client-id-xyz",
        client_secret="client-secret-abc",
    )
    handler._cached_jwt_key = handler.JwtKeyConfig(
        private_pem=private_pem,
        public_pem=public_pem,
        kid=TEST_KID,
    )
    yield
    handler._cached_oidc = None
    handler._cached_jwt_key = None


@pytest.fixture
def mock_http(monkeypatch):
    """Replace handler._http with a recording MagicMock. Individual tests set .request return values."""
    mock = MagicMock()
    monkeypatch.setattr(handler, "_http", mock)
    return mock


def make_alb_event(
    *,
    method: str = "GET",
    path: str = "/",
    query: dict[str, str] | None = None,
) -> dict:
    """Build an ALB -> Lambda event matching the AWS event shape."""
    return {
        "httpMethod": method,
        "path": path,
        "queryStringParameters": query or {},
        "headers": {},
        "body": "",
        "isBase64Encoded": False,
    }
