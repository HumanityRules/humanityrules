"""Outside-the-sandbox broker for per-user third-party integrations.

Runs as a supervisor-managed sidecar process. It starts:

1. The TLS-intercept proxy on 127.0.0.1:9950. The nono sandbox gets
   HTTPS_PROXY pointed here and SSL_CERT_FILE pointed at the CA bundle created
   by `tls_intercept.TlsInterceptRuntime`.

2. The integrations control API on 127.0.0.1:9951, reached same-origin by the
   WebUI extension via Caddy's /__doh_broker/* route. This API owns
   the unified browser-facing integration status surface and mounts the
   MCP-aggregator management routes under /integrations.

The aggregator's port 9952 is sandbox-only MCP traffic. Refresh tokens, DOH's
OAuth client secrets, and the env bearer never enter the sandbox.

Environment contract (set by deploy_app.py's env-bearer overlay):
- DOH_ENV_BEARER        — bearer for DOH's per-env integration endpoints.
- DOH_OWNER_USERNAME    — whose grants this container is for.
- DOH_APP_SLUG          — logical app key for app-scoped credentials.
- DOH_CONTROL_PLANE_URL — base URL for DOH (e.g. https://devopshero.ai).
- DOH_ENV_SLUG          — env slug, for logging only.
- DOH_MERGE_INTEGRATION_ENABLED — optional; false disables all Merge.dev connectors.

Required file system:
- BROKER_CA_DIR must be writable by the broker user. The CA bundle is written
  here on startup for SSL_CERT_FILE to pick up.
- BROKER_PRIVATE_DIR must be writable by the broker user and unreadable by the
  sandbox.
"""

import argparse
import asyncio
import contextlib
from collections.abc import Awaitable, Callable
import json
import logging
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import device_flow
import mcp_aggregator
import tls_intercept


DEFAULT_PROXY_PORT = 9950
DEFAULT_CONTROL_PORT = 9951
DEFAULT_MCP_PORT = 9952
DEFAULT_CA_DIR = Path("/run/doh/integrations-broker/ca")
DEFAULT_PRIVATE_DIR = Path("/run/doh/integrations-broker/private")
DEFAULT_MCP_PERSISTENT_DIR = Path("/hermes-persistent-root/mcp-aggregator")
DEFAULT_GATEWAY_ENV_PATH = Path("/workspace/.hermes/.env")
DEFAULT_WEBUI_STATE_DIR = Path("/workspace/.hermes/webui-mvp")
DEFAULT_PROCESS_COMPOSE_URL = "http://127.0.0.1:9956"
GATEWAY_PROCESS_NAME = "system.gateway"
WEBUI_PROCESS_NAME = "system.webui"
# The in-sandbox gateway user; auth.json must stay owned by it (mode 0600), so
# the root broker drops to it via runuser when it touches the local auth store.
GATEWAY_USER = "hermeswebui"
AUTH_MARKER_PROVIDERS = frozenset({"openai-codex", "nous"})
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})

logger = logging.getLogger("integrations_broker")


async def _handle_unified_status(
    request: Request,
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    control_plane_url: str,
    owner_username: str,
    app_slug: str,
    env_slug: str,
) -> Response:
    """Flat list combining TLS-intercept providers and MCP-aggregator items.

    Reads cached TLS-intercept entries; refresh happens lazily (proxy hot
    path or near expiry). Callers that need fresh state must POST
    /__doh_broker/integrations/{provider}/invalidate_tls_cache for one provider
    (e.g. after a Disconnect on DOH) or /__doh_broker/integrations/refresh
    for the explicit-Refresh path (MCP catalog reload + all-providers TLS
    invalidate in one shot).
    """
    items = await tls_runtime.status_items()
    items.extend(await aggregator.status_items())
    return JSONResponse(content={
        "doh_control_plane_url": control_plane_url,
        "env_slug": env_slug,
        "owner_username": owner_username,
        "app_slug": app_slug,
        "items": items,
    })


