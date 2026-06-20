# Contentful

`https://mcp.contentful.com/mcp`

## DCR status

- AS metadata `https://mcp.contentful.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.contentful.com/register`. DCR live-tested.
- `scopes_supported = []`. Coarse "everything your user can do" model — same shape as Notion.
- `client_id_metadata_document_supported: false` (no MCP-Apps CIMD).

## Did Merge do a good job?

**Comparable.** Merge ships **45 tools**. Contentful's first-party MCP doesn't enumerate a tool list on the remote-server splash page; the closest authoritative source is `@contentful/mcp-server` (the local server backing the same product) which exposes ~44 tools across:

- Context: `get_initial_context`
- Content Types (8)
- Entries (7)
- Assets (7)
- Spaces & Environments (5)
- Locales (5)
- Tags (2)
- AI Actions (9): `create`, `invoke`, `get_invocation`, `get`, `list`, `update`, `publish`, `unpublish`, `delete`

Both ~comparable in tool count.

## Would direct integration be advantageous?

**Modest.** AI Actions — Contentful's first-class CRUD over reusable AI workflows — is a unique feature. Direct gets us OAuth instead of personal access token (Merge's `OAuth or personal access token`). Beyond that, it's a wash.

## Mechanisms beyond Notion

Almost nothing extra:

- No scope strings.
- No URL-level narrowing.
- Space and environment passed per tool call (the local server requires env vars; the remote tool schemas accept them as args).
- AI Actions are a Contentful-specific concept but they're just tools, no special handling.

## Effort

**Straightforward.** Pure Notion-shape connector. Drop in `connectors/contentful.py`, no scopes, no per-session config.

## Sources

- AS metadata: https://mcp.contentful.com/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.contentful.com/.well-known/oauth-protected-resource
- Tool list (sibling local server): https://github.com/contentful/contentful-mcp
