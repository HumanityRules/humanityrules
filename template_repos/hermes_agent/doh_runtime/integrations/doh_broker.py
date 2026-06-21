"""Outside-the-sandbox broker — the env's single relay to DOH.

Fronts per-user third-party integrations and the self-referential permissions
editor (more DOH APIs later). Runs as a supervisor-managed sidecar process. Pure
composition root: it reads the environment contract, constructs the subsystems,
wires them together, and runs the servers. All credential-change choreography
lives in `credentials_service`; all HTTP parsing lives in `control_api`.

It starts:

1. The TLS-intercept proxy on 127.0.0.1:9950. The nono sandbox gets
   HTTPS_PROXY pointed here and SSL_CERT_FILE pointed at the CA bundle created
   by `tls_intercept.TlsInterceptRuntime`.

2. The control API on 127.0.0.1:9951, reached same-origin by the WebUI
   extensions via Caddy's /__doh_broker/* route. This API mounts the
   MCP-aggregator management routes under /integrations and the permissions
   relay under /permissions.

The aggregator's port 9952 is sandbox-only MCP traffic. Refresh tokens, DOH's
OAuth client secrets, and the env bearer never enter the sandbox.

Environment contract (set by deploy_app.py's env-bearer overlay):
- HUMR_ENV_BEARER        — bearer for DOH's per-env integration endpoints.
- HUMR_OWNER_USERNAME    — whose grants this container is for.
- HUMR_APP_SLUG          — logical app key for app-scoped credentials.
- HUMR_CONTROL_PLANE_URL — base URL for DOH (e.g. https://humanityrules.io).
- HUMR_ENV_SLUG          — env slug, for logging only.
- HUMR_MERGE_INTEGRATION_ENABLED — optional; false disables all Merge.dev connectors.

Also required, set by the Dockerfile: HERMES_WEBUI_PYTHON, HUMR_RUNTIME_DIR, and
HERMES_HOME — passed to CredentialsService so it can run provider auth markers
as the gateway user.

Required file system:
- BROKER_CA_DIR must be writable by the broker user. The CA bundle is written
  here on startup for SSL_CERT_FILE to pick up.
- BROKER_PRIVATE_DIR must be writable by the broker user and unreadable by the
  sandbox.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

import uvicorn

import control_api
from credentials_service import CredentialsService
import device_flow
from doh_client import DohClient
from mcp_aggregator import MCPAggregator
import tls_intercept
import tls_providers


DEFAULT_PROXY_PORT = 9950
DEFAULT_CONTROL_PORT = 9951
DEFAULT_MCP_PORT = 9952
DEFAULT_CA_DIR = Path("/run/doh/integrations-broker/ca")
DEFAULT_PRIVATE_DIR = Path("/run/doh/integrations-broker/private")
DEFAULT_MCP_PERSISTENT_DIR = Path("/hermes-persistent-root/mcp-aggregator")
DEFAULT_GATEWAY_ENV_PATH = Path("/workspace/.hermes/.env")
DEFAULT_WEBUI_STATE_DIR = Path("/workspace/.hermes/webui-mvp")
DEFAULT_PROCESS_COMPOSE_URL = "http://127.0.0.1:9956"
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})

logger = logging.getLogger("doh_broker")


class _AsyncioSslEofFilter(logging.Filter):
    """Drop asyncio's 'returning true from eof_received() has no effect when using ssl'
    warning. Stdlib StreamReaderProtocol returns True over SSL transports too
    (python/cpython#82918); the TLS-intercept path trips it once per Telegram long poll,
    and it is harmless — the transport closes either way."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "eof_received() has no effect when using ssl" not in record.getMessage()


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
    control_plane_url = _require_env(name="HUMR_CONTROL_PLANE_URL")
    bearer = _require_env(name="HUMR_ENV_BEARER")
    owner_username = _require_env(name="HUMR_OWNER_USERNAME")
    app_slug = _require_env(name="HUMR_APP_SLUG")
    env_slug = os.environ.get("HUMR_ENV_SLUG", "")
    merge_enabled = _env_flag_enabled(name="HUMR_MERGE_INTEGRATION_ENABLED", default=True)
    webui_python = Path(_require_env(name="HERMES_WEBUI_PYTHON"))
    runtime_dir = Path(_require_env(name="HUMR_RUNTIME_DIR"))
    hermes_home = Path(_require_env(name="HERMES_HOME"))
    logger.info(
        "starting doh_broker for owner=%s env=%s against %s (proxy=%d, control=%d, mcp=%d, merge_enabled=%s)",
        owner_username, env_slug, control_plane_url, proxy_port, control_port, mcp_port, merge_enabled,
    )

    doh_client = DohClient(
        control_plane_url=control_plane_url,
        bearer=bearer,
        owner_username=owner_username,
        app_slug=app_slug,
    )
    tls_intercept_runtime = tls_intercept.TlsInterceptRuntime(
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        doh_client=doh_client,
        refresh_lead_seconds=tls_intercept.REFRESH_LEAD_SECONDS,
        ca_dir=ca_dir,
        private_dir=private_dir,
    )

    public_base_url = os.environ.get("HUMR_APP_PUBLIC_URL")
    mcp_persistent_dir.mkdir(parents=True, exist_ok=True)
    mcp_aggregator = MCPAggregator(
        port=mcp_port,
        persistent_dir=mcp_persistent_dir,
        public_base_url=public_base_url,
        doh_control_plane_url=control_plane_url,
        doh_env_bearer=bearer,
        doh_app_slug=app_slug,
        doh_owner_username=owner_username,
        merge_enabled=merge_enabled,
    )

    credentials_service = CredentialsService(
        doh_client=doh_client,
        tls_intercept_runtime=tls_intercept_runtime,
        mcp_aggregator=mcp_aggregator,
        providers=tls_providers.TLS_INTERCEPT_PROVIDERS,
        gateway_env_path=gateway_env_path,
        webui_state_dir=webui_state_dir,
        process_compose_url=process_compose_url,
        webui_python=webui_python,
        runtime_dir=runtime_dir,
        hermes_home=hermes_home,
    )
    # Render the managed profile env file from current DOH state before opening the
    # control port. supervisor.sh's wait_for_port on the control port doubles
    # as the synchronization point: by the time it returns, the file is on
    # disk and webui.sh can launch process-compose children with current env.
    await credentials_service.bootstrap()

    loop = asyncio.get_running_loop()
    stop = loop.create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, lambda s=sig: (logger.info("signal %d; shutting down", s), stop.done() or stop.set_result(None)))

    tls_proxy_server = await tls_intercept_runtime.start_proxy_server(host="127.0.0.1", port=proxy_port)
    logger.info("proxy listening on 127.0.0.1:%d", proxy_port)

    oauth_device_flow = device_flow.build_default_device_flow(submit_tokens=credentials_service.complete_device_flow)

    control_app = control_api.build_control_app(
        mcp_aggregator=mcp_aggregator,
        tls_intercept_runtime=tls_intercept_runtime,
        oauth_device_flow=oauth_device_flow,
        credentials_service=credentials_service,
        doh_client=doh_client,
        env_slug=env_slug,
    )
    control_uvicorn_config = uvicorn.Config(
        app=control_app, host="127.0.0.1", port=control_port, log_level="warning", access_log=False,
    )
    control_server = uvicorn.Server(config=control_uvicorn_config)
    control_server.install_signal_handlers = lambda: None
    logger.info("control API listening on 127.0.0.1:%d", control_port)

    async with tls_proxy_server:
        tls_proxy_task = asyncio.create_task(tls_proxy_server.serve_forever())
        control_task = asyncio.create_task(control_server.serve())
        mcp_aggregator_task = asyncio.create_task(mcp_aggregator.serve())
        done, pending = await asyncio.wait(
            {stop, tls_proxy_task, control_task, mcp_aggregator_task},
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
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [doh_broker.%(name)s] %(message)s")
    logging.getLogger("asyncio").addFilter(_AsyncioSslEofFilter())
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
