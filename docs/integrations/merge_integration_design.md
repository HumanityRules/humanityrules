# Merge.dev Agent Handler Integration Design

How a Hermes Personal Assistant exposes ~150 third-party SaaS connectors (Slack, GitHub, Linear, …) without onboarding each one individually, and without putting any DOH-tenant-wide secret inside the customer's container.

## What Merge gives us

Merge.dev's "Agent Handler" product is an MCP server in front of a connector catalog. One DOH-tenant API key authenticates all calls; per-end-user identity is scoped via a "Registered User" passed in the URL path. End-user OAuth credentials are brokered by Merge — Merge holds Slack/GitHub/etc. tokens, our agent never sees them. A "Magic Link" flow hands the user off to a Merge-hosted page for connector authorization.

Concretely:

- **One MCP endpoint per (Tool Pack, Registered User) pair.** Tool Pack is a DOH-defined catalog of connectors; Registered User is a per-Hermes-app identity Merge stores OAuth grants under.
- **One Magic Link endpoint per connector** that mints a signed URL the user clicks to authenticate.
- **One DELETE endpoint per (user, connector)** to revoke a connection.
- **One catalog endpoint** listing the connectors in a Tool Pack (with display name, logo URL).

## The trust problem

Merge's API key authorizes operations across **every** Registered User in our Merge tenant. If it leaks, a holder can mint Magic Links, list connectors, revoke credentials, and call MCP tools on behalf of any DOH customer's app — by guessing or enumerating `origin_user_id` strings.

The Hermes container runs in the **customer's** AWS account. A customer admin with `ecs:ExecuteCommand` can read `/proc/<pid>/environ` on any process in the container, including the broker sidecar. So the Merge API key cannot live there.

This mirrors the Google integration's earlier-resolved problem: Google's OAuth `client_secret` is a tenant-wide secret too. The solution is the same — keep the secret on DOH's control plane, and have the broker reach Merge through DOH-side endpoints authenticated with `DOH_ENV_BEARER`.

## Architecture

```
┌─ nono sandbox ─────────────────────────────┐
│                                            │
│  Hermes Agent                              │
│    mcp_servers:                            │
│      notion: http://127.0.0.1:9952/mcp     │ (existing)
│      merge*: same /mcp endpoint            │ (* tools surface alongside Notion)
└──────────────────┬─────────────────────────┘
                   │ plain HTTP, loopback
                   ▼
┌─ parent container (integrations_broker) ───┐
│                                            │
│  /mcp on :9952 (FastMCP aggregator)        │
│    Notion ProxyProvider — direct OAuth     │
│    Merge  ProxyProvider — DOH-relayed:     │
│      url = $DOH_CONTROL_PLANE/api/         │
│            integrations/merge/mcp          │
│      headers:                              │
│        Authorization: Bearer DOH_ENV_BEARER│
│        X-Doh-App-Slug: <slug>              │
│        X-Doh-Owner-Username: <username>    │
│                                            │
│  /__doh_broker/integrations/merge/* on :9951│
│    link-token, connector-status, connectors,│
│    disconnect — all forward to DOH         │
└──────────────────┬─────────────────────────┘
                   │ HTTPS (env bearer)
                   ▼
┌─ DOH control plane ────────────────────────┐
│                                            │
│  /api/integrations/merge/*                 │
│    validates DOH_ENV_BEARER                │
│    derives origin_user_id =                │
│      f"doh_{user.pk}_{app_slug}"           │
│    attaches MERGE_AGENT_HANDLER_API_KEY    │
│    forwards to Merge                       │
│                                            │
│  Settings (DOH-only):                      │
│    MERGE_AGENT_HANDLER_API_KEY             │
│    MERGE_TOOL_PACK_ID                      │
└──────────────────┬─────────────────────────┘
                   │ HTTPS (Merge API key)
                   ▼
              ah-api.merge.dev
```

