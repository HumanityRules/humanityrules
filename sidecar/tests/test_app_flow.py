"""End-to-end tests for the FastAPI sidecar app, with PDP + upstream mocked."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from sidecar import app as app_mod
from sidecar import jwt_verify


def _build_app_with_mocks(
    cfg, jwks_cache, pdp_handler, upstream_handler,
):
    """Return a TestClient whose httpx.AsyncClient is replaced by MockTransport routing."""

    async def _router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cfg.pdp_url):
            return await pdp_handler(request)
        upstream_prefix = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
        if url.startswith(upstream_prefix):
            return await upstream_handler(request)
        raise AssertionError(f"unexpected outbound URL: {url}")

    transport = httpx.MockTransport(_router)
    fastapi_app = app_mod.create_app(cfg=cfg)
    # Replace the app-level client with one using our mock transport, and the
    # pre-primed JWKS cache so JWT verify doesn't hit the network.
    fastapi_app.state.http_client = httpx.AsyncClient(transport=transport)
    fastapi_app.state.jwks_cache = jwks_cache
    # We need the routes to read these updated state values, but our create_app
    # captures the original references at closure time. Rebuild the closures by
    # re-creating the app with config and manually swapping state after.
    # FastAPI closures read the outer variables, not app.state, so we need a
    # cleaner injection path — see _create_app_with_deps below.
    return fastapi_app


def _create_app_with_deps(cfg, jwks_cache, http_client):
    """Variant of create_app that lets tests inject the http client + cache."""
    # Monkey-patch: create_app builds its own client + cache. For the test we
    # construct a minimal app using the same handler logic but pointed at our
    # injected deps.
    from fastapi import FastAPI, Request
    from fastapi.responses import PlainTextResponse, RedirectResponse, Response
    from sidecar import pdp as pdp_mod
    from sidecar import proxy as proxy_mod

    app = FastAPI()
    upstream_base = f"http://{cfg.upstream_host}:{cfg.upstream_port}"

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def catch_all(request: Request, path: str) -> Response:
        cookie_value = request.cookies.get(jwt_verify.SESSION_COOKIE_NAME)
        if not cookie_value:
            # Use the same redirect helper as the real app.
            return app_mod._redirect_to_auth(request=request, cfg=cfg)
        identity = await jwt_verify.verify_session_cookie(
            jwt_value=cookie_value, jwks_cache=jwks_cache, http_client=http_client,
        )
        if identity is None:
            return app_mod._redirect_to_auth(request=request, cfg=cfg)
        decision = await pdp_mod.evaluate(
            http_client=http_client, pdp_url=cfg.pdp_url,
            sidecar_token=cfg.sidecar_token, app_id=cfg.app_id,
            oidc_sub=identity.oidc_sub, username=identity.username,
            path=request.url.path,
        )
        if decision is None:
            return PlainTextResponse(content="authz unavailable", status_code=503)
        if decision.decision != "allow":
            return PlainTextResponse(content="denied", status_code=403)
        return await proxy_mod.proxy_to_upstream(
            request=request, identity=identity,
            upstream_base=upstream_base, http_client=http_client,
        )

    return app


def _mk_client(cfg, jwks_cache, pdp_handler, upstream_handler) -> TestClient:
    async def _router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cfg.pdp_url):
            return await pdp_handler(request)
        upstream_prefix = f"http://{cfg.upstream_host}:{cfg.upstream_port}"
        if url.startswith(upstream_prefix):
            return await upstream_handler(request)
        raise AssertionError(f"unexpected outbound URL: {url}")

    transport = httpx.MockTransport(_router)
    http_client = httpx.AsyncClient(transport=transport)
    fastapi_app = _create_app_with_deps(
        cfg=cfg, jwks_cache=jwks_cache, http_client=http_client,
    )
    return TestClient(fastapi_app)


# --- test cases ---


def test_missing_cookie_redirects_to_auth(sidecar_config, primed_jwks_cache) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called without a cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(sidecar_config, primed_jwks_cache, pdp, upstream)
    response = client.get("/chat/new", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.ch-sandbox.chsandbox.com"
    assert parsed.path == "/start"
    rd = parse_qs(parsed.query)["rd"][0]
    assert rd.endswith("/chat/new")


def test_allow_proxies_to_upstream(sidecar_config, primed_jwks_cache, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        # Validate the sidecar forwarded the identity headers.
        assert request.headers["x-auth-user"] == "vmendi"
        assert request.headers["x-auth-sub"] == "okta|vmendi"
        return httpx.Response(200, text="hello from upstream")

    client = _mk_client(sidecar_config, primed_jwks_cache, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 200
    assert response.text == "hello from upstream"


def test_deny_returns_403(sidecar_config, primed_jwks_cache, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-match"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called on deny")

    client = _mk_client(sidecar_config, primed_jwks_cache, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 403


def test_pdp_unreachable_returns_503(sidecar_config, primed_jwks_cache, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called when PDP is down")

    client = _mk_client(sidecar_config, primed_jwks_cache, pdp, upstream)
    token = jwt_minter()
    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: token},
    )
    assert response.status_code == 503


def test_tampered_cookie_redirects_to_auth(sidecar_config, primed_jwks_cache, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP should not be called on invalid cookie")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(sidecar_config, primed_jwks_cache, pdp, upstream)
    token = jwt_minter()
    # Corrupt the signature.
    h, p, s = token.split(".")
    bad = f"{h}.{p}.{s[:-2]}XY"

    response = client.get(
        "/chat/new",
        cookies={jwt_verify.SESSION_COOKIE_NAME: bad},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].startswith(sidecar_config.auth_base_url + "/start")


def test_validate_rd_accepts_same_domain_and_subdomain() -> None:
    from sidecar.app import validate_rd

    env = "ch-sandbox.chsandbox.com"
    assert validate_rd("https://ch-sandbox.chsandbox.com/", env)
    assert validate_rd("https://vmendi-hermes.ch-sandbox.chsandbox.com/chat", env)
    assert not validate_rd("https://attacker.com/", env)
    assert not validate_rd("http://ch-sandbox.chsandbox.com/", env)  # non-https
    assert not validate_rd("https://evil.ch-sandbox.chsandbox.com.attacker.com/", env)
