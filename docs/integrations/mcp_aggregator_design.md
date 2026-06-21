# MCP Aggregator Design

How a Hermes Personal Assistant talks to MCP servers (Notion today; others tomorrow) on behalf of one user, without credentials entering the sandbox.

## Problem

MCP servers like Notion's (`mcp.notion.com/mcp`) require OAuth 2.1 authentication. Hermes Agent has built-in MCP support (`auth: oauth` in config) that can drive the OAuth flow directly — but that puts refresh tokens inside the sandbox at `/workspace/.hermes/auth.json`, breaking the custody invariant established by the integrations broker design.

## Solution

A thin MCP aggregator process runs in the parent container (outside the sandbox), managed by the existing supervisor alongside the integrations broker. The sandbox talks to it over HTTP on loopback with no credentials. The aggregator holds OAuth tokens, connects to upstream MCP servers, and proxies tool calls.

## Architecture

```
┌─ nono sandbox ──────────────────────┐
│                                     │
│  Hermes Agent                       │
│    mcp_servers:                     │
│      notion:                        │
│        url: http://127.0.0.1:9952/mcp│
│                                     │
│  (no credentials, no OAuth state)   │
└───────────────────┬─────────────────┘
                    │ HTTP (loopback, no TLS)
                    ▼
┌─ parent container ──────────────────┐
│                                     │
│  integrations_broker.py             │
│    ├── HTTPS proxy (:9950)          │
│    ├── broker control API (:9951)   │
│    └── MCP aggregator (:9952)  ◄─── new
│           │                         │
│           │ Streamable HTTP + Bearer│
│           ▼                         │
│     mcp.notion.com/mcp              │
│                                     │
│  Token storage (EBS-backed):        │
│    /hermes-persistent-root/         │
│      mcp-aggregator/                │
│        notion/client.json           │
│        notion/token.json            │
└─────────────────────────────────────┘
```

## Why not reuse the HTTPS MITM proxy

The integrations broker's MITM approach (CA + leaf certs + `HTTPS_PROXY` + `SSL_CERT_FILE`) works for REST APIs where a CLI or HTTP client sends requests through the proxy. MCP over Streamable HTTP could theoretically be proxied the same way.

We chose a dedicated MCP aggregator instead because:

- **Protocol awareness.** The aggregator speaks MCP (JSON-RPC) on both sides. It can send `notifications/tools/list_changed` downstream when providers connect/disconnect — something an opaque MITM proxy cannot do.
- **Lifecycle management.** The aggregator manages upstream MCP sessions (connect, discover, reconnect on token refresh) with proper state. An MITM proxy would need to understand MCP session semantics to avoid breaking mid-stream.
- **No TLS machinery.** Sandbox-to-aggregator is plain HTTP on loopback. No CA, no leaf certs, no `SSL_CERT_FILE` dependency for MCP.
- **Simpler for providers that don't use Bearer headers.** If a future MCP server authenticates differently, the aggregator handles it internally without needing new MITM rules.

The HTTPS MITM proxy continues to serve Google Workspace (where CLIs like `gws` make REST calls through `HTTPS_PROXY`).

## OAuth flow

Notion's MCP endpoint supports OAuth 2.1 with Dynamic Client Registration (DCR) and PKCE. No pre-registered `client_id` or `client_secret` needed.

### First-time connect

1. User clicks "Connect Notion" in the Hermes WebUI integrations pane.
2. Browser navigates to `https://hermes-<env>.<domain>/__mcp_aggregator/oauth/notion/start`.
3. WebUI reverse-proxy patch forwards to `127.0.0.1:9952/oauth/notion/start`.
4. Aggregator checks for cached DCR credentials in `notion/client.json`. If absent, calls Notion's `registration_endpoint` (discovered from `/.well-known/oauth-authorization-server`) to register a new public client with `redirect_uri = https://hermes-<env>.<domain>/__mcp_aggregator/oauth/callback`. Persists result to `client.json`.
5. Aggregator generates PKCE `code_verifier` + `code_challenge`, stores state, 302s browser to Notion's `authorization_endpoint`.
6. User consents on Notion.
7. Notion 302s browser to `https://hermes-<env>.<domain>/__mcp_aggregator/oauth/callback?code=...&state=...`.
8. Request passes through policy proxy (user is already authenticated — SSO cookie is still valid from the same browser session).
9. WebUI reverse-proxy forwards to `127.0.0.1:9952/oauth/callback`.
10. Aggregator exchanges code at Notion's `token_endpoint` with `code_verifier`. Receives `access_token` + `refresh_token` + `expires_in`. Persists to `token.json`.
11. Aggregator connects upstream to `mcp.notion.com/mcp` with the access token.
12. Aggregator calls upstream `tools/list`, caches tool schemas.
13. Aggregator sends `notifications/tools/list_changed` on its downstream MCP connection to Hermes.
14. Hermes re-discovers tools, registers them. Notion tools now visible to the LLM.
15. Aggregator 302s browser back to the integrations pane.

### Token refresh

Access tokens expire (~1h). When a tool call arrives and the cached token is expired:

