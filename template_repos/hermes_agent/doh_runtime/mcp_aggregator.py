"""MCP Aggregator — proxies upstream MCP servers with DOH-managed OAuth.

Architecture: a single FastMCP server exposes /mcp to the sandbox on
127.0.0.1:9952. Per-provider ProxyProviders are added/removed as OAuth
completes/disconnects; each holds a client_factory that builds a Client with
the current access token, so token refresh is automatic.

The sandbox is the only thing talking to /mcp — it does so over plain HTTP on
loopback. Browser-facing integration management (OAuth start/callback, status,
disconnect) is implemented here too, on the same MCPAggregator class that owns
the per-provider state. The integrations_broker exposes those handlers under
its unified /__doh_broker/* router by calling MCPAggregator.routes(prefix=...);
the aggregator owns its own URL surface, the broker owns the mount point.

Custom code is limited to OAuth/DCR/PKCE and token storage — the MCP protocol
on both sides is FastMCP's job.
"""

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx
import uvicorn
from fastmcp import Client, FastMCP
from fastmcp.server.providers.proxy import ProxyProvider
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route


logger = logging.getLogger("mcp_aggregator")

DEFAULT_MCP_PORT = 9952

MCP_PROVIDERS: dict[str, dict] = {
    "notion": {
        "label": "Notion",
        "auth_kind": "oauth_dcr_pkce",
        "upstream_url": "https://mcp.notion.com/mcp",
        "oauth_metadata_url": "https://mcp.notion.com/.well-known/oauth-authorization-server",
    },
    "merge": {
        "label": "Merge",
        "auth_kind": "doh_relay",
        # The relay URL is constructed against DOH_CONTROL_PLANE_URL at attach time.
        "doh_relay_path": "/api/integrations/merge/mcp",
    },
}


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
    """One per Hermes container. Hosts a FastMCP server with per-provider proxies."""

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
        self._provider_attached: dict[str, ProxyProvider] = {}
        self._pending_oauth: dict[str, dict] = {}
        for slug, cfg in MCP_PROVIDERS.items():
            if cfg.get("auth_kind") == "oauth_dcr_pkce":
                self._oauth_states[slug] = _OAuthState(provider_dir=persistent_dir / slug)

    async def serve(self) -> None:
        for slug, cfg in MCP_PROVIDERS.items():
            kind = cfg.get("auth_kind")
            if kind == "oauth_dcr_pkce" and self._oauth_states[slug].has_token:
                self._attach_provider(slug=slug)
            elif kind == "doh_relay":
                # Always attach the DOH relay; per-call failures surface naturally.
                self._attach_provider(slug=slug)

        # Best-effort registration with Merge at boot. Failure is non-fatal —
        # individual MCP/connector calls will surface errors if DOH is unreachable.
        await self._ensure_merge_registered_user()

        mcp_app = self._mcp.http_app(path="/mcp", transport="streamable-http")
        config = uvicorn.Config(app=mcp_app, host="127.0.0.1", port=self._port, log_level="warning", access_log=False)
        server = uvicorn.Server(config=config)
        # The broker process owns signal handling; uvicorn must not install its own.
        server.install_signal_handlers = lambda: None
        logger.info("MCP aggregator listening on 127.0.0.1:%d", self._port)
        await server.serve()

    def routes(self, prefix: str) -> list[Route]:
        """Routes for the integrations_broker to mount under its unified /__doh_broker/* router."""
        return [
            Route(path=f"{prefix}/{{provider}}/oauth/start", endpoint=self.handle_oauth_start, methods=["GET"]),
            Route(path=f"{prefix}/{{provider}}/oauth/callback", endpoint=self.handle_oauth_callback, methods=["GET"]),
            Route(path=f"{prefix}/{{provider}}/disconnect", endpoint=self.handle_disconnect, methods=["POST"]),
            # Merge — DOH-relayed; the WebUI talks to DOH through these passthroughs.
            Route(path=f"{prefix}/merge/connectors", endpoint=self.handle_merge_connectors, methods=["GET"]),
            Route(path=f"{prefix}/merge/connector-status", endpoint=self.handle_merge_connector_status, methods=["GET"]),
            Route(path=f"{prefix}/merge/link-token", endpoint=self.handle_merge_link_token, methods=["POST"]),
            Route(path=f"{prefix}/merge/disconnect", endpoint=self.handle_merge_disconnect, methods=["POST"]),
        ]

    async def _ensure_merge_registered_user(self) -> None:
        """Idempotent boot-time registration with Merge via DOH passthrough."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url=f"{self._doh_control_plane_url}/api/integrations/merge/ensure-registered-user",
                    headers={
                        "Authorization": f"Bearer {self._doh_env_bearer}",
                        "Content-Type": "application/json",
                    },
                    json={"app_slug": self._doh_app_slug, "owner_username": self._doh_owner_username},
                )
            if resp.status_code != 200:
                logger.error("merge ensure-registered-user at boot returned %d: %s", resp.status_code, resp.text[:300])
                return
            logger.info("merge ensure-registered-user OK at boot")
        except Exception:
            logger.exception("merge ensure-registered-user at boot failed (continuing)")

    # ── Provider attach / detach ─────────────────────────────────────

    def _attach_provider(self, slug: str) -> None:
        """Add a ProxyProvider for this slug. Idempotent — replaces any existing one."""
        cfg = MCP_PROVIDERS[slug]
        if slug in self._provider_attached:
            self._detach_provider(slug=slug)
        kind = cfg.get("auth_kind")

        if kind == "oauth_dcr_pkce":
            url = cfg["upstream_url"]

            async def factory() -> Client:
                token = await self._current_access_token(slug=slug)
                if token is None:
                    raise RuntimeError(f"{slug} has no valid token")
                return Client(url, auth=token)
        elif kind == "doh_relay":
            url = self._doh_control_plane_url + cfg["doh_relay_path"]
            bearer = self._doh_env_bearer

            async def factory() -> Client:
                return Client(url, headers={"Authorization": f"Bearer {bearer}"})
        else:
            raise RuntimeError(f"unknown auth_kind for {slug}: {kind!r}")

        provider = ProxyProvider(client_factory=factory)
        self._mcp.add_provider(provider=provider)
        self._provider_attached[slug] = provider
        logger.info("attached provider %s (kind=%s) -> %s", slug, kind, url)

    def _detach_provider(self, slug: str) -> None:
        provider = self._provider_attached.pop(slug, None)
        if provider is None:
            return
        try:
            self._mcp.providers.remove(provider)
        except ValueError:
            pass
        logger.info("detached provider %s", slug)

    async def _current_access_token(self, slug: str) -> str | None:
        oauth = self._oauth_states[slug]
        token = oauth.access_token
        if token is not None:
            return token
        return await self._refresh_access_token(slug=slug)

    # ── Starlette endpoints (mounted by integrations_broker on port 9951) ─

    async def handle_status(self, request: Request) -> Response:
        """Return per-OAuth-provider connection state for the integrations pane.

        Excludes DOH-relay providers (Merge) — those are surfaced as per-connector
        cards via the broker's unified /integrations endpoint, which calls
        handle_merge_connectors directly.
        """
        providers: dict[str, dict] = {}
        for slug, cfg in MCP_PROVIDERS.items():
            if cfg.get("auth_kind") != "oauth_dcr_pkce":
                continue
            oauth = self._oauth_states[slug]
            if slug in self._provider_attached:
                providers[slug] = {"label": cfg["label"], "status": "connected"}
            elif oauth.has_token:
                providers[slug] = {"label": cfg["label"], "status": "token_expired"}
            else:
                providers[slug] = {"label": cfg["label"], "status": "not_connected"}
        return JSONResponse(content={"providers": providers})

    async def handle_disconnect(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        cfg = MCP_PROVIDERS.get(provider)
        if cfg is None:
            return JSONResponse(content={"error": "unknown provider"}, status_code=404)
        if cfg.get("auth_kind") != "oauth_dcr_pkce":
            return JSONResponse(
                content={"error": f"{provider} disconnect must use the connector-specific endpoint"},
                status_code=400,
            )
        self._detach_provider(slug=provider)
        self._oauth_states[provider].clear_token()
        self._oauth_states[provider].clear_client()
        return JSONResponse(content={"ok": True})

    async def handle_oauth_start(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        cfg = MCP_PROVIDERS.get(provider)
        if cfg is None or cfg.get("auth_kind") != "oauth_dcr_pkce":
            return Response(content="unknown OAuth provider", status_code=404)

        return_to = request.query_params.get("return_to", "/")
        origin = request.query_params.get("origin", "") or self._public_base_url or ""
        if not origin:
            return Response(content="origin query param required on first connect", status_code=400)
        if not self._public_base_url:
            self._public_base_url = origin
        redirect_uri = origin + f"/__doh_broker/integrations/{provider}/oauth/callback"
        oauth = self._oauth_states[provider]

        metadata = await self._fetch_oauth_metadata(url=cfg["oauth_metadata_url"])
        if metadata is None:
            return Response(content="failed to fetch OAuth metadata", status_code=502)

        if not oauth.has_client or oauth.registered_redirect_uri != redirect_uri:
            registration_endpoint = metadata.get("registration_endpoint")
            if not registration_endpoint:
                return Response(content="provider does not support DCR", status_code=502)
            client_data = await self._register_client(
                registration_endpoint=registration_endpoint,
                redirect_uri=redirect_uri,
                provider_label=cfg["label"],
            )
            if client_data is None:
                return Response(content="DCR registration failed", status_code=502)
            oauth.save_client(client_data=client_data, redirect_uri=redirect_uri)

        code_verifier = secrets.token_urlsafe(64)
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(32)
        self._pending_oauth[state] = {"provider": provider, "code_verifier": code_verifier, "return_to": return_to}

        params = urllib.parse.urlencode({
            "client_id": oauth.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        })
        return RedirectResponse(url=f"{metadata['authorization_endpoint']}?{params}", status_code=302)

    async def handle_oauth_callback(self, request: Request) -> Response:
        provider = request.path_params.get("provider", "")
        cfg = MCP_PROVIDERS.get(provider)
        if cfg is None or cfg.get("auth_kind") != "oauth_dcr_pkce":
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

        metadata = await self._fetch_oauth_metadata(url=cfg["oauth_metadata_url"])
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
        self._attach_provider(slug=provider)

        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(url=f"{return_to}{separator}connected={provider}", status_code=302)

    # ── Merge.dev passthroughs (DOH owns the API key; we just forward) ─

    async def handle_merge_connectors(self, request: Request) -> Response:
        return await self._merge_passthrough(method="GET", path="/api/integrations/merge/connectors")

    async def handle_merge_connector_status(self, request: Request) -> Response:
        connector_slug = request.query_params.get("connector_slug", "")
        if not connector_slug:
            return JSONResponse(content={"error": "connector_slug is required"}, status_code=400)
        return await self._merge_passthrough(
            method="GET",
            path="/api/integrations/merge/connector-status",
            extra_query={"connector_slug": connector_slug},
        )

    async def handle_merge_link_token(self, request: Request) -> Response:
        try:
            payload = json.loads((await request.body()).decode() or "{}")
        except json.JSONDecodeError:
            return JSONResponse(content={"error": "invalid JSON body"}, status_code=400)
        body: dict = {}
        if "connector_slug" in payload:
            body["connector_slug"] = payload["connector_slug"]
        return await self._merge_passthrough(
            method="POST", path="/api/integrations/merge/link-token", json_body=body,
        )

    async def handle_merge_disconnect(self, request: Request) -> Response:
        try:
            payload = json.loads((await request.body()).decode() or "{}")
        except json.JSONDecodeError:
            return JSONResponse(content={"error": "invalid JSON body"}, status_code=400)
        connector_slug = payload.get("connector_slug", "")
        if not isinstance(connector_slug, str) or not connector_slug:
            return JSONResponse(content={"error": "connector_slug is required"}, status_code=400)
        return await self._merge_passthrough(
            method="POST",
            path="/api/integrations/merge/disconnect",
            json_body={"connector_slug": connector_slug},
        )

    async def _merge_passthrough(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        extra_query: dict | None = None,
    ) -> Response:
        """Forward to DOH with the env bearer attached. DOH does the real work.

        app_slug and owner_username are auto-injected — into the query string
        for GET, into the JSON body for POST. Callers provide only the
        operation-specific fields.
        """
        url = f"{self._doh_control_plane_url}{path}"
        headers = {"Authorization": f"Bearer {self._doh_env_bearer}"}
        identity = {"app_slug": self._doh_app_slug, "owner_username": self._doh_owner_username}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "GET":
                    params = {**identity, **(extra_query or {})}
                    resp = await client.get(url=url, headers=headers, params=params)
                else:
                    body = {**identity, **(json_body or {})}
                    resp = await client.request(method=method, url=url, headers=headers, json=body)
        except Exception as exc:
            logger.exception("merge passthrough %s %s failed", method, path)
            return JSONResponse(content={"error": f"doh unreachable: {exc}"}, status_code=502)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("Content-Type", "application/json"),
        )

    # ── OAuth helpers ───────────────────────────────────────────────

    async def _fetch_oauth_metadata(self, url: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url=url)
            if resp.status_code == 200:
                return resp.json()
            logger.error("OAuth metadata %s returned %d", url, resp.status_code)
        except Exception:
            logger.exception("OAuth metadata fetch failed for %s", url)
        return None

    async def _register_client(self, registration_endpoint: str, redirect_uri: str, provider_label: str) -> dict | None:
        body = {
            "client_name": f"DOH Hermes - {provider_label}",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
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
        cfg = MCP_PROVIDERS[slug]
        metadata = await self._fetch_oauth_metadata(url=cfg["oauth_metadata_url"])
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