async def _handle_healthz(request: Request) -> Response:
    return JSONResponse(content={"ok": True})


def _build_control_app(
    aggregator: mcp_aggregator.MCPAggregator,
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    oauth_device_flow: device_flow.OAuthDeviceFlow,
    control_plane_url: str,
    bearer: str,
    owner_username: str,
    app_slug: str,
    env_slug: str,
) -> Starlette:
    """Wire the unified /__doh_broker/* router for browser-facing integration management."""
    async def status_route(request: Request) -> Response:
        return await _handle_unified_status(
            request=request,
            aggregator=aggregator,
            tls_runtime=tls_runtime,
            control_plane_url=control_plane_url,
            owner_username=owner_username,
            app_slug=app_slug,
            env_slug=env_slug,
        )

    async def refresh_route(request: Request) -> Response:
        """Explicit-Refresh: reload the MCP catalog and drop the all-providers TLS cache.

        Cooldown is owned by the catalog side. If a refresh ran within
        REFRESH_COOLDOWN_SECONDS we return 429 *without* firing the TLS
        invalidate — otherwise smashing the Refresh button on cooldown would
        repeatedly trigger `invalidate_all`'s `on_user_invalidate` hook
        (DOH round-trip + gateway-env rewrite + gateway restart for vault
        providers). Once past the cooldown gate, the two sides run
        concurrently — they share no state and the slower of the two sets
        the round-trip latency.
        """
        remaining = aggregator.cooldown_remaining_seconds()
        if remaining is not None:
            return JSONResponse(
                content={"error": "refresh_cooldown", "retry_after_seconds": remaining},
                status_code=429,
            )

        async def _safe_invalidate_all() -> str | None:
            try:
                await tls_runtime.invalidate_all()
            except RuntimeError as exc:
                return str(exc)
            return None

        catalog_result, tls_error = await asyncio.gather(
            aggregator.refresh_catalog(),
            _safe_invalidate_all(),
        )
        if tls_error is not None:
            return JSONResponse(content={"ok": False, "error": tls_error}, status_code=502)
        ok, payload = catalog_result
        return JSONResponse(content=payload, status_code=200 if ok else 429)

    async def invalidate_provider_tls_cache_route(request: Request) -> Response:
        """Drop one provider's cached TLS-intercept token after known state changes."""
        provider = request.path_params["provider"]
        try:
            await tls_runtime.invalidate(slug=provider)
        except RuntimeError as exc:
            return JSONResponse(
                content={"ok": False, "provider": provider, "error": str(exc)},
                status_code=502,
            )
        return JSONResponse(content={"ok": True, "provider": provider})

    async def vault_setup_session_route(request: Request) -> Response:
        provider = request.path_params["provider"]
        public_origin = request.query_params.get("origin", "")
        status, payload = await asyncio.to_thread(
            _post_control_plane_json,
            control_plane_url=control_plane_url,
            bearer=bearer,
            path="/api/integrations/credentials/setup-session",
            payload={
                "owner_username": owner_username,
                "app_slug": app_slug,
                "provider": provider,
                "public_origin": public_origin,
            },
        )
        return JSONResponse(content=payload, status_code=status)

    async def disconnect_route(request: Request) -> Response:
        """Disconnect any TLS-intercept provider (vault or OAuth).

        DOH's unified disconnect handler deletes the credential row and, for
        OAuth providers, best-effort revokes upstream — the provider kind is
        resolved server-side, so the broker forwards both kinds identically.
        On success we drop only this provider's cached token. Any process
        restart is selected from the provider spec inside `invalidate`, not off
        this route.
        """
        provider = request.path_params["provider"]
        status, payload = await asyncio.to_thread(
            _post_control_plane_json,
            control_plane_url=control_plane_url,
            bearer=bearer,
            path="/api/integrations/credentials/disconnect",
            payload={
                "owner_username": owner_username,
                "app_slug": app_slug,
                "provider": provider,
            },
        )
        if 200 <= status < 300:
            try:
                await tls_runtime.invalidate(slug=provider)
            except RuntimeError as exc:
                return JSONResponse(
                    content={**payload, "ok": False, "error": str(exc)},
                    status_code=502,
                )
            if provider in AUTH_MARKER_PROVIDERS:
                await asyncio.to_thread(_run_provider_auth_marker, provider, "disconnect")
        return JSONResponse(content=payload, status_code=status)

    async def device_start_route(request: Request) -> Response:
        """Begin a provider device login; returns the user_code to display immediately."""
        provider = request.path_params["provider"]
        try:
            view = await oauth_device_flow.start(provider_slug=provider)
        except device_flow.DeviceFlowError as exc:
            return JSONResponse(content={"ok": False, "error": str(exc)}, status_code=502)
        return JSONResponse(content={"ok": True, **view})

    async def device_status_route(request: Request) -> Response:
        """Report the in-flight device-login phase (pending/completed/failed)."""
        provider = request.path_params["provider"]
        view = await oauth_device_flow.status(provider_slug=provider)
        if view is None:
            return JSONResponse(content={"ok": True, "phase": None})
        return JSONResponse(content={"ok": True, **view})

    async def device_cancel_route(request: Request) -> Response:
        """Cancel an in-flight provider device login (user closed the dialog)."""
        provider = request.path_params["provider"]
        await oauth_device_flow.cancel(provider_slug=provider)
        return JSONResponse(content={"ok": True})

    routes = [
        Route(path="/healthz", endpoint=_handle_healthz, methods=["GET"]),
        Route(path="/integrations", endpoint=status_route, methods=["GET"]),
        Route(path="/integrations/refresh", endpoint=refresh_route, methods=["POST"]),
        Route(path="/integrations/{provider}/invalidate_tls_cache", endpoint=invalidate_provider_tls_cache_route, methods=["POST"]),
        Route(path="/integrations/{provider}/vault/setup-session", endpoint=vault_setup_session_route, methods=["POST"]),
        # Device login: broker-run OAuth device flows (no redirect callback).
        Route(path="/integrations/{provider}/device/start", endpoint=device_start_route, methods=["POST"]),
        Route(path="/integrations/{provider}/device/status", endpoint=device_status_route, methods=["GET"]),
        Route(path="/integrations/{provider}/device/cancel", endpoint=device_cancel_route, methods=["POST"]),
        # One disconnect path for every TLS-intercept provider (vault + OAuth).
        # Sits under the /tls/ sub-prefix so it doesn't collide with the MCP
        # aggregator's own /integrations/{provider}/disconnect (Notion/Merge).
        Route(path="/integrations/{provider}/tls/disconnect", endpoint=disconnect_route, methods=["POST"]),
        *aggregator.routes(prefix="/integrations"),
    ]
    return Starlette(routes=routes)


