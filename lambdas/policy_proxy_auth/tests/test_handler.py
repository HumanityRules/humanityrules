"""Tests for the policy-proxy auth Lambda handler."""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest

import handler
from tests.conftest import TEST_ENV_DOMAIN, TEST_KID, make_alb_event


# -----------------------------------------------------------------------------
# /start
# -----------------------------------------------------------------------------


def test_start_without_rd_returns_400() -> None:
    response = handler.handler(make_alb_event(path="/start"), None)
    assert response["statusCode"] == 400


def test_start_with_off_domain_rd_returns_400() -> None:
    response = handler.handler(
        make_alb_event(path="/start", query={"rd": "https://attacker.com/"}),
        None,
    )
    assert response["statusCode"] == 400


def test_start_with_valid_rd_redirects_to_okta() -> None:
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    response = handler.handler(
        make_alb_event(path="/start", query={"rd": rd}),
        None,
    )
    assert response["statusCode"] == 302
    location = response["headers"]["location"]
    assert location.startswith("https://okta.example.com/oauth2/default/v1/authorize?")
    qs = parse_qs(urlparse(location).query)
    assert qs["client_id"] == ["client-id-xyz"]
    assert qs["response_type"] == ["code"]
    assert qs["scope"] == ["openid email profile"]
    assert qs["redirect_uri"] == [f"https://auth.{TEST_ENV_DOMAIN}/callback"]
    assert "state" in qs

    # state must be a JWT we can decode and pull rd back out of.
    state = qs["state"][0]


def test_start_percent_encoded_rd_still_accepted() -> None:
    # Regression: ALB -> Lambda delivers queryStringParameters values as
    # percent-encoded strings. Before the decode fix, urlparse saw scheme=''
    # for "https%3A%2F%2F..." and _validate_rd rejected every real request.
    import urllib.parse
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    encoded_rd = urllib.parse.quote(rd, safe="")
    response = handler.handler(
        make_alb_event(path="/start", query={"rd": encoded_rd}),
        None,
    )
    assert response["statusCode"] == 302, response
    # rd carried through the state JWT should be the decoded original URL.
    location = response["headers"]["location"]
    state = parse_qs(urlparse(location).query)["state"][0]
    assert handler._verify_state(state) == rd
    rd_back = handler._verify_state(state)
    assert rd_back == rd


# -----------------------------------------------------------------------------
# /callback
# -----------------------------------------------------------------------------


def _mint_valid_state(rd: str) -> str:
    return handler._mint_state(rd_url=rd)


def test_callback_missing_params_returns_400() -> None:
    response = handler.handler(make_alb_event(path="/callback"), None)
    assert response["statusCode"] == 400


def test_callback_invalid_state_returns_400() -> None:
    response = handler.handler(
        make_alb_event(path="/callback", query={"code": "c", "state": "not-a-jwt"}),
        None,
    )
    assert response["statusCode"] == 400


def test_callback_off_domain_rd_in_state_rejected(mock_http) -> None:
    # Even if the state JWT verifies, an rd pointing off-domain must be refused.
    state = _mint_valid_state("https://attacker.com/")
    response = handler.handler(
        make_alb_event(path="/callback", query={"code": "c", "state": state}),
        None,
    )
    assert response["statusCode"] == 400


