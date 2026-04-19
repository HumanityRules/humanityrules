"""End-to-end tests for the FastAPI sidecar app, with PDP + upstream mocked."""

from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from sidecar import app as app_mod
from sidecar import jwt_verify


def _mk_client(cfg, fake_jwks_client, pdp_handler, upstream_handler) -> TestClient:
    """Build a real create_app() + swap http_client and jwks_client on app.state."""

    async def _router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cfg.pdp_url):
            return await pdp_handler(request)
        upstream_prefix = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
        if url.startswith(upstream_prefix):
            return await upstream_handler(request)
        raise AssertionError(f"unexpected outbound URL: {url}")

    fastapi_app = app_mod.create_app(cfg=cfg)
    fastapi_app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_router))
    fastapi_app.state.jwks_client = fake_jwks_client
    return TestClient(fastapi_app)


# --- test cases ---


def test_missing_cookie_redirects_to_auth(sidecar_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    response = client.get("/chat/new", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.ch-sandbox.chsandbox.com"
    assert parsed.path == "/start"
    rd = parse_qs(parsed.query)["rd"][0]
    assert rd.endswith("/chat/new")


def test_allow_proxies_to_upstream(sidecar_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        # Validate the sidecar forwarded the identity headers.
        assert request.headers["x-auth-user"] == "vmendi"
        assert request.headers["x-auth-sub"] == "okta|vmendi"
        return httpx.Response(200, text="hello from upstream")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 200
    assert response.text == "hello from upstream"


def test_deny_returns_403(sidecar_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-match"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called on deny")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 403


def test_pdp_unreachable_returns_503(sidecar_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called when PDP is down")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 503


def test_tampered_cookie_redirects_to_auth(sidecar_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on invalid cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    token = jwt_minter()
    h, p, s = token.split(".")
    bad = f"{h}.{p}.{s[:-2]}XY"

    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: bad},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith(sidecar_config.auth_base_url + "/start")


def test_healthz_returns_ok_without_auth(sidecar_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on health checks")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called on health checks")

    client = _mk_client(sidecar_config, fake_jwks_client, pdp, upstream)
    response = client.get("/__sidecar/healthz")
    assert response.status_code == 200
    assert response.text == "ok"
