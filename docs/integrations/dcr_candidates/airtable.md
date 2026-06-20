# Airtable

`https://mcp.airtable.com/mcp`

## DCR status

- AS metadata `https://mcp.airtable.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://airtable.com/oauth2/v1/register`. DCR live-tested.
- `scopes_supported` (7): `data.records:read`, `data.records:write`, `schema.bases:read`, `schema.bases:write`, `data.recordComments:read`, `data.recordComments:write`, `workspacesAndBases:read`. Airtable's own MCP docs also reference `webhook:manage`.
- **DCR is NOT the documented auth path.** Airtable's docs say "Manual OAuth client registration" — the DCR endpoint exists but the recommended flow is a registered Airtable app per integrator. PAT (Bearer) is supported as an alternative.

**Enterprise allowlist by client_id**: enterprise admins must allowlist specific OAuth client IDs. Airtable publishes the IDs for ChatGPT, Claude, Amazon Quick. DCR clients hit a wall on enterprise tenants unless the admin allowlists our DCR-issued client_id.

## Did Merge do a good job?

**Yes.** Merge ships **24 tools** for Airtable. The first-party MCP exposes **~19 tools**:

- Discovery (6): `list_workspaces`, `list_bases`, `list_tables_for_base`, `list_pages_for_base`, `search_bases`, `get_table_schema`
- Records read (4): `list_records_for_table`, `list_records_for_page`, `get_record_for_page`, `search_records`
- Records write (2): `create_records_for_table`, `update_records_for_table`
- Schema write (4): `create_table`, `update_table`, `create_field`, `update_field`, `create_base`
- Health (1): `ping`
- Interactive (gated, 1): `display_records_for_table`

Merge's 24 tools cover comparable ground. The direct surface is leaner.

## Would direct integration be advantageous?

**Marginal at best.** Direct gets us:
- `display_records_for_table` widget on supporting clients.
- Cleaner, smaller tool list.

It loses the enterprise allowlist requirement. For consumer/team plans, direct is fine; for enterprise customers, we'd be blocked.

## Mechanisms beyond Notion

- **Per-resource scopes (7).** Aggregator must request each one explicitly. PostHog-shaped but smaller.
- **Enterprise allowlist by client_id.** Aggregator can detect this — DCR succeeds, OAuth fails for enterprise admins. Surface a clear error.
- **Post-hoc base selection.** No in-OAuth base picker; users grant the integration and then go to Airtable settings → third-party integrations to add/remove bases. The aggregator can't drive this.
- **Interactive widget tool** gated to clients advertising interactive-app support.
- No URL-level narrowing.

## Effort

**Medium.** Notion-shape with a `default_scope` listing all seven scopes. No per-session config, no meta-tools.

The enterprise gating is a UX concern — surface it in the connector card. Otherwise straightforward.

## Sources

- https://support.airtable.com/docs/using-the-airtable-mcp-server
