# Asana

`https://mcp.asana.com/sse` **— deprecated, shutdown 2026-05-11 (already past).**
**New endpoint: `https://mcp.asana.com/v2/mcp`.**

## DCR status

- AS metadata `https://mcp.asana.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.asana.com/register`. DCR live-tested, returns a working `client_id`.
- `scopes_supported = []`. Server-validated; Asana explicitly states "MCP apps in Asana do not use permission scopes."

## Did Merge do a good job?

**Yes — Merge has more tools (51) than Asana's first-party V2 MCP (~25).** Merge's surface covers project portfolios, custom fields and bulk operations the direct MCP doesn't surface yet. The direct server is leaner by design.

The direct server has three **interactive preview tools** (`create_task_preview`, `create_project_preview`, `search_tasks_preview`) that render a host-side widget in Claude or ChatGPT. Outside those two clients these are no-ops.

## Would direct integration be advantageous?

**Marginal.** Merge wins on tool count and breadth. The direct path is only better if we want:

- The interactive preview widgets (and we're running in a host that supports them).
- A leaner, less-prompt-bloating tool list.
- Independence from Merge's tool naming.

We could ship both — direct first, with Merge as the wider fallback for power-user features.

## Mechanisms beyond Notion

Two minor wrinkles, no scope work needed:

- **Workspace pinned at OAuth consent.** Sticky for the session. The V2 server **dropped the `workspaceGid` parameter** from many tools because it's now implicit. To switch workspace, re-auth.
- **MCP token is MCP-server-only.** Cannot be reused against Asana's standard REST API.
- **Preview tools** require host-side widget support. Ignore on Hermes for now — they degrade gracefully.
- **Premium gating on `search_tasks`.** The tool exists but only fires for paid Asana tenants.

No `?features=` / `?tools=` URL params. No region routing. No scope strings.

## Effort

**Straightforward.** Same shape as Notion: drop in `connectors/asana.py`, no scopes, no per-session config. The only thing to remember is to pin the new `/v2/mcp` endpoint, since the legacy `/sse` URL Merge probably still uses internally has already been turned off.

## Sources

- https://developers.asana.com/docs/using-asanas-mcp-server
- https://developers.asana.com/docs/mcp-tools-reference
