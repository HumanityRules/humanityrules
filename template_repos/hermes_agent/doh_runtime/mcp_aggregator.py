"""MCP Aggregator — exposes a 4-tool progressive-disclosure surface to Hermes.

Architecture: a single FastMCP server exposes /mcp to the sandbox on
127.0.0.1:9952. Instead of mounting transparent ProxyProviders that re-export
hundreds of upstream tool defs into Hermes's prompt, the aggregator registers
four LLM-facing tools (search/describe/call/list_connectors) backed by a flat
in-memory catalog assembled at boot from N pluggable Backend implementations.
See mcp_top_level_tools.py for the LLM-facing surface.

This module owns only the generic OAuth DCR/PKCE spine and the aggregator
class that composes everything. Per-service code lives elsewhere:

  - DCR connectors (Notion, PostHog, ...): one self-contained module each in
    `connectors/`. The aggregator iterates `connectors.DCR_CONNECTORS` at
    startup to build the provider table and backend list.
  - Merge.dev relay (Magic Link, not DCR): `mcp_merge_backend.py`. The aggregator
    constructs one `MergeBackend`, calls `boot()` on startup, and includes
    `MergeBackend.routes(...)` in its own `routes()` so the broker mounts
    Merge passthroughs alongside the DCR OAuth ones.

The integrations_broker mounts the aggregator's routes under its unified
/__doh_broker/* router. The aggregator owns its URL surface, the broker owns
the mount point.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from functools import partial
from pathlib import Path

import httpx
import uvicorn
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

import mcp_top_level_tools
from connectors import DCR_CONNECTORS, DCRConnectorSpec
from mcp_merge_backend import MergeBackend


logger = logging.getLogger("mcp_aggregator")

DEFAULT_MCP_PORT = 9952

REFRESH_COOLDOWN_SECONDS = 30

# OAuth-DCR-PKCE providers, indexed by slug. The OAuth handlers and refresh
# loop below resolve provider configuration off these specs.
DCR_CONNECTORS_BY_SLUG: dict[str, DCRConnectorSpec] = {spec.slug: spec for spec in DCR_CONNECTORS}


class _OAuthState:
    """Per-provider DCR client + tokens, persisted on EBS-backed disk."""

    def __init__(self, provider_dir: Path) -> None:
        self._provider_dir = provider_dir
        self._client_file = provider_dir / "client.json"
        self._token_file = provider_dir / "token.json"
        self._client: dict | None = None
        self._token: dict | None = None
        self._load()

    def _load(self) -> None:
        if self._client_file.exists():
            self._client = json.loads(self._client_file.read_text())
        if self._token_file.exists():
            self._token = json.loads(self._token_file.read_text())

    @property
    def has_client(self) -> bool:
        return self._client is not None

    @property
    def client_id(self) -> str | None:
        return self._client.get("client_id") if self._client else None

    @property
    def registered_redirect_uri(self) -> str | None:
        return self._client.get("redirect_uri") if self._client else None

    @property
    def has_token(self) -> bool:
        return self._token is not None

    @property
    def access_token(self) -> str | None:
        if not self._token:
            return None
        expires_at = self._token.get("expires_at", 0)
        if time.time() >= expires_at - 60:
            return None
        return self._token.get("access_token")

    @property
    def refresh_token(self) -> str | None:
        return self._token.get("refresh_token") if self._token else None

    def save_client(self, client_data: dict, redirect_uri: str) -> None:
        self._provider_dir.mkdir(parents=True, exist_ok=True)
        client_data["redirect_uri"] = redirect_uri
        self._client = client_data
        self._client_file.write_text(json.dumps(client_data, indent=2))

    def save_token(self, token_data: dict) -> None:
        self._provider_dir.mkdir(parents=True, exist_ok=True)
        if "expires_in" in token_data and "expires_at" not in token_data:
            token_data["expires_at"] = time.time() + token_data["expires_in"]
        self._token = token_data
        self._token_file.write_text(json.dumps(token_data, indent=2))

    def clear_token(self) -> None:
        self._token = None
        if self._token_file.exists():
            self._token_file.unlink()

    def clear_client(self) -> None:
        self._client = None
        if self._client_file.exists():
            self._client_file.unlink()


class MCPAggregator:
    """One per Hermes container. Hosts a FastMCP server exposing the 4 progressive-disclosure tools."""

    def __init__(
        self,
        port: int,
        persistent_dir: Path,
        public_base_url: str | None,
        doh_control_plane_url: str,
        doh_env_bearer: str,
        doh_app_slug: str,
        doh_owner_username: str,
    ) -> None:
        self._port = port
        self._persistent_dir = persistent_dir
        self._public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self._doh_control_plane_url = doh_control_plane_url.rstrip("/")
        self._doh_env_bearer = doh_env_bearer
        self._doh_app_slug = doh_app_slug
        self._doh_owner_username = doh_owner_username
        self._mcp = FastMCP(name="doh-mcp-aggregator")
        self._oauth_states: dict[str, _OAuthState] = {}
        self._pending_oauth: dict[str, dict] = {}
        for spec in DCR_CONNECTORS:
            self._oauth_states[spec.slug] = _OAuthState(provider_dir=persistent_dir / spec.slug)
        self._catalog_store = mcp_top_level_tools.CatalogStore()
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_ts: float = 0.0

        # Merge (relayed via DOH, not DCR) first, then one Backend per connector spec.
        # Each spec.make_backend gets its own subdir under persistent_dir for any
        # extra state the connector wants to keep (e.g. PostHog's session config).
        # Native connectors win over same-slug Merge connectors in the catalog
        # and browser integrations list. TLS-intercept providers (e.g. github)
        # are also "native" in this sense — DOH manages their auth directly, so
        # the equivalent Merge connector would just duplicate the surface and
        # confuse the agent.
        merge_excluded = frozenset(set(DCR_CONNECTORS_BY_SLUG.keys()) | {"github"})
        self._merge_backend = MergeBackend(
            doh_control_plane_url=self._doh_control_plane_url,
            doh_env_bearer=self._doh_env_bearer,
            doh_app_slug=self._doh_app_slug,
            doh_owner_username=self._doh_owner_username,
            excluded_connector_slugs=merge_excluded,
            on_config_change=self._on_state_change,
        )
        self._backends: list[mcp_top_level_tools.Backend] = [self._merge_backend]
        for spec in DCR_CONNECTORS:
            backend = spec.make_backend(
                oauth_state=self._oauth_states[spec.slug],
                refresh_fn=partial(self._refresh_access_token, slug=spec.slug),
                persistent_dir=persistent_dir / spec.slug,
                on_config_change=self._on_state_change,
            )
            self._backends.append(backend)

    async def _reload_catalog(self) -> None:
        """Force a full catalog reload, awaiting completion. Used by the user-facing Refresh button."""
        async with self._refresh_lock:
            self._last_refresh_ts = time.time()
            for backend in self._backends:
                await backend.invalidate_caches()
            await self._catalog_store.reload(backends=self._backends)

    async def _on_state_change(self, backend_name: str, connector: str, transition: mcp_top_level_tools.StateTransition) -> None:
        """Dispatcher every backend's on_config_change is wired to.

        Per-transition policy:
        - "disconnected": drop the connector's rows from the catalog and zero
          its tool_count. No network — pure dict mutation. Block on it; by
          the time the caller returns, the agent's view is already consistent.
        - "connected" / "reconfigured": schedule a per-backend reload in the
          background. Only the changed backend re-lists tools, so a slow Merge
          response can't delay a Notion connect. The HTTP response doesn't
          wait — the panel renders from oauth.has_token / Merge's live
          authenticated_connectors and doesn't need the catalog.
        """
        if transition == "disconnected":
            self._catalog_store.drop(backend_name=backend_name, connector=connector)
            return
        backend = self._backend_by_name(name=backend_name)
        if backend is None:
            logger.error("on_config_change for unknown backend %r; skipping reload", backend_name)
            return
        asyncio.create_task(self._catalog_store.reload_backend(backend=backend))

    def _backend_by_name(self, *, name: str) -> mcp_top_level_tools.Backend | None:
        for backend in self._backends:
            if backend.name == name:
                return backend
        return None

    async def serve(self) -> None:
        await self._merge_backend.boot()
        connect_kinds: dict[str, str] = {"merge": "magic_link"}
        for spec in DCR_CONNECTORS:
            connect_kinds[spec.slug] = spec.connect_kind
        mcp_top_level_tools.register(
            mcp=self._mcp,
            store=self._catalog_store,
            backends=self._backends,
            connect_kinds=connect_kinds,
        )
        asyncio.create_task(self._catalog_store.load_in_background(backends=self._backends))

        mcp_app = self._mcp.http_app(path="/mcp", transport="streamable-http")
        config = uvicorn.Config(app=mcp_app, host="127.0.0.1", port=self._port, log_level="warning", access_log=False)
        server = uvicorn.Server(config=config)
        # The broker process owns signal handling; uvicorn must not install its own.
        server.install_signal_handlers = lambda: None
        logger.info("MCP aggregator listening on 127.0.0.1:%d", self._port)
        await server.serve()

    def cooldown_remaining_seconds(self) -> int | None:
        """Seconds left on the refresh cooldown, or None if a refresh is allowed now.

        Exposed so callers (the broker's unified `/integrations/refresh` route)
        can gate on cooldown *before* firing co-routines whose side effects
        shouldn't run during a no-op refresh.
        """
        elapsed = time.time() - self._last_refresh_ts
        if elapsed < REFRESH_COOLDOWN_SECONDS:
            return int(REFRESH_COOLDOWN_SECONDS - elapsed) + 1
        return None

    async def refresh_catalog(self) -> tuple[bool, dict]:
        """User-facing Refresh button hits this. Returns (ok, payload)."""
        remaining = self.cooldown_remaining_seconds()
        if remaining is not None:
            return False, {"error": "refresh_cooldown", "retry_after_seconds": remaining}
        async with self._refresh_lock:
            self._last_refresh_ts = time.time()
            for backend in self._backends:
                await backend.invalidate_caches()
            await self._catalog_store.reload(backends=self._backends)
        connectors = {(e.backend, e.connector) for e in self._catalog_store.entries.values()}
        return True, {"ok": True, "tools": len(self._catalog_store.entries), "connectors": len(connectors)}

    def routes(self, prefix: str) -> list[Route]:
        """Routes for the integrations_broker to mount under its unified /__doh_broker/* router.

        Merge owns its own routes under merge_backend.routes(prefix=...); the aggregator
        composes both URL surfaces into one list so the broker mounts them in one shot.
        """
        return [
            Route(path=f"{prefix}/{{provider}}/oauth/start", endpoint=self.handle_oauth_start, methods=["GET"]),
            Route(path=f"{prefix}/{{provider}}/oauth/callback", endpoint=self.handle_oauth_callback, methods=["GET"]),
            Route(path=f"{prefix}/{{provider}}/disconnect", endpoint=self.handle_disconnect, methods=["POST"]),
            *self._merge_backend.routes(prefix=prefix),
        ]

    async def _current_access_token(self, slug: str) -> str | None:
        oauth = self._oauth_states[slug]
        token = oauth.access_token
        if token is not None:
            return token
        return await self._refresh_access_token(slug=slug)

    # ── Starlette endpoints (mounted by integrations_broker on port 9951) ─

    async def status_items(self) -> list[dict]:
        """Return ready-to-render integration cards for the broker's unified status fan-out.

        One entry per DCR provider plus one per Merge-managed connector. The broker
        prepends its own TLS-intercept entries (Google) and returns the combined list
        to the WebUI, which renders cards by `kind`. Keeping the assembly here means
        the broker never has to know Merge or PostHog or Notion exist.
        """
        items: list[dict] = []
        merge_connectors = await self._merge_backend.fetch_connectors()
        merge_connectors_by_slug: dict[str, dict] = {}
        for connector in merge_connectors:
            merge_slug = connector.get("slug")
            if isinstance(merge_slug, str) and merge_slug:
                merge_connectors_by_slug[merge_slug] = connector

        for slug, spec in DCR_CONNECTORS_BY_SLUG.items():
            oauth = self._oauth_states[slug]
            item = {
                "kind": "mcp_aggregator",
                "slug": slug,
                "label": spec.label,
                "status": "connected" if oauth.has_token else "not_connected",
            }
            merge_connector = merge_connectors_by_slug.get(slug)
            if merge_connector is not None:
                item["logo_url"] = merge_connector.get("logo_url")
            items.append(item)

        for connector in self._merge_backend.filter_visible_connectors(connectors=merge_connectors):
            items.append({
                "kind": "merge_connector",
                "slug": connector.get("slug"),
                "label": connector.get("name"),
                "logo_url": connector.get("logo_url"),
                "status": connector.get("status", "unknown"),
            })
        return items

    async def handle_disconnect(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        if provider not in DCR_CONNECTORS_BY_SLUG:
            return JSONResponse(content={"error": "unknown provider"}, status_code=404)
        self._oauth_states[provider].clear_token()
        self._oauth_states[provider].clear_client()
        await self._on_state_change(provider, provider, "disconnected")
        return JSONResponse(content={"ok": True})

    async def handle_oauth_start(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        spec = DCR_CONNECTORS_BY_SLUG.get(provider)
        if spec is None:
            return Response(content="unknown OAuth provider", status_code=404)

        return_to = request.query_params.get("return_to", "/")
        origin = request.query_params.get("origin", "") or self._public_base_url or ""
        if not origin:
            return Response(content="origin query param required on first connect", status_code=400)
        if not self._public_base_url:
            self._public_base_url = origin
        redirect_uri = origin + f"/__doh_broker/integrations/{provider}/oauth/callback"
        oauth = self._oauth_states[provider]

        metadata = await self._fetch_oauth_metadata(url=spec.oauth_metadata_url)
        if metadata is None:
            return Response(content="failed to fetch OAuth metadata", status_code=502)

        scope = spec.default_scope

        if not oauth.has_client or oauth.registered_redirect_uri != redirect_uri:
            registration_endpoint = metadata.get("registration_endpoint")
            if not registration_endpoint:
                return Response(content="provider does not support DCR", status_code=502)
            client_data = await self._register_client(
                registration_endpoint=registration_endpoint,
                redirect_uri=redirect_uri,
                provider_label=spec.label,
                scope=scope,
            )
            if client_data is None:
                return Response(content="DCR registration failed", status_code=502)
            oauth.save_client(client_data=client_data, redirect_uri=redirect_uri)

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        self._pending_oauth[state] = {"provider": provider, "code_verifier": code_verifier, "return_to": return_to}

        authorize_params: dict[str, str] = {
            "client_id": oauth.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if scope:
            authorize_params["scope"] = scope
        params = urllib.parse.urlencode(authorize_params)
        return RedirectResponse(url=f"{metadata['authorization_endpoint']}?{params}", status_code=302)

    async def handle_oauth_callback(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        spec = DCR_CONNECTORS_BY_SLUG.get(provider)
        if spec is None:
            return Response(content="unknown OAuth provider", status_code=404)

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        error = request.query_params.get("error", "")

        if error:
            logger.error("OAuth callback error: %s — %s", error, request.query_params.get("error_description", ""))
            return Response(content=f"OAuth error: {error}", status_code=400)
        if state not in self._pending_oauth:
            return Response(content="invalid or expired state parameter", status_code=400)

        pending = self._pending_oauth.pop(state)
        if pending["provider"] != provider:
            return Response(content="state/provider mismatch", status_code=400)
        code_verifier = pending["code_verifier"]
        return_to = pending["return_to"]

        oauth = self._oauth_states[provider]

        metadata = await self._fetch_oauth_metadata(url=spec.oauth_metadata_url)
        if metadata is None:
            return Response(content="failed to fetch OAuth metadata", status_code=502)

        token_data = await self._exchange_code(
            token_endpoint=metadata["token_endpoint"],
            client_id=oauth.client_id,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=oauth.registered_redirect_uri,
        )
        if token_data is None:
            return Response(content="token exchange failed", status_code=502)

        oauth.save_token(token_data=token_data)
        await self._on_state_change(provider, provider, "connected")

        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(url=f"{return_to}{separator}connected={provider}", status_code=302)

    # ── OAuth helpers ───────────────────────────────────────────────

    async def _fetch_oauth_metadata(self, url: str) -> dict | None:
        try:
            # follow_redirects=True: PostHog's /.well-known/oauth-authorization-server
            # at mcp.posthog.com 302s to oauth.posthog.com (different host) — RFC 8414
            # discovery is supposed to live at the resource origin, but providers
            # commonly redirect to the AS origin instead.
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url=url)
            if resp.status_code == 200:
                return resp.json()
            logger.error("OAuth metadata %s returned %d", url, resp.status_code)
        except Exception:
            logger.exception("OAuth metadata fetch failed for %s", url)
        return None

    async def _register_client(self, registration_endpoint: str, redirect_uri: str, provider_label: str, scope: str | None = None) -> dict | None:
        body: dict = {
            "client_name": f"DOH Hermes - {provider_label}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if scope:
            body["scope"] = scope
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url=registration_endpoint, json=body)
            if resp.status_code in (200, 201):
                return resp.json()
            logger.error("DCR at %s failed: %d %s", registration_endpoint, resp.status_code, resp.text[:300])
        except Exception:
            logger.exception("DCR request to %s failed", registration_endpoint)
        return None

    async def _exchange_code(self, token_endpoint: str, client_id: str, code: str, code_verifier: str, redirect_uri: str) -> dict | None:
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url=token_endpoint, data=data)
            if resp.status_code == 200:
                return resp.json()
            logger.error("token exchange at %s failed: %d %s", token_endpoint, resp.status_code, resp.text[:300])
        except Exception:
            logger.exception("token exchange at %s failed", token_endpoint)
        return None

    async def _refresh_access_token(self, slug: str) -> str | None:
        oauth = self._oauth_states[slug]
        if not oauth.refresh_token or not oauth.client_id:
            return None
        spec = DCR_CONNECTORS_BY_SLUG[slug]
        metadata = await self._fetch_oauth_metadata(url=spec.oauth_metadata_url)
        if metadata is None:
            return None
        data = {
            "grant_type": "refresh_token",
            "client_id": oauth.client_id,
            "refresh_token": oauth.refresh_token,
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url=metadata["token_endpoint"], data=data)
            if resp.status_code == 200:
                token_data = resp.json()
                oauth.save_token(token_data=token_data)
                return token_data["access_token"]
            logger.error("refresh at %s failed: %d %s", metadata["token_endpoint"], resp.status_code, resp.text[:300])
        except Exception:
            logger.exception("token refresh failed for %s", slug)
        return None
