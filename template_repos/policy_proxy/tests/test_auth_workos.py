"""Tests for the auth-service role's WorkOS provider branch."""

import json
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


TEST_KID = "workos-test-kid"
TEST_ENV_DOMAIN = "personal.dohsandbox.com"
TEST_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:0:secret:workos-auth-config"


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
    private_pem, public_pem = rsa_pems
    payload = {
        "provider": "workos",
        "workos_config": {
            "client_id": "client_workos_xyz",
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
    workos_handler,
) -> TestClient:
    transport = httpx.MockTransport(workos_handler)
    fastapi_app = app_mod.create_auth_app(cfg=cfg, secrets_client=secrets_client)
    fastapi_app.state.http_client = httpx.AsyncClient(transport=transport)
    return TestClient(fastapi_app, base_url=f"https://auth.{TEST_ENV_DOMAIN}")


# -----------------------------------------------------------------------------
# Secret loading
# -----------------------------------------------------------------------------


def test_load_runtime_config_workos(fake_secrets_client) -> None:
    runtime = auth_mod.load_runtime_config(
        secret_arn=TEST_SECRET_ARN, secrets_client=fake_secrets_client,
    )
    assert runtime.provider == auth_mod.PROVIDER_WORKOS
    assert runtime.oidc is None
    assert runtime.workos is not None
    assert runtime.workos.client_id == "client_workos_xyz"


def test_load_runtime_config_unknown_provider_raises(rsa_pems) -> None:
    private_pem, public_pem = rsa_pems
    payload = {
        "provider": "google-direct",
        "jwt_key": {
            "private_pem": private_pem.decode("ascii"),
            "public_pem": public_pem.decode("ascii"),
            "kid": TEST_KID,
        },
    }
    client = MagicMock()
    client.get_secret_value.return_value = {"SecretString": json.dumps(payload)}
    with pytest.raises(RuntimeError, match="unknown auth provider"):
        auth_mod.load_runtime_config(secret_arn=TEST_SECRET_ARN, secrets_client=client)


# -----------------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------------


def test_workos_start_redirects_to_workos_authorize(auth_service_config, fake_secrets_client) -> None:
    async def workos(request: httpx.Request) -> httpx.Response:
        raise AssertionError("WorkOS should not be called at /start time")

    client = _mk_client(auth_service_config, fake_secrets_client, workos)
    rd = f"https://app.{TEST_ENV_DOMAIN}/chat"
    response = client.get(f"/start?rd={rd}", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://api.workos.com/user_management/authorize?")
    qs = parse_qs(urlparse(location).query)
    assert qs["client_id"] == ["client_workos_xyz"]
    assert qs["response_type"] == ["code"]
    assert qs["provider"] == ["GoogleOAuth"]
    assert qs["redirect_uri"] == [f"https://auth.{TEST_ENV_DOMAIN}/callback"]
    assert "state" in qs
    assert qs["code_challenge_method"] == ["S256"]
    assert "code_challenge" in qs

    runtime = auth_mod.load_runtime_config(secret_arn=TEST_SECRET_ARN, secrets_client=fake_secrets_client)
    state_claims = auth_mod._verify_state(state=qs["state"][0], key=runtime.jwt_key)
    assert state_claims is not None
    pkce_cookie = response.cookies.get(auth_mod.WORKOS_PKCE_COOKIE_NAME)
    assert pkce_cookie is not None
    code_verifier = auth_mod._verify_workos_pkce_cookie(
        cookie_value=pkce_cookie, nonce=state_claims.nonce, key=runtime.jwt_key,
    )
    assert code_verifier is not None
    assert qs["code_challenge"] == [auth_mod._workos_code_challenge(code_verifier=code_verifier)]
    set_cookie = response.headers["set-cookie"]
    assert f"{auth_mod.WORKOS_PKCE_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "Path=/callback" in set_cookie


def test_workos_start_off_domain_rd_returns_400(auth_service_config, fake_secrets_client) -> None:
    async def workos(request: httpx.Request) -> httpx.Response:
        raise AssertionError("WorkOS should not be called for invalid rd")

    client = _mk_client(auth_service_config, fake_secrets_client, workos)
    response = client.get("/start?rd=https://attacker.com/", follow_redirects=False)
    assert response.status_code == 400


# -----------------------------------------------------------------------------
# /callback
# -----------------------------------------------------------------------------


def _start_workos_flow(client: TestClient, rd: str) -> tuple[str, str]:
    response = client.get(f"/start?rd={rd}", follow_redirects=False)
    assert response.status_code == 302
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    pkce_cookie = response.cookies.get(auth_mod.WORKOS_PKCE_COOKIE_NAME)
    assert pkce_cookie is not None
    return state, pkce_cookie


def test_workos_callback_happy_path(auth_service_config, fake_secrets_client, rsa_pems) -> None:
    rd = f"https://app.{TEST_ENV_DOMAIN}/dashboard"

    captured: dict[str, Any] = {}

    async def workos(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "user": {
                "id": "user_01HXYZ",
                "email": "friend@gmail.com",
                "first_name": "Friendly",
                "last_name": "Stranger",
            },
            "access_token": "at_dummy",
        })

    client = _mk_client(auth_service_config, fake_secrets_client, workos)
    state, pkce_cookie = _start_workos_flow(client=client, rd=rd)
    response = client.get(f"/callback?code=code-xyz&state={state}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == rd

    assert captured["url"] == "https://api.workos.com/user_management/authenticate"
    assert captured["body"]["client_id"] == "client_workos_xyz"
    assert captured["body"]["code_verifier"]
    assert "client_secret" not in captured["body"]
    assert captured["body"]["grant_type"] == "authorization_code"
    assert captured["body"]["code"] == "code-xyz"

    runtime = auth_mod.load_runtime_config(secret_arn=TEST_SECRET_ARN, secrets_client=fake_secrets_client)
    state_claims = auth_mod._verify_state(state=state, key=runtime.jwt_key)
    assert state_claims is not None
    expected_code_verifier = auth_mod._verify_workos_pkce_cookie(
        cookie_value=pkce_cookie, nonce=state_claims.nonce, key=runtime.jwt_key,
    )
    assert captured["body"]["code_verifier"] == expected_code_verifier

    set_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(cookie for cookie in set_cookies if cookie.startswith("doh_session="))
    assert any(
        cookie.startswith(f"{auth_mod.WORKOS_PKCE_COOKIE_NAME}=") and "Max-Age=0" in cookie
        for cookie in set_cookies
    )
    cookie_value = session_cookie.split("=", 1)[1].split(";", 1)[0]
    _, public_pem = rsa_pems
    claims = jwt.decode(cookie_value, key=public_pem, algorithms=["RS256"])
    assert claims["sub"] == "user_01HXYZ"
    assert claims["email"] == "friend@gmail.com"
    assert claims["username"] == "friend@gmail.com"
    assert claims["provider"] == "workos"


def test_workos_callback_authenticate_failure_returns_502(auth_service_config, fake_secrets_client) -> None:
    rd = f"https://app.{TEST_ENV_DOMAIN}/chat"

    async def workos(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "invalid_grant"})

    client = _mk_client(auth_service_config, fake_secrets_client, workos)
    state, _ = _start_workos_flow(client=client, rd=rd)
    response = client.get(f"/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 502


def test_workos_callback_missing_pkce_cookie_returns_400(auth_service_config, fake_secrets_client) -> None:
    rd = f"https://app.{TEST_ENV_DOMAIN}/chat"

    async def workos(request: httpx.Request) -> httpx.Response:
        raise AssertionError("WorkOS should not be called without PKCE cookie")

    client = _mk_client(auth_service_config, fake_secrets_client, workos)
    state, _ = _start_workos_flow(client=client, rd=rd)
    client.cookies.clear()
    response = client.get(f"/callback?code=c&state={state}", follow_redirects=False)
    assert response.status_code == 400
    assert response.text == "missing pkce cookie"
