"""Tests for jwt_verify.verify_session_jwt against a fake JWKS client."""

import time

import jwt

from policy_proxy import jwt_verify


ENV_DOMAIN = "humr-sandbox.humrsandbox.com"


def test_valid_cookie_returns_identity(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter()
    identity = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert identity is not None
    assert identity.sub == "okta|vmendi"
    assert identity.provider == "oidc"
    assert identity.username == "vmendi"
    assert identity.email == "vmendi@example.com"


def test_workos_provider_returns_identity(fake_jwks_client, jwt_minter) -> None:
    """Both providers must verify; the WorkOS branch carries sub=workos_user_id."""
    token = jwt_minter(sub="user_01H...", provider="workos", username="alice@example.com")
    identity = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert identity is not None
    assert identity.provider == "workos"
    assert identity.sub == "user_01H..."


def test_unknown_provider_returns_none(fake_jwks_client, jwt_minter) -> None:
    """Defense-in-depth: unrecognised provider claim must not become a valid identity."""
    token = jwt_minter(provider="something-else")
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_expired_cookie_returns_none(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter(exp_offset=-120)
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_tampered_signature_returns_none(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter()
    head, payload, sig = token.split(".")
    mid = len(sig) // 2
    flipped_sig = sig[:mid] + ("A" if sig[mid] != "A" else "B") + sig[mid + 1:]
    bad_token = f"{head}.{payload}.{flipped_sig}"
    result = jwt_verify.verify_session_jwt(
        jwt_value=bad_token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_unknown_kid_returns_none(fake_jwks_client, jwt_minter) -> None:
    token = jwt_minter(kid="some-other-key")
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_missing_kid_header_returns_none(rsa_keypair, fake_jwks_client) -> None:
    private_key, _ = rsa_keypair
    now = int(time.time())
    token = jwt.encode(
        payload={"sub": "a", "username": "u", "aud": ENV_DOMAIN, "iat": now, "exp": now + 60},
        key=private_key, algorithm="RS256",
    )
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_aud_mismatch_returns_none(fake_jwks_client, jwt_minter) -> None:
    """Sibling-env replay: a token minted for one env-domain must NOT verify under another."""
    token = jwt_minter(aud="other-env.humrsandbox.com")
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_missing_aud_returns_none(rsa_keypair, fake_jwks_client) -> None:
    private_key, _ = rsa_keypair
    now = int(time.time())
    token = jwt.encode(
        payload={"sub": "a", "username": "u", "iat": now, "exp": now + 60},
        key=private_key, algorithm="RS256",
        headers={"kid": "test-key"},
    )
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain=ENV_DOMAIN,
    )
    assert result is None


def test_same_slug_different_domain_does_not_replay(fake_jwks_client, jwt_minter) -> None:
    """Two envs in different AWS accounts can share env_slug but never share env_domain.

    A token minted for env_domain=staging.acme-prod.com must NOT verify against a
    sibling env at staging.beta-co.com, even though both might have env_slug='staging'.
    This is the central-key replay risk the env-domain audience protects against.
    """
    token = jwt_minter(aud="staging.acme-prod.com")
    result = jwt_verify.verify_session_jwt(
        jwt_value=token, jwks_client=fake_jwks_client, env_domain="staging.beta-co.com",
    )
    assert result is None
