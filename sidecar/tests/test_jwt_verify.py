"""Tests for jwt_verify.verify_session_cookie against a primed JWKS cache."""

import time

import httpx
import jwt
import pytest

from sidecar import jwt_verify


@pytest.mark.asyncio
async def test_valid_cookie_returns_identity(primed_jwks_cache, jwt_minter) -> None:
    token = jwt_minter()
    async with httpx.AsyncClient() as client:
        identity = await jwt_verify.verify_session_cookie(
            jwt_value=token, jwks_cache=primed_jwks_cache, http_client=client,
        )
    assert identity is not None
    assert identity.oidc_sub == "okta|vmendi"
    assert identity.username == "vmendi"
    assert identity.email == "vmendi@example.com"


@pytest.mark.asyncio
async def test_expired_cookie_returns_none(primed_jwks_cache, jwt_minter) -> None:
    # Negative exp_offset + leeway of 30s still ends up in the past.
    token = jwt_minter(exp_offset=-120)
    async with httpx.AsyncClient() as client:
        result = await jwt_verify.verify_session_cookie(
            jwt_value=token, jwks_cache=primed_jwks_cache, http_client=client,
        )
    assert result is None


@pytest.mark.asyncio
async def test_tampered_signature_returns_none(primed_jwks_cache, jwt_minter) -> None:
    token = jwt_minter()
    # Flip one character in the signature segment.
    head, payload, sig = token.split(".")
    flipped_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    bad_token = f"{head}.{payload}.{flipped_sig}"
    async with httpx.AsyncClient() as client:
        result = await jwt_verify.verify_session_cookie(
            jwt_value=bad_token, jwks_cache=primed_jwks_cache, http_client=client,
        )
    assert result is None


@pytest.mark.asyncio
async def test_unknown_kid_returns_none(primed_jwks_cache, jwt_minter) -> None:
    token = jwt_minter(kid="some-other-key")
    # Patch the JWKS URL to a no-op transport so refresh doesn't hit the network.

    async def _no_route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"keys": []})

    transport = httpx.MockTransport(_no_route)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await jwt_verify.verify_session_cookie(
            jwt_value=token, jwks_cache=primed_jwks_cache, http_client=client,
        )
    assert result is None


@pytest.mark.asyncio
async def test_missing_kid_header_returns_none(rsa_keypair, primed_jwks_cache) -> None:
    private_key, _ = rsa_keypair
    now = int(time.time())
    token = jwt.encode(
        payload={"sub": "a", "username": "u", "iat": now, "exp": now + 60},
        key=private_key, algorithm="RS256",
        # No kid header.
    )
    async with httpx.AsyncClient() as client:
        result = await jwt_verify.verify_session_cookie(
            jwt_value=token, jwks_cache=primed_jwks_cache, http_client=client,
        )
    assert result is None
