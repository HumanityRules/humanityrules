"""Tests for the auth-service role (OAuth dance, JWT minting, JWKS)."""

import base64
import json
import time
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi.testclient import TestClient

from policy_proxy import app as app_mod
from policy_proxy import auth as auth_mod
from policy_proxy import config as config_mod


TEST_KID = "test-kid"
TEST_ENV_DOMAIN = "ch-sandbox.chsandbox.com"
TEST_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:0:secret:policy-proxy-auth-config"


@pytest.fixture
def auth_service_config() -> config_mod.AuthServiceConfig:
    return config_mod.AuthServiceConfig(
        env_domain=TEST_ENV_DOMAIN,
        auth_base_url=f"https://auth.{TEST_ENV_DOMAIN}",
        listen_port=8443,
        auth_config_secret_arn=TEST_SECRET_ARN,
        session_ttl_seconds=config_mod.DEFAULT_SESSION_TTL_SECONDS,
    )


@pytest.fixture
def rsa_pems(rsa_keypair) -> tuple[bytes, bytes]:
    """Reshape the shared RSA keypair fixture into PKCS8 PEM bytes for the auth role."""
    private_key, public_key = rsa_keypair
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture
def fake_secrets_client(rsa_pems: tuple[bytes, bytes]) -> Any:
    """Secrets Manager client stub returning an auth-config secret."""
    private_pem, public_pem = rsa_pems
    payload = {
        "oidc_config": {
            "issuer_url": "https://okta.example.com/oauth2/default",
            "client_id": "client-id-xyz",
            "client_secret": "client-secret-abc",
        },
        "jwt_key": {
            "private_pem": private_pem.decode("ascii"),
            "public_pem": public_pem.decode("ascii"),
            "kid": TEST_KID,
        },
    }
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": json.dumps(payload)}
    return client


def _mk_client(
    cfg: config_mod.AuthServiceConfig,
    secrets_client: Any,
    okta_handler,
) -> TestClient:
    """Build create_auth_app() and swap its http client for a MockTransport routed to okta_handler."""
    transport = httpx.MockTransport(okta_handler)
    fastapi_app = app_mod.create_auth_app(cfg=cfg, secrets_client=secrets_client)
    # Replace the real outbound httpx client with a mocked transport.
    fastapi_app.state.http_client = httpx.AsyncClient(transport=transport)
    return TestClient(fastapi_app)


# -----------------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------------


