"""End-to-end tests for the FastAPI policy-proxy app, with PDP + upstream mocked."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from policy_proxy import app as app_mod
from policy_proxy import config as config_mod
from policy_proxy import jwt_verify


def _streamed_body(*chunks: bytes):
    """Wrap *chunks* as an async generator so MockTransport serves them via aiter_raw."""
    async def _gen():
        for chunk in chunks:
            yield chunk
    return _gen()


def _mk_client(
    cfg: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    pdp_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
    upstream_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> TestClient:
    """Build a real create_app() + swap http_client and jwks_client on app.state."""

    async def _router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cfg.pdp_url):
            return await pdp_handler(request)
        if url.startswith(f"{cfg.control_plane_url}/api/runtime/policy-proxy-activity"):
            return httpx.Response(200, json={"ok": True})
        upstream_prefix = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
        if url.startswith(upstream_prefix):
            return await upstream_handler(request)
        raise AssertionError(f"unexpected outbound URL: {url}")

    fastapi_app = app_mod.create_app(cfg=cfg)
    fastapi_app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_router))
    fastapi_app.state.jwks_client = fake_jwks_client
    return TestClient(fastapi_app)


# --- test cases ---


def test_missing_cookie_redirects_to_auth(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get("/chat/new", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.humr-sandbox.humrsandbox.com"
    assert parsed.path == "/auth/env-start"
    rd = parse_qs(parsed.query)["rd"][0]
    assert rd.endswith("/chat/new")


def test_missing_cookie_api_returns_401_with_auth_url(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    referer = "https://vmendi-hermes.humr-sandbox.humrsandbox.com/"
    response = client.get(
        "/api/sessions",
        headers={
            "referer": referer,
            "sec-fetch-mode": "cors",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    auth_url = response.headers[app_mod.AUTH_URL_HEADER]
    parsed = urlparse(auth_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.humr-sandbox.humrsandbox.com"
    assert parsed.path == "/auth/env-start"
    assert parse_qs(parsed.query)["rd"] == [referer]


def test_allow_proxies_to_upstream(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        # Validate the policy proxy forwarded the identity headers.
        assert request.headers["x-auth-user"] == "vmendi"
        assert request.headers["x-auth-sub"] == "okta|vmendi"
        assert request.headers["x-auth-provider"] == "oidc"
        return httpx.Response(200, content=_streamed_body(b"hello from upstream"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 200
    assert response.text == "hello from upstream"


def test_allow_observes_policy_proxy_activity(
    policy_proxy_config: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    jwt_minter: Callable[..., str],
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    client.app.state.activity_reporter.observe = mock.Mock()

    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: jwt_minter()},
    )

    assert response.status_code == 200
    client.app.state.activity_reporter.observe.assert_called_once_with()


def test_deny_returns_403(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-match"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called on deny")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 403


def test_deny_does_not_observe_policy_proxy_activity(
    policy_proxy_config: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    jwt_minter: Callable[..., str],
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-match"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called on deny")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    client.app.state.activity_reporter.observe = mock.Mock()

    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: jwt_minter()},
    )

    assert response.status_code == 403
    client.app.state.activity_reporter.observe.assert_not_called()


def test_pdp_unreachable_returns_503(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called when PDP is down")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 503


def test_tampered_cookie_redirects_to_auth(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on invalid cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    h, p, s = token.split(".")
    bad = f"{h}.{p}.{s[:-2]}XY"

    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: bad},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith(policy_proxy_config.auth_base_url + "/auth/env-start")


def test_tampered_cookie_api_returns_401_with_auth_url(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on invalid cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    h, p, s = token.split(".")
    bad = f"{h}.{p}.{s[:-2]}XY"
    referer = "https://vmendi-hermes.humr-sandbox.humrsandbox.com/"

    response = client.get(
        "/api/session",
        cookies={jwt_verify.SESSION_COOKIE_NAME: bad},
        headers={
            "referer": referer,
            "sec-fetch-mode": "cors",
        },
        follow_redirects=False,
    )

    assert response.status_code == 401
    auth_url = response.headers[app_mod.AUTH_URL_HEADER]
    assert parse_qs(urlparse(auth_url).query)["rd"] == [referer]


def test_json_accept_without_fetch_metadata_returns_401(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/whatever",
        headers={"accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert app_mod.AUTH_URL_HEADER in response.headers


def test_navigation_fetch_metadata_redirects_even_for_api_path(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/api/sessions",
        headers={"sec-fetch-mode": "navigate"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith(policy_proxy_config.auth_base_url + "/auth/env-start")


def test_healthz_returns_ok_without_auth(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on health checks")

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get("/__policy_proxy/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


def test_healthz_tracks_upstream_readiness(policy_proxy_config, fake_jwks_client) -> None:
    """healthz is 503 until the upstream accepts connections, then 200 — even on error statuses."""
    upstream_up = False

    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on health checks")

    async def upstream(request: httpx.Request) -> httpx.Response:
        if not upstream_up:
            raise httpx.ConnectError("connection refused")
        # A 500 still proves the app is listening; healthz only reports connectivity.
        return httpx.Response(500)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)

    response = client.get("/__policy_proxy/healthz")
    assert response.status_code == 503

    upstream_up = True
    response = client.get("/__policy_proxy/healthz")
    assert response.status_code == 200


def test_upstream_connect_error_serves_starting_page_to_navigations(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
        headers={"sec-fetch-mode": "navigate"},
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["retry-after"] == "3"
    assert response.headers["cache-control"] == "no-store"
    assert 'http-equiv="refresh"' in response.text
    assert "Your agent is starting" in response.text


def test_upstream_connect_error_returns_plaintext_503_to_fetch(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/api/sessions",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
        headers={"sec-fetch-mode": "cors"},
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["retry-after"] == "3"


def test_upstream_non_connect_error_still_returns_502(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection reset mid-response")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
        headers={"sec-fetch-mode": "navigate"},
    )

    assert response.status_code == 502
    assert response.text == "upstream unreachable"


# -----------------------------------------------------------------------------
# PDP decision cache
# -----------------------------------------------------------------------------


def test_cache_enabled_reuses_first_decision(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    """Two requests from the same user hit PDP once when the cache is enabled."""
    cfg = replace(policy_proxy_config, pdp_cache_ttl_seconds=60)
    pdp_calls = 0

    async def pdp(request: httpx.Request) -> httpx.Response:
        nonlocal pdp_calls
        pdp_calls += 1
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(cfg, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    cookies = {jwt_verify.SESSION_COOKIE_NAME: token}

    for path in ["/", "/static/login.js", "/static/style.css", "/api/models"]:
        resp = client.get(path, cookies=cookies)
        assert resp.status_code == 200

    assert pdp_calls == 1


def test_cache_misses_across_different_users(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    """Two different identities each trigger their own PDP call even when cache is on."""
    cfg = replace(policy_proxy_config, pdp_cache_ttl_seconds=60)
    pdp_calls: list[str] = []

    async def pdp(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        assert body["provider"] == "oidc"
        pdp_calls.append(body["sub"])
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(cfg, fake_jwks_client, pdp, upstream)
    alice = jwt_minter(sub="okta|alice", username="alice")
    bob = jwt_minter(sub="okta|bob", username="bob")

    client.get("/", cookies={jwt_verify.SESSION_COOKIE_NAME: alice})
    client.get("/", cookies={jwt_verify.SESSION_COOKIE_NAME: alice})  # hit
    client.get("/", cookies={jwt_verify.SESSION_COOKIE_NAME: bob})
    client.get("/", cookies={jwt_verify.SESSION_COOKIE_NAME: bob})   # hit

    assert pdp_calls == ["okta|alice", "okta|bob"]


def test_streaming_sse_body_flows_through(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    """SSE-style upstream (no Content-Length) streams through and producer runs to completion."""
    chunks_produced: list[bytes] = []

    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        async def body():
            chunks_produced.append(b"a")
            yield b"data: first\n\n"
            chunks_produced.append(b"b")
            yield b"data: second\n\n"

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body(),
        )

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()

    with client.stream(
        "GET", "/events", cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream"
        # SSE responses must not carry Content-Length — they stream indefinitely.
        assert "content-length" not in response.headers
        body_bytes = b"".join(response.iter_raw())

    assert body_bytes == b"data: first\n\ndata: second\n\n"
    assert chunks_produced == [b"a", b"b"]


def test_fixed_length_response_preserves_content_length(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    """A static asset with Content-Length must pass that header through verbatim.

    Stripping it would force Starlette to re-frame as chunked, which breaks
    ALB → HTTP/2 translation for small fixed-length assets (CSS/JS).
    """
    css_bytes = b"body { color: red; }"

    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/css",
                "content-length": str(len(css_bytes)),
            },
            content=_streamed_body(css_bytes),
        )

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()

    response = client.get(
        "/static/style.css", cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/css"
    assert response.headers["content-length"] == str(len(css_bytes))
    assert response.content == css_bytes


def test_cache_disabled_when_ttl_zero(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    """Confirm the default (ttl=0) still calls PDP on every request — existing prod behavior."""
    pdp_calls = 0

    async def pdp(request: httpx.Request) -> httpx.Response:
        nonlocal pdp_calls
        pdp_calls += 1
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    cookies = {jwt_verify.SESSION_COOKIE_NAME: token}

    client.get("/", cookies=cookies)
    client.get("/static/login.js", cookies=cookies)
    client.get("/api/models", cookies=cookies)

    assert pdp_calls == 3
