# Intercom

`https://mcp.intercom.com/sse` **— deprecated; new endpoint `https://mcp.intercom.com/mcp`.**

## DCR status

- AS metadata `https://mcp.intercom.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.intercom.com/register`. DCR live-tested.
- `scopes_supported = []`. Coarse; Intercom describes scopes as "Read and list users and companies", "Read conversations", "Read and write articles" — bundled, not exposed as RFC 7591 strings.

## Did Merge do a good job?

**Mixed.** Merge ships **31 tools**. Intercom's own MCP exposes just **13** — five of them are the universal `search`/`fetch` ChatGPT-app pattern (typed-prefix IDs `conversation_*`, `contact_*`, `company_*`), the rest are list/get/create/update on conversations, contacts, companies, articles. Intercom's surface is **deliberately narrow.**

Where Merge wins: real surface area, write tools, more granular reads. Where Intercom's MCP wins: the universal `search`/`fetch` pattern that ChatGPT/Claude know natively, plus tighter scoping.

**Critical blocker for direct integration: US-only.** Intercom's MCP server hard-rejects EU/AU-hosted workspaces. Customers on those regions cannot use the first-party MCP at all. Merge's REST integration may or may not have the same constraint (Intercom's APIs themselves are region-routed but accessible).

## Would direct integration be advantageous?

**Probably not as a replacement.** Direct integration loses a lot of Merge's surface, gains the universal `search`/`fetch` pattern, and breaks for any non-US customer. Keep Merge as primary; consider direct as an optional power-user path for US-hosted customers who specifically want ChatGPT-style universal search.

## Mechanisms beyond Notion

- **Region restriction.** Aggregator must check the user's Intercom region before attempting OAuth, or surface a clear error after. Not a config knob — a hard gate.
- **Universal `search`/`fetch` requires `object_type:` DSL in the query string.** Without it, search silently returns nothing. Worth coding into the connector's tool description so the agent gets it right.
- **Default `search` page size is 5 (max 150).** Counter-intuitive default — bake `limit:` into the tool description.
- **Mixed pagination conventions** (cursor on search, page numbers on list_companies/articles). Annotate clearly.

No URL params, no scope strings, no per-session config.

## Effort

**Straightforward but with a region check.** ~Notion-shape connector module. Add an explicit precondition step: when the user starts the OAuth flow, hit Intercom's region-detection endpoint or surface a "US-hosted workspaces only" notice in the consent UI.

## Sources

- https://developers.intercom.com/docs/guides/mcp