def _post_control_plane_json(control_plane_url: str, bearer: str, path: str, payload: dict) -> tuple[int, dict]:
    """POST JSON to DOH from the outside-sandbox broker."""
    url = f"{control_plane_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"error": f"control plane returned HTTP {exc.code}"}
        return exc.code, body
    except Exception as exc:
        logger.error("control plane request failed path=%s: %s", path, exc)
        return 502, {"error": "control plane request failed"}


def _post_process_compose_restart(process_compose_url: str, process_name: str) -> tuple[int, str]:
    """Tell process-compose's REST API to restart one entry."""
    url = f"{process_compose_url.rstrip('/')}/process/restart/{process_name}"
    req = urllib.request.Request(url=url, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return exc.code, body
    except Exception as exc:
        logger.error("process-compose restart failed for %s: %s", process_name, exc)
        return 502, str(exc)


def _run_provider_auth_marker(provider: str, action: str) -> bool:
    """Add/remove a local provider auth marker, as the gateway user.

    `action` is "connect" or "disconnect". The marker writes/clears the
    placeholder provider block in auth.json via the agent's locked, atomic
    primitives, so the WebUI model picker shows/hides model providers without a
    restart. We run it through `runuser` because the broker is root and
    auth.json must stay owned by the sandbox user (mode 0600); a root-owned
    store or lock file would lock the gateway out. Best-effort: a failure here
    only means the dropdown is briefly stale, not that the credential is wrong.
    """
    python = os.environ.get("HERMES_WEBUI_PYTHON")
    runtime_dir = os.environ.get("DOH_RUNTIME_DIR")
    hermes_home = os.environ.get("HERMES_HOME")
    if not python or not runtime_dir or not hermes_home:
        logger.error("provider auth marker skipped: HERMES_WEBUI_PYTHON/DOH_RUNTIME_DIR/HERMES_HOME not set")
        return False
    script = str(Path(runtime_dir) / "provider_auth_marker.py")
    try:
        result = subprocess.run(
            ["runuser", "-u", GATEWAY_USER, "--", python, script, action, provider],
            env={**os.environ, "HERMES_HOME": hermes_home},
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        logger.error("provider auth marker (%s %s) failed to run: %s", action, provider, exc)
        return False
    if result.returncode != 0:
        logger.error("provider auth marker (%s %s) exited %d: %s", action, provider, result.returncode, result.stderr.strip())
        return False
    logger.info("provider auth marker (%s %s): %s", action, provider, result.stdout.strip())
    return True


async def _render_gateway_env_file(tls_runtime: tls_intercept.TlsInterceptRuntime, env_path: Path) -> bool:
    """Write the DOH-managed profile env block from current cache state."""
    snapshot = await tls_runtime.gateway_env_snapshot()
    block = tls_intercept.render_managed_block(snapshot=snapshot)
    changed = await asyncio.to_thread(tls_intercept.write_gateway_env_file, env_path=env_path, managed_block=block)
    if changed:
        logger.info("rewrote managed env at %s (%d bytes)", env_path, len(block))
    else:
        logger.info("managed env unchanged at %s", env_path)
    return changed


# Keeps strong references to fire-and-forget asyncio tasks so they aren't GC'd mid-flight.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _bootstrap_gateway_env(
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    env_path: Path,
    webui_state_dir: Path,
    process_compose_url: str,
) -> None:
    """At broker startup: refresh every provider, then render env file once.

    When the bootstrap refresh fails transiently (DOH unreachable, e.g. a 503
    mid-deploy), the cache is empty but the env file on disk may still hold a
    good block from the previous broker run — rendering now would strip every
    integration from the gateway until the next connect/refresh. Instead the
    existing file is left untouched and a background task retries until DOH
    answers, then renders and restarts whatever the new block requires.
    """
    if await tls_runtime.refresh_all():
        await _render_gateway_env_file(tls_runtime=tls_runtime, env_path=env_path)
        return
    logger.error("bootstrap refresh failed transiently; keeping existing managed env, retrying in background")
    task = asyncio.create_task(_retry_bootstrap_gateway_env(
        tls_runtime=tls_runtime,
        env_path=env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url=process_compose_url,
    ))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _retry_bootstrap_gateway_env(
    tls_runtime: tls_intercept.TlsInterceptRuntime,
    env_path: Path,
    webui_state_dir: Path,
    process_compose_url: str,
) -> None:
    """Retry the bootstrap refresh until DOH answers, then render env and restart affected processes."""
    delay_seconds = 5
    while True:
        await asyncio.sleep(delay_seconds)
        delay_seconds = min(delay_seconds * 2, 60)
        if not await tls_runtime.refresh_all():
            continue
        logger.info("bootstrap refresh recovered; rendering managed env")
        try:
            env_changed = await _render_gateway_env_file(tls_runtime=tls_runtime, env_path=env_path)
            if _slug_affects_model_picker(slug=None, runtime=tls_runtime):
                await asyncio.to_thread(_delete_webui_models_cache, webui_state_dir=webui_state_dir)
            if env_changed:
                await _restart_processes_for_env_change(runtime=tls_runtime, process_compose_url=process_compose_url, slug=None)
        except Exception as exc:
            logger.error("bootstrap recovery render/restart failed: %s; a manual Refresh may be needed", exc)
        return


def _build_on_user_invalidate(
    tls_runtime_holder: dict[str, tls_intercept.TlsInterceptRuntime],
    env_path: Path,
    webui_state_dir: Path,
    process_compose_url: str,
) -> Callable[[str | None], Awaitable[None]]:
    """Return the hook that re-renders env and restarts the gateway when needed.

    Uses a holder dict because the runtime is constructed *with* this hook,
    creating a chicken-and-egg. The broker fills `tls_runtime_holder["runtime"]`
    immediately after construction.

    When `slug` is set (a single provider was just connected/disconnected),
    only that provider is re-fetched from DOH — refreshing every disconnected
    provider on each connect would spam DOH with `no integration row` 404s.
    `slug=None` (explicit Refresh-all) does fan out to every provider.
    """

    async def on_user_invalidate(slug: str | None) -> None:
        runtime = tls_runtime_holder["runtime"]
        if slug is None:
            doh_reachable = await runtime.refresh_all()
        else:
            doh_reachable = await runtime.refresh_slug(slug=slug)
        if not doh_reachable:
            # Rendering from a cache that missed its refresh would strip
            # integrations from the gateway env; keep file and processes as-is.
            logger.error("refresh after invalidate(slug=%s) failed transiently; managed env left untouched", slug)
            return
        env_changed = await _render_gateway_env_file(tls_runtime=runtime, env_path=env_path)
        if _slug_affects_model_picker(slug=slug, runtime=runtime):
            await asyncio.to_thread(_delete_webui_models_cache, webui_state_dir=webui_state_dir)
        if not env_changed:
            return
        await _restart_processes_for_env_change(runtime=runtime, process_compose_url=process_compose_url, slug=slug)

    return on_user_invalidate


async def _restart_processes_for_env_change(
    runtime: tls_intercept.TlsInterceptRuntime,
    process_compose_url: str,
    slug: str | None,
) -> None:
    """Restart every process the touched provider(s) declare; raises on a failed restart."""
    for process_name in _processes_requiring_restart(slug=slug, runtime=runtime):
        status, body = await asyncio.to_thread(
            _post_process_compose_restart,
            process_compose_url=process_compose_url,
            process_name=process_name,
        )
        if not (200 <= status < 300):
            logger.error("%s restart returned %d: %s", process_name, status, body)
            raise RuntimeError(
                f"{process_name} restart failed (process-compose returned {status}); "
                f"please redeploy the app to apply the new credentials"
            )
        logger.info("%s restart kicked off after invalidate(slug=%s)", process_name, slug)


def _delete_webui_models_cache(webui_state_dir: Path) -> bool:
    """Delete WebUI's persisted /api/models cache without importing WebUI code."""
    cache_path = webui_state_dir / "models_cache.json"
    try:
        cache_path.unlink()
    except FileNotFoundError:
        logger.info("WebUI models cache already absent at %s", cache_path)
        return False
    except OSError as exc:
        logger.error("failed to delete WebUI models cache at %s: %s", cache_path, exc)
        raise RuntimeError(f"failed to delete WebUI models cache at {cache_path}") from exc
    logger.info("deleted WebUI models cache at %s", cache_path)
    return True


def _slug_affects_model_picker(slug: str | None, runtime: tls_intercept.TlsInterceptRuntime) -> bool:
    """Return whether a provider state change can alter /api/models output."""
    providers = runtime._token_store._providers
    if slug is None:
        return any(spec.affects_model_picker for spec in providers.values())
    spec = providers.get(slug)
    if spec is None:
        return False
    return spec.affects_model_picker


def _processes_requiring_restart(slug: str | None, runtime: tls_intercept.TlsInterceptRuntime) -> tuple[str, ...]:
    """Return process-compose entries that must reload after provider state changes."""
    processes: list[str] = []
    if _slug_requires_gateway_restart(slug=slug, runtime=runtime):
        processes.append(GATEWAY_PROCESS_NAME)
    if _slug_requires_webui_restart(slug=slug, runtime=runtime):
        processes.append(WEBUI_PROCESS_NAME)
    return tuple(processes)


def _slug_requires_gateway_restart(slug: str | None, runtime: tls_intercept.TlsInterceptRuntime) -> bool:
    """A user-invalidate triggers gateway restart only when the provider declares it.

    `slug=None` (Refresh-all) restarts only if any provider in scope declares
    `restart_gateway_after_save`.
    """
    providers = runtime._token_store._providers
    if slug is None:
        return any(spec.restart_gateway_after_save for spec in providers.values())
    spec = providers.get(slug)
    if spec is None:
        return False
    return spec.restart_gateway_after_save


def _slug_requires_webui_restart(slug: str | None, runtime: tls_intercept.TlsInterceptRuntime) -> bool:
    """Restart WebUI when the touched provider declares that WebUI must reload."""
    providers = runtime._token_store._providers
    if slug is None:
        return any(spec.restart_webui_after_save for spec in providers.values())
    spec = providers.get(slug)
    if spec is None:
        return False
    return spec.restart_webui_after_save


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


def _env_flag_enabled(*, name: str, default: bool) -> bool:
    """Parse a bool-ish environment flag, falling back on empty or invalid values."""
    value = os.environ.get(name, "")
    if not value:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    logger.error("Invalid boolean value for %s=%r; using default %s", name, value, default)
    return default


async def _run(
    proxy_port: int,
    control_port: int,
    mcp_port: int,
    ca_dir: Path,
    private_dir: Path,
    mcp_persistent_dir: Path,
    gateway_env_path: Path,
    webui_state_dir: Path,
    process_compose_url: str,
) -> None:
    control_plane_url = _require_env(name="DOH_CONTROL_PLANE_URL")
    bearer = _require_env(name="DOH_ENV_BEARER")
    owner_username = _require_env(name="DOH_OWNER_USERNAME")
    app_slug = _require_env(name="DOH_APP_SLUG")
    env_slug = os.environ.get("DOH_ENV_SLUG", "")
    merge_enabled = _env_flag_enabled(name="DOH_MERGE_INTEGRATION_ENABLED", default=True)
    logger.info(
        "starting integrations_broker for owner=%s env=%s against %s (proxy=%d, control=%d, mcp=%d, merge_enabled=%s)",
        owner_username, env_slug, control_plane_url, proxy_port, control_port, mcp_port, merge_enabled,
    )

    tls_runtime_holder: dict = {}
    on_user_invalidate = _build_on_user_invalidate(
        tls_runtime_holder=tls_runtime_holder,
        env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url=process_compose_url,
    )
    tls_runtime = tls_intercept.TlsInterceptRuntime(
        providers=tls_intercept.TLS_INTERCEPT_PROVIDERS,
        refresh_config=tls_intercept.DohRefreshConfig(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
            app_slug=app_slug,
        ),
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
        on_user_invalidate=on_user_invalidate,
    )
    tls_runtime_holder["runtime"] = tls_runtime
    # Render the managed profile env file from current DOH state before opening the
    # control port. supervisor.sh's wait_for_port on the control port doubles
    # as the synchronization point: by the time it returns, the file is on
    # disk and webui.sh can launch process-compose children with current env.
    await _bootstrap_gateway_env(
        tls_runtime=tls_runtime,
        env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url=process_compose_url,
    )

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, lambda s=sig: (logger.info("signal %d; shutting down", s), stop.done() or stop.set_result(None)))

    tls_proxy_server = await tls_runtime.start_proxy_server(host="127.0.0.1", port=proxy_port)
    logger.info("proxy listening on 127.0.0.1:%d", proxy_port)

    public_base_url = os.environ.get("DOH_APP_PUBLIC_URL")
    mcp_persistent_dir.mkdir(parents=True, exist_ok=True)
    aggregator = mcp_aggregator.MCPAggregator(
        port=mcp_port,
        persistent_dir=mcp_persistent_dir,
        public_base_url=public_base_url,
        doh_control_plane_url=control_plane_url,
        doh_env_bearer=bearer,
        doh_app_slug=app_slug,
        doh_owner_username=owner_username,
        merge_enabled=merge_enabled,
    )

    async def _store_device_tokens(provider: str, tokens: dict) -> bool:
        """Persist a provider device-flow token payload to DOH, then refresh TLS state."""
        payload = {
            "owner_username": owner_username,
            "app_slug": app_slug,
            **tokens,
        }
        status, _ = await asyncio.to_thread(
            _post_control_plane_json,
            control_plane_url=control_plane_url,
            bearer=bearer,
            path=f"/api/integrations/credentials/{provider}/device-complete",
            payload=payload,
        )
        if not (200 <= status < 300):
            return False
        with contextlib.suppress(RuntimeError):
            await tls_runtime.invalidate(slug=provider)
        if provider in AUTH_MARKER_PROVIDERS:
            await asyncio.to_thread(_run_provider_auth_marker, provider, "connect")
        return True

    oauth_device_flow = device_flow.build_default_device_flow(submit_tokens=_store_device_tokens)

    control_app = _build_control_app(
        aggregator=aggregator,
        tls_runtime=tls_runtime,
        oauth_device_flow=oauth_device_flow,
        control_plane_url=control_plane_url,
        bearer=bearer,
        owner_username=owner_username,
        app_slug=app_slug,
        env_slug=env_slug,
    )
    control_uvicorn_config = uvicorn.Config(
        app=control_app, host="127.0.0.1", port=control_port, log_level="warning", access_log=False,
    )
    control_server = uvicorn.Server(config=control_uvicorn_config)
    control_server.install_signal_handlers = lambda: None
    logger.info("control API listening on 127.0.0.1:%d", control_port)

    async with tls_proxy_server:
        proxy_task = asyncio.create_task(tls_proxy_server.serve_forever())
        control_task = asyncio.create_task(control_server.serve())
        mcp_task = asyncio.create_task(aggregator.serve())
        done, pending = await asyncio.wait(
            {stop, proxy_task, control_task, mcp_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            exc = task.exception() if task.done() and not task.cancelled() else None
            if exc:
                logger.error("task exited: %r", exc)
                raise exc


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [integrations_broker] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--proxy-port", type=int, default=DEFAULT_PROXY_PORT)
    parser.add_argument("--control-port", type=int, default=DEFAULT_CONTROL_PORT)
    parser.add_argument("--mcp-port", type=int, default=DEFAULT_MCP_PORT)
    parser.add_argument("--ca-dir", type=Path, default=DEFAULT_CA_DIR)
    parser.add_argument("--private-dir", type=Path, default=DEFAULT_PRIVATE_DIR)
    parser.add_argument("--mcp-persistent-dir", type=Path, default=DEFAULT_MCP_PERSISTENT_DIR)
    parser.add_argument("--gateway-env-path", type=Path, default=DEFAULT_GATEWAY_ENV_PATH)
    parser.add_argument("--webui-state-dir", type=Path, default=DEFAULT_WEBUI_STATE_DIR)
    parser.add_argument("--process-compose-url", default=DEFAULT_PROCESS_COMPOSE_URL)
    args = parser.parse_args()
    try:
        asyncio.run(
            _run(
                proxy_port=args.proxy_port,
                control_port=args.control_port,
                mcp_port=args.mcp_port,
                ca_dir=args.ca_dir,
                private_dir=args.private_dir,
                mcp_persistent_dir=args.mcp_persistent_dir,
                gateway_env_path=args.gateway_env_path,
                webui_state_dir=args.webui_state_dir,
                process_compose_url=args.process_compose_url,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
