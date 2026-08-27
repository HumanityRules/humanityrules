"""Tests for pdp.evaluate against a mocked HUMR endpoint."""

import httpx
import pytest

from policy_proxy import pdp as pdp_mod


@pytest.mark.asyncio
async def test_allow_response_parsed() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(200, json={"decision": "allow", "reason": "policy:x"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        decision = await pdp_mod.evaluate(
            http_client=client,
            pdp_url="https://humanityrules.io/api/pdp/evaluate",
            app_bearer_token="token-abc",
            provider="oidc",
            sub="okta|v",
            username="vmendi",
            path="/chat/new",
        )
    assert decision is not None
    assert decision.decision == "allow"
    assert decision.reason == "policy:x"
    assert captured["auth"] == "Bearer token-abc"
    # The bearer names the app; the payload must not carry an app identifier the
    # caller could have chosen.
    body = captured["body"]
    assert b"app_id" not in body
    assert b"vmendihermes" not in body


@pytest.mark.asyncio
async def test_deny_response_parsed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-policy"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        decision = await pdp_mod.evaluate(
            http_client=client,
            pdp_url="https://humanityrules.io/api/pdp/evaluate",
            app_bearer_token="t",
            provider="oidc", sub="y", username="z", path="/",
        )
    assert decision is not None
    assert decision.decision == "deny"


@pytest.mark.asyncio
async def test_non_200_returns_none() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        decision = await pdp_mod.evaluate(
            http_client=client,
            pdp_url="https://humanityrules.io/api/pdp/evaluate",
            app_bearer_token="t",
            provider="oidc", sub="y", username="z", path="/",
        )
    assert decision is None


@pytest.mark.asyncio
async def test_connection_error_returns_none() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        decision = await pdp_mod.evaluate(
            http_client=client,
            pdp_url="https://humanityrules.io/api/pdp/evaluate",
            app_bearer_token="t",
            provider="oidc", sub="y", username="z", path="/",
        )
    assert decision is None
