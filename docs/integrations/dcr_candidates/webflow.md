# Webflow

`https://mcp.webflow.com/sse`

## DCR status

- AS metadata `https://mcp.webflow.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.webflow.com/oauth/register`. DCR live-tested.
- `scopes_supported = []`. DCR not the documented auth path; Webflow's docs describe a registered Webflow app underneath. The endpoint accepts our DCR call but the production flow goes via the `mcp-remote` proxy + a stored app.

## Did Merge do a good job?

**Yes for breadth.** Merge ships **64 tools** for Webflow. Webflow's first-party MCP uses the **umbrella-tool-per-area pattern**: ~17 top-level tools, each accepting an `actions: [...]` array:

- Data API tools: `data_sites_tool`, `data_cms_tool`, `data_pages_tool`, `data_components_tool`, `data_comments_tool`, `data_scripts_tool`, `data_webhook_tool`, `data_workflows_tool`, `data_enterprise_tool`, `ask_webflow_ai`
- Designer API tools (require Bridge App): `de_page_tool`, `de_component_tool`, `component_builder`, `element_tool`, `element_builder`, `whtml_builder`, `element_snapshot_tool`, `asset_tool`, `get_image_preview`, `style_tool`, `de_learn_more_about_styles`, `variable_tool`
- Meta: `webflow_guide_tool`, `get_designer_app_connection_info`

Merge's 64 named tools are friendlier for our LLM-facing catalog than 17 umbrella tools, even though the underlying capability is comparable.

## Would direct integration be advantageous?

**No, unless we want Designer API access** — which requires a separate **Webflow MCP Bridge App** to be installed and running inside the user's Designer. That's a significant onboarding wrinkle that we don't want to introduce for most customers.

## Mechanisms beyond Notion

- **Umbrella-tool-per-area** (similar to Wix, lighter than Wix).
- **Companion-app requirement** for Designer tools. The Bridge App auto-installs during OAuth and must remain open in the user's Designer for those tools to function. Unique among all candidates.
- **Sites/workspaces selected at OAuth consent.** No mid-session switch tool; user grants access to specific sites and the surface is static for that grant.
- **Open-source server source on GitHub** — easy to audit/fork if we ever need to.
- No scope strings published.

## Effort

**Skip for now.** Same reason as Wix: Merge's named-tool surface is friendlier than the umbrella shape. The Bridge App requirement makes the Designer tools impractical in our current setup anyway.

## Sources

- https://developers.webflow.com/data/docs/ai-tools
- https://github.com/webflow/mcp-server/tree/main/src/tools
