# Wix

`https://mcp.wix.com/mcp` (also `/sse`)

## DCR status

- AS metadata `https://mcp.wix.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.wix.com/register`. DCR live-tested.
- `scopes_supported = []`.
- DCR isn't explicitly documented; Wix also accepts API key + account ID for non-interactive clients.

## Did Merge do a good job?

**Different shapes.** Merge ships **31 tools** as `API key`. Wix's first-party MCP follows the **umbrella-tool pattern**: ~12 tools where most surface area funnels through `CallWixSiteAPI`, a mega-dispatcher to Wix's REST/SDK:

- Documentation Q&A: `SearchWixWDSDocumentation`, `SearchWixRESTDocumentation`, `SearchWixSDKDocumentation`, `SearchBuildAppsDocumentation`, `SearchWixHeadlessDocumentation`, `WixBusinessFlowsDocumentation`, `ReadFullDocsArticle`, `ReadFullDocsMethodSchema`
- Site operations: `ListWixSites`, `CallWixSiteAPI`, `ManageWixSite`, `SupportAndFeedback`

The umbrella pattern is **agent-hostile**: the model has to know Wix's REST/SDK shape to call `CallWixSiteAPI` correctly. Wix expects the agent to use the doc-search tools first, then construct the API call — RAG-style. Merge's 31 named tools are easier for an LLM to pick up.

## Would direct integration be advantageous?

**Probably not.** Merge's approach is friendlier for our use case: discrete tools the LLM can pick from `search`. The direct MCP optimizes for "give the LLM the docs and let it call anything," which is powerful but burns more tokens.

## Mechanisms beyond Notion

- **Umbrella-tool pattern.** No per-action tool definitions; one tool taking `(site_id, action, payload)` plus discovery via `ListWixSites`. If we ever wanted to handle this shape generically, we'd need to teach the catalog how to surface "doc-shaped" tools.
- **Two auth flows.** Mixed OAuth + API-key on the same endpoint. Merge's API-key path is the simpler one.
- No scope strings, no `?features=`, no per-session config.

## Effort

**Skip for now.** Notion-shape technically possible, but the umbrella-tool model means we'd surface 12 tools that look uninteresting to the LLM. Merge's 31 named tools serve our customers better. Reconsider if Wix ever shifts to a per-action tool catalog.

## Sources

- https://www.wix.com/studio/developers/mcp-server
- https://dev.wix.com/docs/sdk/articles/use-the-wix-mcp/about-the-wix-mcp
