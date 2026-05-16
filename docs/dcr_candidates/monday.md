# Monday.com

`https://mcp.monday.com/mcp`

## DCR status

- AS metadata `https://mcp.monday.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.monday.com/register`. DCR live-tested.
- `scopes_supported = []`. DCR not explicitly documented but implied.

## Did Merge do a good job?

**Mixed.** Merge ships **36 tools**. Monday's first-party MCP exposes ~40 tools across 12 groups, with verbatim coverage that includes some categories Merge doesn't:

- Board operations (5), Item management (4), Columns & groups (3), Users & teams (3)
- Workspaces & folders (7), Documentation (3), Forms (4)
- Dashboards & widgets (3)
- Search (1)
- monday dev sprints (3) — not in Merge
- Advanced API access (3): `all_monday_api`, `get_graphql_schema`, `get_type_details`
- **UI components (interactive widgets):** `show-table`, `show-chart`, `show-battery`, `show-assign` — analogous to MCP-Apps-SDK widgets

Direct MCP wins on dev-sprints coverage and the interactive widgets. Merge wins on comprehensive CRUD across the standard board/item surface. Roughly equivalent overall.

## Would direct integration be advantageous?

**Marginal yes.** Three reasons to consider direct:

1. **Widget tools** (`show-*`) — only useful if the host renders them, but a clear differentiator for clients that do.
2. **Dev sprints** support out of the box.
3. **No scope work** — DCR + coarse grant.

The catch: `all_monday_api` is a GraphQL escape hatch that can read or write *anything* the user can do. We'd want to flag that tool prominently or filter it out at the catalog layer.

## Mechanisms beyond Notion

- **Marketplace install required by an account admin** before users can authorize. The connector card / consent flow should tell the user this prerequisite up front.
- **Interactive UI widget tools.** No special handling needed — they degrade to no-ops on hosts that don't render them.
- **`all_monday_api` security flag.** Consider catalog-level filtering or a prominent annotation.
- No URL params, no scope strings, no per-session config.

## Effort

**Straightforward.** Notion-shape connector module. Add a one-line precondition note to the connector spec ("requires Monday.com marketplace install by an account admin") and consider blacklisting `all_monday_api`.

## Sources

- https://support.monday.com/hc/en-us/articles/28588158981266-Get-started-with-monday-MCP
- https://support.monday.com/hc/en-us/articles/32364501317906-monday-MCP-available-tools
- https://github.com/mondaycom/mcp