1. Aggregator POSTs to Notion's `token_endpoint` with `grant_type=refresh_token`.
2. Receives new `access_token` + optionally rotated `refresh_token`. Updates `token.json`.
3. Closes current upstream FastMCP `Client` session.
4. Opens new `Client` session with fresh bearer.
5. Re-calls upstream `tools/list` (schemas may have changed).
6. If tool list changed, sends `notifications/tools/list_changed` downstream.

### Disconnect

User clicks "Disconnect Notion". Aggregator:
1. Revokes the refresh token at Notion (if revocation endpoint exists).
2. Deletes `token.json`.
3. Closes upstream session.
4. Sends `notifications/tools/list_changed` downstream (empty tool list).
5. Hermes deregisters Notion tools.

## Process model

The MCP aggregator is folded into `integrations_broker.py` as a new asyncio server on port 9952. Same process, separate concern. Rationale: fewer supervisor entries, one health endpoint, shared event loop. The risk of an MCP bug taking down Google is mitigated by `try/except` isolation around the MCP handler coroutines.

## What the aggregator exposes downstream

A standard MCP server (Streamable HTTP on `127.0.0.1:9952/mcp`) that:

- Returns empty `tools/list` when no providers are connected.
- Returns the union of all connected providers' tools when connected (prefixed by Hermes as `mcp_notion_<tool>` automatically).
- Forwards `call_tool` requests to the appropriate upstream provider.
- Sends `notifications/tools/list_changed` whenever the available tool set changes.

## What the aggregator exposes for the WebUI

The aggregator owns the OAuth handlers (DCR, callback, status, disconnect) but they're mounted by the integrations_broker's unified Starlette router on `127.0.0.1:9951` under `/__doh_broker/integrations/`, alongside Google's TLS-intercept controls and Merge's DOH-relay passthroughs. Port 9952 is sandbox-only MCP transport; the browser never reaches it.

`MCPAggregator.routes(prefix=...)` returns the Starlette routes the broker splats into its router:

- `GET /__doh_broker/integrations/mcp/<provider>/oauth/start?return_to=...` — initiate DCR + authorize.
- `GET /__doh_broker/integrations/mcp/<provider>/oauth/callback?code=...&state=...` — exchange code, store tokens.
- `POST /__doh_broker/integrations/mcp/<provider>/disconnect` — drop tokens, revoke, notify Hermes.

`GET /__doh_broker/integrations` returns a unified flat list of all integrations (TLS-intercept providers like Google + MCP-aggregator providers like Notion + Merge per-connector cards), each tagged with a `kind` discriminator the WebUI uses to dispatch the right click handlers.

The aggregator also exposes a second kind of upstream — `auth_kind="doh_relay"` — used for Merge.dev: instead of holding OAuth tokens directly, the ProxyProvider's `client_factory` builds a `StreamableHttpTransport` pointed at a DOH relay endpoint with `HUMR_ENV_BEARER` + identity headers attached. See `merge_integration_design.md`.

## Nono profile changes

Add port 9952 to `open_port`. No other nono changes — `HTTPS_PROXY` and `SSL_CERT_FILE` are unaffected; the sandbox talks to the aggregator via plain HTTP.

## Storage layout

```
/hermes-persistent-root/mcp-aggregator/
  notion/
    client.json    — DCR-issued client credentials (persists across reconnects)
    token.json     — refresh_token + last access_token + expiry
```

EBS-backed, survives container restarts. If `token.json` is missing on boot, provider is `not_connected`. If `client.json` is missing, DCR re-runs on next connect.

## Technology

- **Downstream MCP server:** FastMCP (Python). Handles protocol, schema serving, notifications.
- **Upstream MCP client:** FastMCP `Client`. Reconnects with fresh bearer on token refresh.
- **OAuth/DCR:** Custom code (~100 lines). Calls `.well-known` discovery, registration, authorize, token endpoints via `httpx`.
- **Folded into:** `integrations_broker.py`, sharing its asyncio event loop and supervisor lifecycle.

## Tool visibility to the LLM

- Not connected: LLM sees zero Notion tools. No "connect_notion" meta-tool.
- Connected: LLM sees all Notion tools with full schemas, auto-prefixed as `mcp_notion_<tool_name>` by Hermes.
- Disconnected mid-session: tools disappear after `notifications/tools/list_changed`.

## Adding future MCP providers

Adding a second DCR-capable MCP provider (e.g., Linear, Slack) requires:

1. A new entry in the aggregator's provider registry (URL, scopes, display label).
2. A new `<provider>/` directory under the storage layout.
3. A "Connect <Provider>" tile in the WebUI extension.

No new processes, no new ports, no new supervisor entries.

## Open questions

- **Providers without DCR.** If a future MCP server doesn't support dynamic client registration, we'd need to pre-register an OAuth app and store the client_id/secret as DOH-managed config pushed to the env. The aggregator would read it from a config file instead of running DCR. Not needed for Notion.
- **Multi-provider tool collisions.** If two MCP servers expose tools with the same name, Hermes's `mcp_<server>_<tool>` prefix prevents collisions as long as `mcp_servers:` keys are distinct. No action needed for v1.
- **SSE streaming responses.** If Notion's MCP server uses `text/event-stream` for long-running tool results, the aggregator needs to stream through rather than buffer. FastMCP handles this if we use its client; verify with a real Notion call.
- **Scope of Notion OAuth.** Confirm what scopes Notion requires/supports for MCP access. Likely workspace-level read/write.
