"""Tests for jwt_verify.verify_session_cookie against a fake JWKS client."""

import time

import jwt

from sidecar import jwt_verify


def test_valid_cookie_returns_identity(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter()
    identity = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert identity is not None
    assert identity.oidc_sub == "okta|vmendi"
    assert identity.username == "vmendi"
    assert identity.email == "vmendi@example.com"


def test_expired_cookie_returns_none(fake_jwks_client, jwt_minter) -> None:
    # Past exp + small leeway ⇒ still in the past.
    token = jwt_minter(exp_offset=-120)
    result = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert result is None


def test_tampered_signature_returns_none(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter()
    head, payload, sig = token.split(".")
    flipped_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    bad_token = f"{head}.{payload}.{flipped_sig}"
    result = jwt_verify.verify_session_cookie(
        jwt_value=bad_token, jwks_client=fake_jwks_client,
    )
    assert result is None


def test_unknown_kid_returns_none(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter(kid="some-other-key")
    result = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert result is None


def test_missing_kid_header_returns_none(rsa_keypair, fake_jwks_client) -> None:
    private_key, _ = rsa_keypair
    now = int(time.time())
    token = jwt.encode(
        payload={"sub": "a", "username": "u", "iat": now, "exp": now + 60},
        key=private_key, algorithm="RS256",
        # No kid header.
    )
    result = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert result is None
