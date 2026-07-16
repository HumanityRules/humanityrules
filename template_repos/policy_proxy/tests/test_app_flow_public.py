"""Public-webapp path: anonymous access via WebappPublicGrant decisions, header hygiene, caching, body cap."""

from dataclasses import replace
from unittest import mock

import httpx

from policy_proxy import app as app_mod
from policy_proxy import jwt_verify
from tests.test_app_flow import _mk_client, _streamed_body

PUBLIC_HOST = "dashboard.vmendi-hermes.humr-sandbox.humrsandbox.com"


def _pdp_public_allow_else_fail(request: httpx.Request) -> httpx.Response:
    assert str(request.url).endswith("-public"), "only the anonymous PDP endpoint may be called"
    return httpx.Response(200, json={"decision": "allow", "reason": "public-webapp"})


def test_webapp_slug_for_host_parses_and_normalizes() -> None:
    public = "agent.example.com"
    assert app_mod._webapp_slug_for_host(host="dash.agent.example.com", public_hostname=public) == "dash"
    assert app_mod._webapp_slug_for_host(host="DASH.Agent.Example.Com:443", public_hostname=public) == "dash"
    assert app_mod._webapp_slug_for_host(host="dash.agent.example.com.", public_hostname=public) == "dash"
    # Bare host, wrong domain, nested label, internal-style and invalid labels are not webapp slugs.
    assert app_mod._webapp_slug_for_host(host="agent.example.com", public_hostname=public) is None
    assert app_mod._webapp_slug_for_host(host="dash.other.example.com", public_hostname=public) is None
    assert app_mod._webapp_slug_for_host(host="a.b.agent.example.com", public_hostname=public) is None
    assert app_mod._webapp_slug_for_host(host="__admin.agent.example.com", public_hostname=public) is None
    assert app_mod._webapp_slug_for_host(host="-x.agent.example.com", public_hostname=public) is None
    assert app_mod._webapp_slug_for_host(host="dash.agent.example.com", public_hostname=None) is None


def test_public_grant_allows_anonymous_and_strips_spoofed_identity(policy_proxy_config, fake_jwks_client) -> None:
    seen_payloads = []

    async def pdp(request: httpx.Request) -> httpx.Response:
        import json
        seen_payloads.append((str(request.url), json.loads(request.content)))
        return _pdp_public_allow_else_fail(request)

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert "x-auth-user" not in request.headers
        assert "x-auth-sub" not in request.headers
        assert "x-auth-provider" not in request.headers
        assert "x-auth-email" not in request.headers
        assert jwt_verify.SESSION_COOKIE_NAME not in request.headers.get("cookie", "")
        assert request.headers["x-forwarded-host"] == PUBLIC_HOST
        return httpx.Response(200, content=_streamed_body(b"public content"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/",
        headers={
            "host": PUBLIC_HOST,
            "X-Auth-User": "victor",
            "X-Auth-Email": "spoof@example.com",
            "cookie": f"{jwt_verify.SESSION_COOKIE_NAME}=stale-garbage; app_pref=dark",
        },
    )
    assert response.status_code == 200
    assert response.text == "public content"
    url, payload = seen_payloads[0]
    assert payload == {"app_id": "vmendi-hermes", "webapp_slug": "dashboard", "path": "/"}


def test_public_request_cannot_forge_underscore_identity_alias(
    policy_proxy_config, fake_jwks_client,
) -> None:
    """X_Auth_User is a WSGI alias of X-Auth-User (both fold to HTTP_X_AUTH_USER),
    so the underscore form must be stripped just like the hyphenated one."""
    seen: dict[str, list[str]] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen["names"] = [k.decode().lower() for k, _ in request.headers.raw]
        return httpx.Response(200, content=_streamed_body(b"public content"))

    async def pdp(request: httpx.Request) -> httpx.Response:
        return _pdp_public_allow_else_fail(request)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/",
        headers={
            "host": PUBLIC_HOST,
            "X_Auth_User": "victor",
            "X_Auth_Email": "spoof@example.com",
        },
    )
    assert response.status_code == 200
    assert "x_auth_user" not in seen["names"]
    assert "x-auth-user" not in seen["names"]
    assert "x_auth_email" not in seen["names"]
    assert "x-auth-email" not in seen["names"]


