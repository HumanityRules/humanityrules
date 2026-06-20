# Linear

`https://mcp.linear.app/mcp`

## DCR status

- AS metadata `https://mcp.linear.app/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.linear.app/register`. **DCR live-tested**, returns a working `client_id`.
- `scopes_supported = []`. Coarse OAuth grant — server validates internally, the same shape as Notion.

## Did Merge do a good job?

**Yes — and they expose OAuth alongside an API-key fallback (`OAuth or API key`).** The connector ships **32 tools**, narrower than what's possible against the live MCP server but wide enough for typical CRUD on issues/projects/comments. Linear's own docs do not publish a full enumerated tool list — Merge's is more transparent than Linear's.

Where Linear's MCP wins: tool-name churn handled upstream (Linear consolidated `create_issue`/`update_issue` into a single `save_issue` in Feb 2026), and continuous additions through the changelog.

## Would direct integration be advantageous?

**Modest yes.** The direct path gives us live tool-list updates as Linear evolves, and a slightly bigger surface (likely 15-25 tools today, growing). Merge's 32 tools may already include things Linear's MCP doesn't surface, so this is a wash on breadth. The real argument for direct is uniformity with the rest of the DCR pipeline, not a feature gap.

## Mechanisms beyond Notion

None — this is the cleanest possible Notion-shape integration:

- No scope strings to negotiate.
- No `?features=`/`?tools=` URL knobs.
- Workspace picked at OAuth consent and sticky for the session. No `switch_workspace` tool.
- One quirk: as of May 14 2026, "unknown tool parameters return a validation error instead of being silently dropped." Worth pinning a contract test.
- Codex requires `experimental_use_rmcp_client = true`; WSL needs `--transport sse-only`. Not relevant to our aggregator.

## Effort

**Straightforward.** Drop in a `connectors/linear.py` mirroring `connectors/notion.py`. No `default_scope`, no per-session config, no meta-tools. Add to `DCR_CONNECTORS`. Done.

## Sources

- https://linear.app/docs/mcp
- https://linear.app/changelog (filter for MCP entries)