def test_start_without_rd_returns_400(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/start", follow_redirects=False)
    assert response.status_code == 400


def test_start_with_off_domain_rd_returns_400(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/start?rd=https://attacker.com/", follow_redirects=False)
    assert response.status_code == 400


def test_start_with_valid_rd_redirects_to_okta(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called at /start time")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    response = client.get(f"/start?rd={rd}", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://okta.example.com/oauth2/default/v1/authorize?")
    qs = parse_qs(urlparse(location).query)
    assert qs["client_id"] == ["client-id-xyz"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["openid email profile"]
    assert qs["redirect_uri"] == [f"https://auth.{TEST_ENV_DOMAIN}/callback"]
    assert "state" in qs


# -----------------------------------------------------------------------------
# /callback
# -----------------------------------------------------------------------------


def _mint_state_directly(cfg: config_mod.AuthServiceConfig, secrets_client: Any, rd: str) -> str:
    """Helper: load runtime config and mint a valid state JWT without going through /start."""
    runtime = auth_mod.load_runtime_config(
        secret_arn=cfg.auth_config_secret_arn, secrets_client=secrets_client,
    )
    return auth_mod._mint_state(rd_url=rd, key=runtime.jwt_key)


def test_callback_missing_params_returns_400(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/callback", follow_redirects=False)
    assert response.status_code == 400


def test_callback_invalid_state_returns_400(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/callback?code=c&state=not-a-jwt", follow_redirects=False)
    assert response.status_code == 400


def test_callback_off_domain_rd_in_state_rejected(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called for off-domain rd")

    state = _mint_state_directly(auth_service_config, fake_secrets_client, "https://attacker.com/")
    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get(f"/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 400


def test_callback_happy_path(auth_service_config, fake_secrets_client, rsa_pems) -> None:
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    state = _mint_state_directly(auth_service_config, fake_secrets_client, rd)

    async def okta(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/token"):
            return httpx.Response(200, json={"access_token": "at-1"})
        if url.endswith("/v1/userinfo"):
            return httpx.Response(200, json={
                "sub": "okta|vmendi",
                "email": "vmendi@example.com",
                "given_name": "Victor", "family_name": "Mendiluce",
            })
        raise AssertionError(f"unexpected outbound URL: {url}")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get(f"/callback?code=code-xyz&state={state}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == rd
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("doh_session=")
    assert f"Domain=.{TEST_ENV_DOMAIN}" in set_cookie
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie

    cookie_value = set_cookie.split("=", 1)[1].split(";", 1)[0]
    _, public_pem = rsa_pems
    claims = jwt.decode(cookie_value, key=public_pem, algorithms=["RS256"])
    assert claims["sub"] == "okta|vmendi"
    assert claims["username"] == "vmendi@example.com"
    assert claims["email"] == "vmendi@example.com"
    assert claims["exp"] - claims["iat"] == config_mod.DEFAULT_SESSION_TTL_SECONDS
    assert f"Max-Age={config_mod.DEFAULT_SESSION_TTL_SECONDS}" in set_cookie


def test_callback_respects_configured_session_ttl(auth_service_config, fake_secrets_client, rsa_pems) -> None:
    cfg = replace(auth_service_config, session_ttl_seconds=7200)
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    state = _mint_state_directly(cfg, fake_secrets_client, rd)

    async def okta(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/token"):
            return httpx.Response(200, json={"access_token": "at-1"})
        if url.endswith("/v1/userinfo"):
            return httpx.Response(200, json={"sub": "okta|vmendi", "email": "vmendi@example.com"})
        raise AssertionError(f"unexpected outbound URL: {url}")

    client = _mk_client(cfg, fake_secrets_client, okta)
    response = client.get(f"/callback?code=code-xyz&state={state}", follow_redirects=False)
    set_cookie = response.headers["set-cookie"]
    cookie_value = set_cookie.split("=", 1)[1].split(";", 1)[0]
    _, public_pem = rsa_pems
    claims = jwt.decode(cookie_value, key=public_pem, algorithms=["RS256"])

    assert claims["exp"] - claims["iat"] == 7200
    assert "Max-Age=7200" in set_cookie


def test_callback_token_exchange_failure_returns_502(auth_service_config, fake_secrets_client) -> None:
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    state = _mint_state_directly(auth_service_config, fake_secrets_client, rd)

    async def okta(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get(f"/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 502


# -----------------------------------------------------------------------------
# /.well-known/jwks.json
# -----------------------------------------------------------------------------


def test_jwks_endpoint_returns_valid_jwks(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
    body = response.json()
    assert "keys" in body and len(body["keys"]) == 1
    k = body["keys"][0]
    assert k["kty"] == "RSA"
    assert k["alg"] == "RS256"
    assert k["kid"] == TEST_KID
    assert "=" not in k["n"]
    assert "=" not in k["e"]


def test_jwks_round_trip_verifies_session_jwt(auth_service_config, fake_secrets_client) -> None:
    """End-to-end: mint a session JWT, export JWKS, reconstruct public key from JWKS, verify JWT."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    jwks_response = client.get("/.well-known/jwks.json")
    jwk_key = jwks_response.json()["keys"][0]

    def _b64url_uint_to_int(s: str) -> int:
        padded = s + "=" * (-len(s) % 4)
        return int.from_bytes(base64.urlsafe_b64decode(padded), "big")

    n = _b64url_uint_to_int(jwk_key["n"])
    e = _b64url_uint_to_int(jwk_key["e"])
    public_key = RSAPublicNumbers(e, n).public_key(default_backend())

    runtime = auth_mod.load_runtime_config(
        secret_arn=auth_service_config.auth_config_secret_arn, secrets_client=fake_secrets_client,
    )
    session_jwt = auth_mod._mint_session_jwt(
        oidc_sub="okta|x",
        username="x@example.com",
        email="x@example.com",
        ttl_seconds=config_mod.DEFAULT_SESSION_TTL_SECONDS,
        key=runtime.jwt_key,
    )
    claims = jwt.decode(session_jwt, key=public_key, algorithms=["RS256"])
    assert claims["sub"] == "okta|x"


# -----------------------------------------------------------------------------
# /__policy_proxy/healthz (shared route, bypasses auth/PDP)
# -----------------------------------------------------------------------------


def test_healthz_returns_ok(auth_service_config, fake_secrets_client) -> None:
    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called on health checks")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get("/__policy_proxy/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


# -----------------------------------------------------------------------------
# State expiry
# -----------------------------------------------------------------------------


def test_expired_state_rejected(auth_service_config, fake_secrets_client, monkeypatch) -> None:
    """Mint state 'in the past' by stubbing time.time during mint only."""
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    runtime = auth_mod.load_runtime_config(
        secret_arn=auth_service_config.auth_config_secret_arn, secrets_client=fake_secrets_client,
    )
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() - auth_mod.STATE_TTL_SECONDS - 60)
    state = auth_mod._mint_state(rd_url=rd, key=runtime.jwt_key)
    monkeypatch.setattr(time, "time", real_time)

    async def okta(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Okta should not be called for expired state")

    client = _mk_client(auth_service_config, fake_secrets_client, okta)
    response = client.get(f"/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 400
