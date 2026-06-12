"""Per-user integration credential lifecycle for the integrations broker.

Owns the choreography that follows every credential change, regardless of
which surface initiated it (device-flow completion, browser disconnect,
vault save, explicit Refresh): persist/confirm the change with DOH, drop
the TLS-intercept token cache, refresh it from DOH truth, re-render the
gateway-managed env block, and kick whatever each provider spec declares
(gateway/WebUI restart, models-cache drop, local auth markers).

Layering: `control_api` parses HTTP and delegates here; `integrations_broker`
constructs one instance at startup. Mechanism modules (`tls_intercept`,
`device_flow`, `mcp_aggregator`) sit below and never call back up.
"""

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from doh_client import DohClient
from mcp_aggregator import MCPAggregator
import tls_intercept


GATEWAY_PROCESS_NAME = "system.gateway"
WEBUI_PROCESS_NAME = "system.webui"
# Rate limit for the user-facing Refresh-all button. Policy, not correctness:
# concurrent catalog reloads are serialized by the aggregator's own lock.
REFRESH_COOLDOWN_SECONDS = 30
# The in-sandbox gateway user; auth.json must stay owned by it (mode 0600), so
# the root broker drops to it via runuser when it touches the local auth store.
GATEWAY_USER = "hermeswebui"
AUTH_MARKER_PROVIDERS = frozenset({"openai-codex", "nous"})

logger = logging.getLogger("credentials_service")


