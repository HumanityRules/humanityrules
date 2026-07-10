"""WebSocket end-to-end tests for the policy-proxy app.

Auth/PDP rejection paths use a stub upstream (we only assert the close code).
The happy path stands up a real `websockets.serve` server on a loopback port
so we exercise subprotocol negotiation, header propagation, and bidirectional
frame pumping in the actual `proxy_to_upstream_ws` code path.
"""

import asyncio
import socket
import threading
from collections.abc import Awaitable, Callable
from contextlib import contextmanager

import httpx
import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

from policy_proxy import app as app_mod
from policy_proxy import config as config_mod
from policy_proxy import jwt_verify


def _mk_app(
    cfg: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    pdp_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> FastAPI:
    async def _router(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(cfg.pdp_url):
            return await pdp_handler(request)
        if url.startswith(f"{cfg.control_plane_url}/api/runtime/policy-proxy-activity"):
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected outbound URL: {url}")

    fastapi_app = app_mod.create_app(cfg=cfg)
    fastapi_app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_router))
    fastapi_app.state.jwks_client = fake_jwks_client
    return fastapi_app


def _mk_client(
    cfg: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    pdp_handler: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> TestClient:
    fastapi_app = _mk_app(
        cfg=cfg, fake_jwks_client=fake_jwks_client, pdp_handler=pdp_handler,
    )
    return TestClient(fastapi_app)


def _free_port() -> int:
    """Pick an OS-assigned free port. There's a race with the bind that follows
    but it's tight enough that retries aren't worth the complexity in tests."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _start_uvicorn(fastapi_app: FastAPI, port: int) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    """Start a real ASGI server for browser-visible WebSocket handshake checks."""
    server = uvicorn.Server(
        uvicorn.Config(
            app=fastapi_app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="error",
        ),
    )
    task = asyncio.create_task(server.serve())
    deadline = asyncio.get_running_loop().time() + 5
    while not server.started:
        if task.done():
            task.result()
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("uvicorn server did not start")
        await asyncio.sleep(0.05)
    return server, task


@contextmanager
def _upstream_ws_server(handler, port: int, **serve_kwargs):
    """Run `websockets.serve(handler)` on `port` on a private event loop in a
    background thread. Yields once the server is accepting connections."""
    ready = threading.Event()
    stop = threading.Event()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    def _run():
        loop = asyncio.new_event_loop()
        loop_holder["loop"] = loop
        asyncio.set_event_loop(loop)

        async def _serve_forever():
            async with ws_serve(
                handler, host="127.0.0.1", port=port, **serve_kwargs,
            ):
                ready.set()
                while not stop.is_set():
                    await asyncio.sleep(0.05)

        loop.run_until_complete(_serve_forever())
        loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    if not ready.wait(timeout=5):
        raise RuntimeError("upstream ws server did not start")
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


# --- rejection paths (no upstream needed) ---


def test_ws_missing_cookie_closes_with_4401(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP must not be called without a cookie")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/webapps/terminal/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == app_mod.WS_CLOSE_AUTH_REQUIRED


def test_ws_tampered_cookie_closes_with_4401(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP must not be called on bad cookie")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp)
    token = jwt_minter()
    h, p, s = token.split(".")
    bad = f"{h}.{p}.{s[:-2]}XY"

    client.cookies.set(jwt_verify.SESSION_COOKIE_NAME, bad)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/webapps/terminal/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == app_mod.WS_CLOSE_AUTH_REQUIRED


def test_ws_pdp_deny_closes_with_4403(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "deny", "reason": "no-match"})

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp)
    client.cookies.set(jwt_verify.SESSION_COOKIE_NAME, jwt_minter())
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/webapps/terminal/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == app_mod.WS_CLOSE_FORBIDDEN


def test_ws_pdp_unreachable_closes_with_1011(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp)
    client.cookies.set(jwt_verify.SESSION_COOKIE_NAME, jwt_minter())
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/webapps/terminal/ws") as ws:
            ws.receive_text()
    assert exc_info.value.code == app_mod.WS_CLOSE_SERVICE_UNAVAILABLE


def test_ws_internal_path_rejected_with_4403(policy_proxy_config, fake_jwks_client) -> None:
    async def pdp(request: httpx.Request) -> httpx.Response:
        raise AssertionError("PDP must not be called on internal path")

    client = _mk_client(policy_proxy_config, fake_jwks_client, pdp)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/__policy_proxy/sneaky") as ws:
            ws.receive_text()
    assert exc_info.value.code == app_mod.WS_CLOSE_FORBIDDEN


# --- happy path: real upstream ws server ---


def test_ws_allow_proxies_bidirectional(
    policy_proxy_config, fake_jwks_client, jwt_minter,
) -> None:
    """End-to-end: subprotocol negotiated, frames flow both ways, identity
    headers reach the upstream handshake."""
    port = _free_port()
    cfg = type(policy_proxy_config)(
        **{**policy_proxy_config.__dict__, "upstream_port": port},
    )
    seen_headers: dict[str, str] = {}

    async def upstream_handler(connection):
        # Snapshot the handshake the policy proxy sent us — captured here so
        # the assertion below can verify identity headers + subprotocol
        # negotiation actually traversed the proxy.
        for k, v in connection.request.headers.raw_items():
            seen_headers[k.lower()] = v
        seen_headers["__subprotocol__"] = connection.subprotocol or ""

        first = await connection.recv()
        await connection.send(f"echo:{first}")
        await connection.send(b"\x00\x01\x02binary")
        # Wait for client-initiated close instead of closing eagerly — keeping
        # the connection alive lets the test read both frames before teardown.
        try:
            await connection.recv()
        except websockets.ConnectionClosed:
            pass

    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    client = _mk_client(cfg, fake_jwks_client, pdp)
    client.cookies.set(jwt_verify.SESSION_COOKIE_NAME, jwt_minter())

    with _upstream_ws_server(upstream_handler, port=port, subprotocols=["tty"]):
        with client.websocket_connect(
            "/webapps/terminal/ws", subprotocols=["tty"],
        ) as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "echo:hello"
            assert ws.receive_bytes() == b"\x00\x01\x02binary"

    assert seen_headers["x-auth-user"] == "vmendi"
    assert seen_headers["x-auth-sub"] == "okta|vmendi"
    assert seen_headers["x-auth-email"] == "vmendi@example.com"
    assert seen_headers["__subprotocol__"] == "tty"
    # The session cookie is for the policy proxy and must not leak to apps.
    assert "cookie" not in seen_headers


async def test_ws_upstream_unreachable_closes_with_1011(
    policy_proxy_config: config_mod.PolicyProxyConfig,
    fake_jwks_client: object,
    jwt_minter: Callable[[], str],
) -> None:
    """Allow + dead upstream port = browser sees a 1011 close code."""
    cfg = type(policy_proxy_config)(
        **{**policy_proxy_config.__dict__, "upstream_port": _free_port()},
    )

    async def pdp(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"decision": "allow", "reason": "ok"})

    fastapi_app = _mk_app(cfg=cfg, fake_jwks_client=fake_jwks_client, pdp_handler=pdp)
    proxy_port = _free_port()
    server, task = await _start_uvicorn(fastapi_app=fastapi_app, port=proxy_port)

    try:
        async with ws_connect(
            f"ws://127.0.0.1:{proxy_port}/webapps/terminal/ws",
            additional_headers={
                "Cookie": f"{jwt_verify.SESSION_COOKIE_NAME}={jwt_minter()}",
            },
        ) as ws:
            with pytest.raises(websockets.ConnectionClosed) as exc_info:
                await ws.recv()
        assert exc_info.value.rcvd is not None
        assert exc_info.value.rcvd.code == app_mod.WS_CLOSE_SERVICE_UNAVAILABLE
    finally:
        server.should_exit = True
        await task
