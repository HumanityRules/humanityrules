# Klaviyo

`https://mcp.klaviyo.com/mcp`

## DCR status

- AS metadata `https://mcp.klaviyo.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.klaviyo.com/register`. DCR live-tested.
- `scopes_supported = []`. Coarse — access gated by Klaviyo's own RBAC: Owner, Admin, or Manager only.
- `token_endpoint_auth_methods`: `client_secret_basic`, `client_secret_post`, `none` (public clients OK).

GA. Listed in Claude Connectors and ChatGPT custom MCP catalog.

## Did Merge do a good job?

**Yes.** Merge ships **48 tools** for Klaviyo (`OAuth or API key`). Klaviyo's first-party MCP doesn't enumerate tool names verbatim, but the local server (which mirrors the remote surface) covers ~20+ tools across accounts, campaigns, catalogs, events, flows, images, lists, metrics, profiles, segments, subscriptions, tags, templates, translations.

Merge has wider coverage on individual tools.

## Would direct integration be advantageous?

**Yes, modestly — Klaviyo has session-narrowing knobs that look very PostHog-like.** Direct gets us:

1. **OAuth via DCR.**
2. **`?read-only=true`** — disables write tools server-side.
3. **`?disable-tools-with-user-generated-content=true`** — hides tools that surface UGC.
4. **`?company=<slug>`** — disambiguates accounts when the same user has multiple.

These three URL knobs match the PostHog session-config pattern. Aggregator already handles that pattern; this would be a small per-vendor variation.

## Mechanisms beyond Notion

- **`?read-only=`** — boolean URL knob.
- **`?disable-tools-with-user-generated-content=`** — boolean URL knob.
- **`?company=`** — multi-account disambiguation. Each connector instance binds to one account.
- No scope strings.
- No region routing.
- Role-gated server-side (Owner/Admin/Manager only) — aggregator doesn't need to do anything; non-qualifying users get 403.

## Effort

**Medium.** Notion-shape + per-session config + 2-3 meta-tools:

- `connectors/klaviyo.py` with two boolean toggles and a `company` field in per-session config.
- Three meta-tools: `klaviyo-set-read-only`, `klaviyo-set-block-ugc`, `klaviyo-set-company`.
- Rebuild upstream URL on every catalog load and call.

This is the third PostHog-shaped target after Datadog and Make, but lighter — only 2-3 toggles instead of 30+ feature categories.

## Sources

- AS metadata: https://mcp.klaviyo.com/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.klaviyo.com/.well-known/oauth-protected-resource
- Docs: https://developers.klaviyo.com/en/docs/klaviyo_mcp_server