def test_callback_happy_path(mock_http) -> None:
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    state = _mint_valid_state(rd)

    # First call: token exchange. Second call: userinfo.
    token_resp = type("R", (), {"status": 200, "data": json.dumps({"access_token": "at-1"}).encode()})()
    userinfo_resp = type("R", (), {
        "status": 200,
        "data": json.dumps({
            "sub": "okta|vmendi",
            "email": "vmendi@example.com",
            "given_name": "Victor", "family_name": "Mendiluce",
        }).encode(),
    })()
    mock_http.request.side_effect = [token_resp, userinfo_resp]

    response = handler.handler(
        make_alb_event(path="/callback", query={"code": "code-xyz", "state": state}),
        None,
    )
    assert response["statusCode"] == 302
    assert response["headers"]["location"] == rd
    set_cookie = response["headers"]["set-cookie"]
    assert set_cookie.startswith("doh_session=")
    assert f"Domain=.{TEST_ENV_DOMAIN}" in set_cookie
    assert "Secure" in set_cookie and "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie

    cookie_value = set_cookie.split("=", 1)[1].split(";", 1)[0]
    # Decode the session JWT with the public key and confirm claims.
    claims = jwt.decode(
        cookie_value,
        key=handler._cached_config.jwt_key.public_pem,
        algorithms=["RS256"],
    )
    assert claims["sub"] == "okta|vmendi"
    assert claims["username"] == "vmendi@example.com"
    assert claims["email"] == "vmendi@example.com"
    # exp - iat should equal SESSION_TTL_SECONDS
    assert claims["exp"] - claims["iat"] == handler.SESSION_TTL_SECONDS


def test_callback_token_exchange_failure_returns_502(mock_http) -> None:
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    state = _mint_valid_state(rd)
    bad = type("R", (), {"status": 400, "data": b"{}"})()
    mock_http.request.side_effect = [bad]
    response = handler.handler(
        make_alb_event(path="/callback", query={"code": "c", "state": state}),
        None,
    )
    assert response["statusCode"] == 502


# -----------------------------------------------------------------------------
# /.well-known/jwks.json
# -----------------------------------------------------------------------------


def test_jwks_endpoint_returns_valid_jwks() -> None:
    response = handler.handler(
        make_alb_event(path="/.well-known/jwks.json"), None,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "keys" in body and len(body["keys"]) == 1
    k = body["keys"][0]
    assert k["kty"] == "RSA"
    assert k["alg"] == "RS256"
    assert k["kid"] == TEST_KID
    # n and e are base64url without padding.
    assert "=" not in k["n"]
    assert "=" not in k["e"]


def test_jwks_policy_proxy_verifies_session_jwt_using_jwks_public_key() -> None:
    """End-to-end: mint a session JWT, export JWKS, reconstruct public key from JWKS, verify JWT."""
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
    from cryptography.hazmat.backends import default_backend

    jwks_response = handler.handler(
        make_alb_event(path="/.well-known/jwks.json"), None,
    )
    jwk = json.loads(jwks_response["body"])["keys"][0]

    def _b64url_uint_to_int(s: str) -> int:
        padded = s + "=" * (-len(s) % 4)
        return int.from_bytes(base64.urlsafe_b64decode(padded), "big")

    n = _b64url_uint_to_int(jwk["n"])
    e = _b64url_uint_to_int(jwk["e"])
    public_key = RSAPublicNumbers(e, n).public_key(default_backend())

    session_jwt = handler._mint_session_jwt(
        oidc_sub="okta|x", username="x@example.com", email="x@example.com",
    )
    claims = jwt.decode(session_jwt, key=public_key, algorithms=["RS256"])
    assert claims["sub"] == "okta|x"


# -----------------------------------------------------------------------------
# Unknown path
# -----------------------------------------------------------------------------


def test_unknown_path_returns_404() -> None:
    response = handler.handler(make_alb_event(path="/random"), None)
    assert response["statusCode"] == 404


# -----------------------------------------------------------------------------
# State expiry
# -----------------------------------------------------------------------------


def test_expired_state_rejected(monkeypatch) -> None:
    # Mint state "in the past" by stubbing time.time during mint only.
    rd = f"https://vmendi-hermes.{TEST_ENV_DOMAIN}/chat"
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() - handler.STATE_TTL_SECONDS - 60)
    state = handler._mint_state(rd_url=rd)
    monkeypatch.setattr(time, "time", real_time)

    response = handler.handler(
        make_alb_event(path="/callback", query={"code": "c", "state": state}),
        None,
    )
    assert response["statusCode"] == 400
