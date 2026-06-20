# Sanity

`https://mcp.sanity.io`

## DCR status

- AS metadata `https://mcp.sanity.io/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.sanity.io/register`. DCR live-tested.
- `scopes_supported = ["global"]`. A single coarse scope. The local `@sanity/mcp-server` repo is **archived** in favor of the hosted server.

GA since v2.6.0 (Dec 2025), currently ~v2.19.0.

## Did Merge do a good job?

**Acceptable, but Merge picks `API token` and the direct surface is bigger.** Merge ships **25 tools**. Sanity's first-party MCP exposes **~30 tools**:

- Schema & Studio (4): `get_schema`, `list_workspace_schemas`, `deploy_schema`, `deploy_studio`
- Document creation (3): `create_documents_from_json`, `create_documents_from_markdown`, `create_version`
- Document patching/reading (4): `patch_document_from_json`, `patch_document_from_markdown`, `query_documents`, `get_document`
- AI image (2): `generate_image`, `transform_image`
- Publishing (6): `publish_documents`, `unpublish_documents`, `discard_drafts`, `version_replace_document`, `version_discard`, `version_unpublish_document`
- Releases (2): `create_release`, `list_releases`
- Project/org (6): `list_organizations`, `list_projects`, `get_project_studios`, `create_project`, `add_cors_origin`, `whoami`
- Datasets (3): `list_datasets`, `create_dataset`, `update_dataset`
- Embeddings/search (2): `list_embeddings_indices`, `semantic_search`
- Docs & rules (5): `migration_guide`, `search_docs`, `read_docs`, `list_sanity_rules`, `get_sanity_rules`

Merge is missing the AI image tools, embeddings/semantic search, and the `deploy_studio` flow. Notable gap.

## Would direct integration be advantageous?

**Yes, modestly.** Direct gets us:

1. **OAuth via DCR** instead of pasting an API token.
2. **AI image generation/transformation** tools.
3. **Semantic search / embeddings** — Sanity-hosted vector search.
4. **`deploy_studio`** — first-party "deploy a UI from MCP" flow. Few peers do this.
5. **Built-in RAG over Sanity docs/rules** (`search_docs`, `read_docs`, `list_sanity_rules`).

## Mechanisms beyond Notion

- **Single coarse `global` scope** — almost the same as Notion's empty scope set, just explicit.
- No URL-level narrowing.
- Project and dataset are tool-call arguments, discovered via `list_projects` / `list_datasets`.
- No multi-tenancy picker.
- `deploy_studio` returns a hosted Studio URL — pass-through, no special handling.

## Effort

**Straightforward.** Notion-shape with `default_scope = "global"`. Drop in `connectors/sanity.py`. No per-session config, no meta-tools.

## Sources

- https://www.sanity.io/docs/ai/mcp-server
- AS metadata: https://mcp.sanity.io/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.sanity.io/.well-known/oauth-protected-resource
- Archived local server: https://github.com/sanity-io/sanity-mcp-server
