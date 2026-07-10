"""Tests for coalesced policy-proxy activity reports."""

import asyncio
import json

import httpx

from policy_proxy import activity_reporter


async def _wait_for_count(requests: list[httpx.Request], expected: int) -> None:
    deadline = asyncio.get_running_loop().time() + 2
    while len(requests) < expected:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {expected} requests; saw {len(requests)}")
        await asyncio.sleep(0.001)


async def test_first_observation_reports_immediately() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reporter = activity_reporter.PolicyProxyActivityReporter(
            http_client_provider=lambda: client,
            endpoint_url="https://humanityrules.io/api/runtime/policy-proxy-activity",
            env_bearer_token="secret-token",
            app_id="activity-agent",
            interval_seconds=60,
        )

        reporter.observe()
        await _wait_for_count(requests=requests, expected=1)
        await reporter.close()

    payload = json.loads(requests[0].content)
    assert payload["app_id"] == "activity-agent"
    assert payload["observed_at"].endswith("+00:00")
    assert requests[0].headers["authorization"] == "Bearer secret-token"


async def test_observations_inside_window_are_coalesced_to_latest_report() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reporter = activity_reporter.PolicyProxyActivityReporter(
            http_client_provider=lambda: client,
            endpoint_url="https://humanityrules.io/api/runtime/policy-proxy-activity",
            env_bearer_token="secret-token",
            app_id="activity-agent",
            interval_seconds=0.02,
        )

        reporter.observe()
        await _wait_for_count(requests=requests, expected=1)
        reporter.observe()
        reporter.observe()
        reporter.observe()
        await _wait_for_count(requests=requests, expected=2)
        await reporter.close()

    assert len(requests) == 2
    first = json.loads(requests[0].content)["observed_at"]
    second = json.loads(requests[1].content)["observed_at"]
    assert second >= first
