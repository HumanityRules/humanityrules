# Miro

`https://mcp.miro.com/`

## DCR status

- AS metadata `https://mcp.miro.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.miro.com/register`. DCR live-tested.
- `scopes_supported` (2): `boards:read`, `boards:write`. Coarse but real.
- **OAuth 2.1 with DCR explicitly documented.**

## Did Merge do a good job?

**Comparable surface.** Merge ships **46 tools**. Miro's first-party MCP exposes **29 tools** across boards, context, diagrams, docs, images, tables, code-widgets, comments, and layouts. Functional families (prefix-grouped):

- Read (14): `board_list_items`, `board_search_boards`, `context_explore`, `context_get` (uses AI Credits), `diagram_get_dsl`, `doc_get`, `image_get_data`, `image_get_url`, `table_list_rows`, `code_widget_get`, `code_widget_list_items`, `comment_list_comments`, `layout_get_dsl`, `layout_read`
- Write (15): `board_create`, `diagram_create`, `doc_create`, `doc_update`, `image_create`, `image_get_upload_url`, `table_create`, `table_sync_rows`, `code_widget_create`, `code_widget_update`, `code_widget_delete`, `comment_reply`, `comment_resolve`, `layout_create`, `layout_update`

Merge wins on count; direct wins on a couple of unique capabilities (DSL-based bulk authoring, presigned-URL uploads).

## Would direct integration be advantageous?

**Marginal.** Direct gives us:

1. **DSL-based authoring** (`layout_create`, `diagram_create`) — bulk operations described in Miro's DSL. The agent has to learn the DSL via `*_get_dsl` tools first, then issue large structured payloads.
2. **AI-credit-billed tools** (`context_get`) — explicit "this costs credits" surfacing.
3. **OAuth 2.1 + DCR explicit.**

Worth considering as a follow-up to the easy candidates. Not a high-priority replacement.

## Mechanisms beyond Notion

- **Per-resource scopes (read/write split).** `boards:read`, `boards:write`. Aggregator should request both.
- **Team-pinned at install time.** During OAuth: "Select team to install Miro MCP into." A single client install pins one team — switching teams requires re-auth. Aggregator should make this clear.
- **Presigned-URL upload flow.** `image_get_upload_url` returns a temporary single-use URL the client uses to PUT the asset directly. Aggregator must let through opaque presigned URLs without rewriting.
- **AI-credit billing** surfaces inside tool descriptions. Annotate the connector accordingly.
- **Enterprise gate.** Miro Enterprise tenants must enable MCP Server for the org first.
- No URL-level narrowing.

## Effort

**Straightforward.** Notion-shape with a `default_scope = "boards:read boards:write"` and a "team picked at consent — re-auth to switch" note in the connector description. No per-session config.

## Sources

- https://developers.miro.com/docs/miro-mcp
- https://developers.miro.com/docs/connecting-to-miro-mcp
- https://developers.miro.com/docs/miro-mcp-prompts
- https://developers.miro.com/reference/scopes