class CredentialsService:
    """User-intent operations over this env's integration credentials."""

    def __init__(
        self,
        doh_client: DohClient,
        tls_intercept_runtime: tls_intercept.TlsInterceptRuntime,
        mcp_aggregator: MCPAggregator,
        providers: dict[str, tls_intercept.TlsProviderSpec],
        gateway_env_path: Path,
        webui_state_dir: Path,
        process_compose_url: str,
    ) -> None:
        self._doh_client = doh_client
        self._tls_intercept_runtime = tls_intercept_runtime
        self._mcp_aggregator = mcp_aggregator
        self._providers = providers
        self._gateway_env_path = gateway_env_path
        self._webui_state_dir = webui_state_dir
        self._process_compose_url = process_compose_url
        self._last_refresh_all_ts: float = 0.0

    async def bootstrap(self) -> None:
        """At broker startup: refresh every provider, then render the env file once.

        A failed refresh (DOH unreachable, e.g. a 503 mid-deploy) is fatal: the
        broker exits before opening its control port, supervisor.sh tears the
        container down, and ECS restarts the task until DOH answers. Dying here
        is what guarantees the gateway/WebUI children only ever launch with an
        env rendered from live DOH state — no stale-env recovery path needed.
        """
        if not await self._tls_intercept_runtime.refresh_all():
            logger.error("FATAL: bootstrap refresh against DOH failed; exiting so ECS restarts the task")
            sys.exit(1)
        await self._render_gateway_env_file()

    async def complete_device_flow(self, provider: str, tokens: dict) -> bool:
        """Persist a provider device-flow token payload to DOH, then refresh TLS state.

        Passed to `device_flow` as its `submit_tokens` hook — the device flow
        itself never learns about DOH or the TLS cache.
        """
        status, _ = await asyncio.to_thread(
            self._doh_client.post_json,
            path=f"/api/integrations/credentials/{provider}/device-complete",
            payload=dict(tokens),
            timeout_seconds=30,
        )
        if not (200 <= status < 300):
            return False
        with contextlib.suppress(RuntimeError):
            await self.credentials_invalidate(slug=provider)
        if provider in AUTH_MARKER_PROVIDERS:
            await asyncio.to_thread(_run_provider_auth_marker, provider=provider, action="connect")
        return True

    async def credentials_disconnect(self, provider: str) -> tuple[int, dict]:
        """Disconnect any TLS-intercept provider (vault or OAuth); returns DOH's `(status, payload)`.

        DOH's unified disconnect handler deletes the credential row and, for
        OAuth providers, best-effort revokes upstream — the provider kind is
        resolved server-side, so both kinds are forwarded identically. On
        success we drop only this provider's cached token; any process restart
        is selected from the provider spec inside `credentials_invalidate`.
        """
        status, payload = await asyncio.to_thread(
            self._doh_client.post_json,
            path="/api/integrations/credentials/disconnect",
            payload={"provider": provider},
            timeout_seconds=30,
        )
        if not (200 <= status < 300):
            return status, payload
        try:
            await self.credentials_invalidate(slug=provider)
        except RuntimeError as exc:
            return 502, {**payload, "ok": False, "error": str(exc)}
        if provider in AUTH_MARKER_PROVIDERS:
            await asyncio.to_thread(_run_provider_auth_marker, provider=provider, action="disconnect")
        return status, payload

    async def credentials_setup_session(self, provider: str, public_origin: str) -> tuple[int, dict]:
        """Ask DOH for a vault setup-session submit token; returns DOH's `(status, payload)`."""
        return await asyncio.to_thread(
            self._doh_client.post_json,
            path="/api/integrations/credentials/setup-session",
            payload={"provider": provider, "public_origin": public_origin},
            timeout_seconds=30,
        )

    async def credentials_invalidate(self, slug: str) -> None:
        """Drop one provider's cached token after a known state change, then re-project.

        Raises RuntimeError when a required process restart (or models-cache
        delete) failed; a transient DOH refresh failure is absorbed instead —
        see `_refresh_and_apply`.
        """
        await self._tls_intercept_runtime.invalidate(slug=slug)
        await self._refresh_and_apply(slug=slug)

    async def refresh_all_integrations(self) -> tuple[int, dict]:
        """Explicit-Refresh: reload the MCP catalog and drop the all-providers TLS cache.

        Returns the browser-facing `(status, payload)`. If a refresh ran
        within REFRESH_COOLDOWN_SECONDS we return 429 without firing either
        side — otherwise smashing the Refresh button would repeatedly trigger
        the catalog reload and the invalidate choreography (DOH round-trip +
        gateway-env rewrite + gateway restart for vault providers). Once past
        the cooldown gate, the two sides run concurrently — they share no
        state and the slower of the two sets the round-trip latency.
        """
        remaining = self._refresh_cooldown_remaining_seconds()
        if remaining is not None:
            return 429, {"error": "refresh_cooldown", "retry_after_seconds": remaining}
        self._last_refresh_all_ts = time.time()

        async def _safe_invalidate_all() -> str | None:
            try:
                await self._tls_intercept_runtime.invalidate_all()
                await self._refresh_and_apply(slug=None)
            except RuntimeError as exc:
                return str(exc)
            return None

        catalog_payload, tls_error = await asyncio.gather(
            self._mcp_aggregator.refresh_catalog(),
            _safe_invalidate_all(),
        )
        if tls_error is not None:
            return 502, {"ok": False, "error": tls_error}
        return 200, catalog_payload

    def _refresh_cooldown_remaining_seconds(self) -> int | None:
        """Seconds left on the Refresh-all cooldown, or None when a refresh is allowed now."""
        elapsed = time.time() - self._last_refresh_all_ts
        if elapsed < REFRESH_COOLDOWN_SECONDS:
            return int(REFRESH_COOLDOWN_SECONDS - elapsed) + 1
        return None

    async def _refresh_and_apply(self, slug: str | None) -> None:
        """Refresh from DOH after an invalidate, then project the result onto disk/processes.

        When `slug` is set (a single provider was just connected/disconnected),
        only that provider is re-fetched from DOH — refreshing every disconnected
        provider on each connect would spam DOH with `no integration row` 404s.
        `slug=None` (explicit Refresh-all) does fan out to every provider.
        """
        if slug is None:
            doh_reachable = await self._tls_intercept_runtime.refresh_all()
        else:
            doh_reachable = await self._tls_intercept_runtime.refresh_slug(slug=slug)
        if not doh_reachable:
            # Rendering from a cache that missed its refresh would strip
            # integrations from the gateway env; keep file and processes as-is.
            logger.error("refresh after invalidate(slug=%s) failed transiently; managed env left untouched", slug)
            return
        await self._apply_refreshed_state(slug=slug)

    async def _apply_refreshed_state(self, slug: str | None) -> None:
        """Project a successful refresh onto disk and processes.

        Renders the managed env block, drops WebUI's models cache when the touched
        provider(s) can change /api/models, and restarts whatever processes the
        provider(s) declare when the env actually changed. Callers must only
        invoke it after a refresh that reflected DOH truth. Raises on a failed
        process restart.
        """
        env_changed = await self._render_gateway_env_file()
        specs_in_scope = self._specs_in_scope(slug=slug)
        if any(spec.affects_model_picker for spec in specs_in_scope):
            await asyncio.to_thread(_delete_webui_models_cache, webui_state_dir=self._webui_state_dir)
        if not env_changed:
            return
        for process_name in self._processes_requiring_restart(slug=slug):
            status, body = await asyncio.to_thread(
                _post_process_compose_restart,
                process_compose_url=self._process_compose_url,
                process_name=process_name,
            )
            if not (200 <= status < 300):
                logger.error("%s restart returned %d: %s", process_name, status, body)
                raise RuntimeError(
                    f"{process_name} restart failed (process-compose returned {status}); "
                    f"please redeploy the app to apply the new credentials"
                )
            logger.info("%s restart kicked off after invalidate(slug=%s)", process_name, slug)

    async def _render_gateway_env_file(self) -> bool:
        """Write the DOH-managed profile env block from current cache state."""
        snapshot = await self._tls_intercept_runtime.gateway_env_snapshot()
        block = tls_intercept.render_managed_block(snapshot=snapshot)
        changed = await asyncio.to_thread(
            tls_intercept.write_gateway_env_file,
            env_path=self._gateway_env_path,
            managed_block=block,
        )
        if changed:
            logger.info("rewrote managed env at %s (%d bytes)", self._gateway_env_path, len(block))
        else:
            logger.info("managed env unchanged at %s", self._gateway_env_path)
        return changed

    def _specs_in_scope(self, slug: str | None) -> tuple[tls_intercept.TlsProviderSpec, ...]:
        """Return the provider specs a state change touches: one for a slug, all for None."""
        if slug is None:
            return tuple(self._providers.values())
        spec = self._providers.get(slug)
        if spec is None:
            return ()
        return (spec,)

    def _processes_requiring_restart(self, slug: str | None) -> tuple[str, ...]:
        """Return process-compose entries that must reload after provider state changes."""
        specs_in_scope = self._specs_in_scope(slug=slug)
        processes: list[str] = []
        if any(spec.restart_gateway_after_save for spec in specs_in_scope):
            processes.append(GATEWAY_PROCESS_NAME)
        if any(spec.restart_webui_after_save for spec in specs_in_scope):
            processes.append(WEBUI_PROCESS_NAME)
        return tuple(processes)


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
    script = str(Path(runtime_dir) / "sandbox_seed.py")
    try:
        result = subprocess.run(
            ["runuser", "-u", GATEWAY_USER, "--", python, script, "--auth-marker", action, provider],
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