def test_public_request_cannot_forge_forwarded_host(policy_proxy_config, fake_jwks_client) -> None:
    """A forged X-Forwarded-Host must not ride along with ours: Caddy routes on that
    header and matches on any value, so a second one reaches the bare agent host."""
    seen: dict[str, list[str]] = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen["xfh"] = request.headers.get_list("x-forwarded-host")
        return httpx.Response(200, content=_streamed_body(b"public content"))

    async def pdp(request: httpx.Request) -> httpx.Response:
        return _pdp_public_allow_else_fail(request)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/",
        headers={
            "host": PUBLIC_HOST,
            "X-Forwarded-Host": policy_proxy_config.public_hostname,
        },
    )
    assert response.status_code == 200
    assert seen["xfh"] == [PUBLIC_HOST]


def test_not_public_webapp_host_falls_through_to_auth(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("-public")
        return httpx.Response(200, json={"decision": "deny", "reason": "not-public"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get("/", headers={"host": PUBLIC_HOST}, follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/env-start" in response.headers["location"]


def test_authenticated_user_reaches_private_webapp(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("-public"):
            return httpx.Response(200, json={"decision": "deny", "reason": "not-public"})
        return httpx.Response(200, json={"decision": "allow", "reason": "app:use"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-auth-user"] == "vmendi"
        return httpx.Response(200, content=_streamed_body(b"private webapp"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get(
        "/",
        headers={"host": PUBLIC_HOST},
        cookies={jwt_verify.SESSION_COOKIE_NAME: jwt_minter()},
    )
    assert response.status_code == 200
    assert response.text == "private webapp"


def test_bare_host_skips_public_pdp(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no PDP call expected for a cookieless bare-host request")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get("/", headers={"host": policy_proxy_config.public_hostname}, follow_redirects=False)
    assert response.status_code == 302


def test_public_decision_is_cached_per_slug(policy_proxy_config, fake_jwks_client) -> None:
    cfg = replace(policy_proxy_config, public_cache_ttl_seconds=60)
    pdp_calls = []

    async def pdp(request: httpx.Request) -> httpx.Response:
        pdp_calls.append(str(request.url))
        return _pdp_public_allow_else_fail(request)

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(cfg, fake_jwks_client, pdp, upstream)
    for _ in range(3):
        assert client.get("/", headers={"host": PUBLIC_HOST}).status_code == 200
    assert len(pdp_calls) == 1


def test_pdp_down_on_public_host_fails_closed(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("pdp unreachable")

    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream should not be called")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.get("/", headers={"host": PUBLIC_HOST})
    assert response.status_code == 503


def test_public_body_over_cap_returns_413(policy_proxy_config, fake_jwks_client) -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("capped body must not reach upstream")

    async def pdp(request: httpx.Request) -> httpx.Response:
        return _pdp_public_allow_else_fail(request)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    with mock.patch.object(app_mod, "PUBLIC_MAX_BODY_BYTES", 16):
        response = client.post("/", headers={"host": PUBLIC_HOST}, content=b"x" * 17)
    assert response.status_code == 413


def test_public_chunked_body_returns_411(policy_proxy_config, fake_jwks_client) -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        raise AssertionError("chunked public body must not reach upstream")

    async def pdp(request: httpx.Request) -> httpx.Response:
        return _pdp_public_allow_else_fail(request)

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    response = client.post(
        "/",
        headers={"host": PUBLIC_HOST, "transfer-encoding": "chunked"},
        content=iter([b"chunk"]),
    )
    assert response.status_code == 411


def test_authenticated_body_is_uncapped(policy_proxy_config, fake_jwks_client, jwt_minter) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "app:use"})

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streamed_body(b"ok"))

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp, upstream)
    with mock.patch.object(app_mod, "PUBLIC_MAX_BODY_BYTES", 16):
        response = client.post(
            "/upload",
            cookies={jwt_verify.SESSION_COOKIE_NAME: jwt_minter()},
            content=b"x" * 64,
        )
    assert response.status_code == 200
