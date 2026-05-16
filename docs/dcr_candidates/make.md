# Make.com

`https://mcp.make.com` (and `https://<MAKE_ZONE>/mcp/...`)

## DCR status

- AS metadata `https://mcp.make.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://www.make.com/oauth/v2/register/mcp`. DCR live-tested.
- `scopes_supported = []`. Make defines coarse scopes (`scenarios:read`, `mcp:use`) at the OAuth consent screen, not RFC 7591 metadata.
- Two distinct paths: OAuth at `mcp.make.com`, or bearer MCP-token against `https://<MAKE_ZONE>/mcp/...`.

## Did Merge do a good job?

**Different shapes.** Merge ships **27 tools** for Make as `API token`. Make's first-party MCP has two static categories plus **dynamic scenario-tools** — your own active on-demand scenarios are exposed as callable MCP tools, with names/inputs/descriptions derived from the scenario configuration. The tool list is **per-token, per-active-scenario** — not a static catalog.

Merge's 27 tools are presumably wrappers around Make's REST management API. That's a reasonable mapping for a static catalog but misses the killer feature of Make's MCP: **scenarios-as-tools**.

## Would direct integration be advantageous?

**Yes — Make's scenarios-as-tools is genuinely novel.** If a customer has scenarios configured, the direct MCP turns them into MCP tools the LLM can call directly. Merge's REST wrapping cannot do this.

Caveat: the dynamism implies **per-session tool discovery**. Aggregator must NOT cache tool lists. This is even more dynamic than PostHog's session-config narrowing.

## Mechanisms beyond Notion

This is the **most dynamic vendor** in the audit.

- **Scenarios-as-tools.** User's active on-demand scenarios appear as MCP tools at session-list time. Tool lists vary per token. Aggregator must invalidate the catalog whenever the user reconnects.
- **First-class URL narrowing** for the bearer-token path:
  - `?organizationId=<id>` — restrict to one org
  - `?teamId=<id>` — restrict to one team
  - `?scenarioId=<id>` — restrict to one scenario (array form: `?scenarioId[]=<id1>&scenarioId[]=<id2>`)
  - `?maxToolNameLength=<32-160>` — caps tool name truncation (default 56)
  - **Levels are mutually exclusive.**
- **Async execution beyond MCP timeout.** Scenarios run for up to 40 minutes; the timeout response includes `executionId` + `scenarioId` so a follow-up tool call can fetch results. Requires `scenarios:read` scope. Stateful.
- **Multiple URL variants per zone:** `/stateless`, `/stream`, `/sse`. Aggregator must preserve the suffix verbatim.
- **Per-tool timeouts** vary by transport (25s/40s for scenario runs, 30s/60s/5m20s for management).
- Per-tenant Make zone affects the URL. Aggregator must let the customer specify zone.

## Effort

**High.** Like Datadog and Sentry, plan ~1 week:

- `connectors/make.py` with meta-tools to set zone, organization, team, scenario filters.
- Per-session config persisted; rebuild upstream URL on every catalog load and call.
- Disable tool-list caching for this connector — always re-fetch.
- Plumb async-execution support: when a scenario run hits the MCP timeout, surface `executionId` in the tool result so the agent knows it can poll.
- `default_scope = "scenarios:read mcp:use"` (request both at consent).

This is **the most novel candidate** in terms of patterns the aggregator doesn't currently handle (dynamic tool inventory + async execution).

## Sources

- https://developers.make.com/mcp-server/make-mcp-server.md
- https://developers.make.com/mcp-server/connect-using-oauth.md
- https://developers.make.com/mcp-server/connect-using-mcp-token/scenarios-as-tools-access-control.md
