# Figma

`https://mcp.figma.com/mcp`

## DCR status

- AS metadata `https://mcp.figma.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://api.figma.com/v1/oauth/mcp/register`.
- **Live-tested DCR returns 403 Forbidden.** This is the Vercel pattern: DCR is advertised but gated behind Figma's MCP Catalog. New clients join via an Asana-form waitlist.
- `scopes_supported = ["mcp:connect"]`.

Status: public, write features (`use_figma`, `generate_diagram`) **beta**; some tools (`generate_figma_design`) are "select clients only."

## Did Merge do a good job?

**Yes, for what's possible.** Merge ships **27 tools** for Figma. Figma's first-party MCP exposes ~16 tools focused on design context, code-connect, and write-to-canvas:

- File & workspace: `create_new_file`, `whoami`
- Design context / inspection: `get_design_context`, `get_metadata`, `get_screenshot`, `get_variable_defs`, `get_figjam`
- Design system / libraries: `get_libraries`, `search_design_system`
- Code Connect: `add_code_connect_map`, `get_code_connect_map`, `get_code_connect_suggestions`, `send_code_connect_mappings`, `get_context_for_code_connect`
- Authoring: `use_figma`, `generate_figma_design`, `generate_diagram`, `upload_assets`

Merge's 27 tools are different in shape — more REST CRUD over Figma's classic API (files, comments, projects). Direct MCP gives access to features Merge cannot replicate (writing to canvas, code-connect mappings).

## Would direct integration be advantageous?

**Probably yes, but blocked.** The direct path would unlock features Merge lacks (canvas authoring, design-context extraction). The blocker is the same as Vercel and Square: **allowlist gating.** We'd have to apply to Figma's MCP Catalog and wait for approval.

If we hit the catalog, the integration is medium effort because of plan-conditional tool visibility (see below).

## Mechanisms beyond Notion

- **Client allowlist (catalog) gating.** Hard precondition before any customer can connect.
- **Plan- and seat-aware rate limits baked into the server.** View/Collab seats: 6 calls/month. Dev/Full: 200/day on Starter, up to 600/day, 20/min on Enterprise. Some write tools exempt. Our connector spec doesn't currently surface "rate limit" — the agent just hits a 429 and we should propagate that signal cleanly.
- **Beta = paid later.** `use_figma`, `generate_diagram` will become usage-based paid features after beta. Connector description should call this out.
- **Tool exposure is plan-conditional** (some tools effectively hidden if the seat doesn't have access). Aggregator must NOT cache tool lists across users on this connector.
- **Per-tool argument-level scoping** on `get_code_connect_map` (`clientFrameworks`, `clientLanguages`). Per-call, not per-session.

## Effort

**Blocked on Figma's catalog.** If/when unblocked: ~Medium, similar to PostHog, because of the dynamic tool list per user (plan-conditional visibility) and the rate-limit handling.

## Sources

- https://developers.figma.com/docs/figma-mcp-server/
- https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/
- https://developers.figma.com/docs/figma-mcp-server/rate-limits-access/
- https://www.figma.com/mcp-catalog/