Three internet hops on the customer side (sandbox→aggregator is loopback, sub-ms). The DOH hop is the price of keeping the Merge API key off customer infrastructure. Same trade-off Google's design already makes for token refresh.

## Identity: `origin_user_id = f"doh_{user.pk}_{app_slug}"`

Per-Hermes-app, not per-DOH-user. Each agent gets its own connector credentials.

- **Same user destroys/recreates the same slug → integrations carry over.** Merge keeps the Registered User; the new container connects on next boot. Familiar redeploy semantics.
- **Different user takes over the slug → fresh slate.** Different `user.pk` produces a different `origin_user_id`, so Merge issues a new Registered User. The old user's grants don't leak to the new owner.
- **Renames are disallowed at the App level**, so the slug is stable for the app's lifetime.

DOH derives `origin_user_id` server-side from authenticated state (the env bearer's resolved environment + `app_slug` from the request). The broker has no way to forge a different identity — it can only act for its own (env-bearer-resolved) app.

## Endpoints (DOH side)

All under `/api/integrations/merge/`:

| Endpoint | Method | Purpose |
|---|---|---|
| `ensure-registered-user` | POST | Idempotent boot-time registration with Merge. |
| `link-token` | POST | Mint Magic Link URL for one connector. |
| `connectors` | GET | Tool Pack catalog merged with this user's connection state. |
| `connector-status` | GET | Per-connector state for active-connect polling. |
| `disconnect` | POST | Revoke credentials for one connector. |
| `mcp` | POST | Streaming-HTTP MCP relay. |

Identity (`app_slug`, `owner_username`) is read first from `X-Doh-App-Slug` / `X-Doh-Owner-Username` headers (the MCP relay path, where the body is the JSON-RPC payload), then from query string (GET) or JSON body (POST) for the other endpoints. Bearer always in `Authorization`.

Each endpoint is a narrow, validated surface. None forwards arbitrary `{method, path, body}` to Merge. URL path components (`tool_pack_id`, `registered_user_id`) are constructed server-side from authenticated state, never injected by the caller.

## Surprises uncovered against the live API

Verified against `ah-api.merge.dev` during integration:

- **`POST /registered-users/` is NOT idempotent** — duplicates return HTTP 400 with `non_field_errors: ["User of origin_id: ... already exists. ... PATCH /registered-users/<UUID>"]`. We parse the UUID out via regex and treat the call as successful. The response field for the create path is `id`, not `registered_user_id`.
- **Per-user connection state is not a separate endpoint** — it's an `authenticated_connectors: [<slug>, ...]` array embedded in the Registered User record (`GET /registered-users/{id}/`).
- **Link-token field is `connector`** (not `connector_slug`); endpoint path needs the trailing slash; success status is 201.
- **Disconnect is `DELETE /api/v1/credentials/registered-users/{rid}/connectors/{slug}/`** — not on the Registered User record itself.
- **MCP endpoint needs the trailing slash** (`/mcp/`) or it 404s. Returns `text/event-stream` with `Mcp-Session-Id` in headers.

## Frontend: Magic Link with no `callback_url`

We mint Magic Links **without** a `callback_url`. Reason: each customer's Hermes container lives at `<app-slug>.<customer-hosted-zone>`, and Merge's "Allowed callback origins" list is global to the Merge tenant. We'd need either a wildcard pattern Merge supports, or per-customer registration at deploy time — neither viable. Without `callback_url`, Merge lands the user on its hosted "authentication complete" page; the user closes the tab manually.

The WebUI integrations pane handles this with a two-stage modal:

1. **Explainer modal.** "You'll authenticate in a new tab via Merge.dev. Close the tab when Merge says you're done — your integrations list will refresh automatically." Mints the Magic Link only on Continue (preserves the 30-min single-use window).
2. **Waiting modal.** Polls `connector-status?slug=<connector>` every 3 seconds with a 5-minute cap. Closes on `connected`.

The narrow per-connector status endpoint (vs. fetching the full catalog every 3s) keeps polling load bounded.

## Reauth flow (Path α)

When a Slack OAuth token expires mid-conversation, Merge returns an `authenticate_meta` payload **inside the MCP tool response** containing a fresh `magic_link_url` and a `message` field "written for the model to relay to the user verbatim." The agent surfaces the link in chat; the user clicks, completes reauth in a new tab, and retries.

We don't intercept or rewrite this — the response flows through the relay untouched. No DOH or broker special-casing. The polling logic in the integrations pane is unrelated; it's just for the initial-connect flow.

## Boot failure modes

The Merge ProxyProvider attaches **unconditionally** at aggregator boot. If DOH or Merge is down, individual MCP calls fail with errors the agent can relay. This mirrors how the Notion ProxyProvider behaves once OAuth is established — failure is per-call, not per-attach.

The broker fires one `ensure-registered-user` call at boot as fire-and-forget. Failure is non-fatal; subsequent operations don't depend on the boot call having succeeded (Merge's "duplicate user" path returns the existing UUID, so any later DOH endpoint can re-ensure on demand).

