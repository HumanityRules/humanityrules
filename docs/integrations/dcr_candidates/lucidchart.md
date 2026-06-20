# Lucidchart / Lucid

`https://mcp.lucid.app/mcp`

## DCR status

- AS metadata `https://mcp.lucid.app/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.lucid.app/oauth/register`. DCR live-tested.
- `scopes_supported = []` (empty array).
- PKCE `S256` only (stricter than peers, who allow `plain` too).
- Token auth: `client_secret_post`, `none`.

## Did Merge do a good job?

**Unknown — we can't compare.** Merge ships **30 tools** as `OAuth or API key`. Lucid does not publish a tool list for the product MCP at `mcp.lucid.app`. The Lucid docs site (`lucid.readme.io`) only documents a *different* server: a Readme-hosted MCP over Lucid's developer documentation (search docs, install/edit-extension skills). Don't confuse the two.

The product MCP at `mcp.lucid.app` is **live** but documentation-sparse. Live probing confirmed:
- Endpoint accepts MCP protocol on `/mcp`.
- Returns 405 with a hint that SSE streaming isn't supported (Streamable HTTP only).
- `resource_name = "Lucid MCP Server"` per protected-resource metadata.

Tools likely cover documents, pages, shapes, comments, sharing — but I won't invent specifics.

## Would direct integration be advantageous?

**Unclear without the tool list.** Defer until Lucid publishes proper documentation. Worth a recheck in 1-2 months — the OAuth metadata is correctly set up enough that this is clearly intended for general use.

## Mechanisms beyond Notion

- **PKCE-S256 only** (no `plain` fallback). Aggregator already does S256, so this is a non-issue.
- No advertised scopes.
- Two distinct Lucid-branded MCP servers (product vs docs). Don't conflate.

## Effort

**Defer.** Investigate again when Lucid publishes a tool list. Implementation would presumably be Notion-shape.

## Sources

- AS metadata: https://mcp.lucid.app/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.lucid.app/.well-known/oauth-protected-resource
- Adjacent docs MCP (different product): https://lucid.readme.io/docs/mcp
