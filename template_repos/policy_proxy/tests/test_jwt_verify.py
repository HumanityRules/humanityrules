"""Tests for jwt_verify.verify_session_cookie against a fake JWKS client."""

import time

import jwt

from policy_proxy import jwt_verify


def test_valid_cookie_returns_identity(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter()
    identity = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert identity is not None
    assert identity.sub == "okta|vmendi"
    assert identity.username == "vmendi"
    assert identity.email == "vmendi@example.com"
    assert identity.provider == "oidc"


def test_expired_cookie_returns_none(fake_jwks_client, jwt_minter) -> None:
    # Past exp + small leeway ⇒ still in the past.
    token = jwt_minter(exp_offset=-120)
    result = jwt_verify.verify_session_cookie(
        jwt_value=token, jwks_client=fake_jwks_client,
    )
    assert result is None


def test_tampered_signature_returns_none(fake_jwks_client, jwt_minter) -> None:
    # Flip a middle signature char rather than the final one: the trailing
    # base64url char's low bits get truncated by an unpadded-length decode,
    # so a final-char flip can decode to the same RSA signature bytes.
    token = jwt_minter()
    head, payload, sig = token.split(".")
    mid = len(sig) // 2
    flipped_sig = sig[:mid] + ("A" if sig[mid] != "A" else "B") + sig[mid + 1:]
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