## What lives where

- **`MERGE_AGENT_HANDLER_API_KEY`, `MERGE_TOOL_PACK_ID`** — DOH settings, env-driven. Never injected into customer containers.
- **`origin_user_id`** — derived per request from `(env.aws_account.organization, app_slug, user.pk)`. Not stored.
- **`registered_user_id`** — Merge's UUID for our Registered User. Not stored on DOH; Merge's create-or-409-with-UUID flow makes per-call resolution cheap. The aggregator does cache it in process memory after the boot ensure call.
- **Connector credentials** — Merge holds them; we never see them.
- **Tool Pack contents** — Merge dashboard. One global Tool Pack for all DOH customers. Per-org Tool Packs are deferred until a customer asks for it.

## Single Tool Pack today; per-org later

We use one Merge Tool Pack for all DOH customers. Cross-tenant security comes from per-app `origin_user_id`, not from per-tenant Tool Packs. A customer's CIO who wants to disable a connector can't do it today — when that need arrives, we'll add a `merge_tool_pack_id` column on `Organization` and let DOH ops provision per-org Tool Packs. Migration is straightforward; the URL construction in the MCP relay already happens server-side.

## What the broker's env contract gains

One new variable: `DOH_APP_SLUG`, injected by `deploy_app.py`'s env-bearer overlay alongside the existing `DOH_OWNER_USERNAME`, `DOH_ENV_BEARER`, etc. Used to construct the `X-Doh-App-Slug` header on every Merge-bound call.

No new secrets in the customer container.

## Adding a new third-party MCP via Merge

Once Merge supports the connector (their team's job), exposing it to Hermes agents is:

1. Add the connector to our Tool Pack on Merge's dashboard.
2. The catalog endpoint (`/connectors`) picks it up automatically; the WebUI integrations pane gains a new card on next pane open.
3. The MCP relay's `tools/list` surfaces the new tools after the next reconnect.

No DOH code change, no broker code change, no deploy.

## Open questions

- **Tool Pack drift mid-session.** When DOH ops add a connector to the Tool Pack, running Hermes containers don't re-list MCP tools until the agent's MCP session reconnects. Fine for first cut; if it becomes annoying, we can plumb `notifications/tools/list_changed` from Merge through the relay.
- **Cleanup on app destroy.** Currently we leave orphaned Registered Users on Merge when an app is destroyed. Acceptable for now — if it ever matters, add a teardown call in the app-removal flow.
- **DOH availability dependency.** Merge tools fail when DOH is unreachable, even if Merge is up. This is the cost of keeping the API key off customer infrastructure. Acceptable; same shape as the Google refresh dependency.
- **Per-org Tool Pack provisioning.** As above — design when needed.
