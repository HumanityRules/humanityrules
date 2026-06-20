# ClickUp

`https://mcp.clickup.com/mcp`

## DCR status

- AS metadata `https://mcp.clickup.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.clickup.com/oauth/register`. DCR live-tested.
- `scopes_supported` (2): `read`, `write`.
- **Public Beta.** "Custom clients must support OAuth 2.1 with PKCE."

**Allowlist-gated for redirect URIs.** "We maintain an allowlist of vetted MCP client redirect URIs to protect our users from malicious phishing attacks. Custom clients must submit a review form." This is the strictest gating among the candidates that aren't Vercel/Figma/Square.

## Did Merge do a good job?

**Yes for now.** Merge ships **46 tools**. ClickUp's first-party MCP exposes ~40 tools across 13 groups: search, task management, bulk, attachments, comments, tags, relationships, list moves, time tracking, workspace hierarchy, members, chat, docs, time-in-status reporting.

Notable absence on the direct MCP: deletion tools beyond `Delete task` are intentionally omitted ("we haven't added any deletion tools as a safety measure"). Merge probably has more deletion coverage.

## Would direct integration be advantageous?

**Blocked on allowlist.** Submitting a redirect URI for review delays integration and creates an external dependency. Until ClickUp lifts the allowlist or we go through their review process, Merge is the path.

If/when unblocked: marginal gain — direct gets us the safety-conscious tool set (no deletion footguns), Merge has the broader CRUD.

## Mechanisms beyond Notion

- **Redirect URI allowlist** — hard precondition.
- **Per-resource scopes (2).** `read`, `write`. Coarse but explicit; aggregator must request both.
- **Plan-conditional rate limits.** Free Forever: 50 calls/24h. Unlimited+: 300 calls/24h without "Everything AI" add-on; with add-on, falls back to public API limits.
- **No deletion tools** by design — annotate this expectation.
- No URL-level narrowing, no MCP-Apps widgets.

## Effort

**Blocked.** If unblocked: Notion-shape + `default_scope = "read write"` + a connector-card note about plan-conditional rate limits. Otherwise straightforward.

## Sources

- https://developer.clickup.com/docs/connect-an-ai-assistant-to-clickups-mcp-server
- https://developer.clickup.com/docs/mcp-tools
