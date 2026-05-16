# Attio

`https://mcp.attio.com/mcp`

## DCR status

- AS metadata `https://mcp.attio.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://app.attio.com/oauth/register`. DCR live-tested.
- `scopes_supported = ["mcp", "offline_access", "openid"]`. Coarse "give me MCP" + standard OIDC.
- **AS host ≠ resource host.** Issuer is `https://app.attio.com`; resource is `https://mcp.attio.com`. Aggregator must walk the `oauth-protected-resource → authorization_servers` indirection (RFC 9728).
- `resource_signing_alg_values_supported: ["ES256"]` — ES256-signed access tokens (most peers are RS256 or unsigned bearer).

## Did Merge do a good job?

**Mixed.** Merge ships **31 tools** as `API key`. Attio's first-party MCP exposes ~32 tools:

- Records & Objects (7): `search-records`, `list-records`, `get-records-by-ids`, `create-record`, `upsert-record`, `update-record`, `list-attribute-definitions`
- Lists (7): `list-lists`, `list-list-attribute-definitions`, `list-records-in-list`, `add-record-to-list`, `update-list`, `update-list-entry-by-id`, `update-list-entry-by-record-id`
- Comments (4): `create-comment`, `list-comments`, `list-comment-replies`, `delete-comment`
- Notes (5): `create-note`, `search-notes-by-metadata`, `semantic-search-notes`, `get-note-body`, `update-note`
- Tasks (3)
- Meetings/Calls (4): `search-meetings`, `search-call-recordings-by-metadata`, `semantic-search-call-recordings`, `get-call-recording`
- Emails (3): `search-emails-by-metadata`, `semantic-search-emails`, `get-email-content`
- Workspace (3): `list-workspace-members`, `list-workspace-teams`, `whoami`
- Reporting (1): `run-basic-report`

Surfaces are similar in size. **The direct MCP wins on semantic-search tools** (notes, call recordings, emails) — Attio-side vector search Merge cannot replicate.

## Would direct integration be advantageous?

**Yes, modestly.** Direct gets us:

1. **OAuth instead of API key** — better security posture.
2. **Semantic search across notes, call recordings, emails.**
3. **MCP tool annotations** (write tools annotated as "destructive" / "requires-confirmation"; read tools auto-approve).
4. **ES256-signed tokens** — fine-grained verification if we want it.

## Mechanisms beyond Notion

- **AS / resource host split.** Aggregator must follow the protected-resource → authorization_servers indirection. (Already handled correctly for Atlassian, so this is precedent.)
- **Workspace baked into the access token** at consent time (`workspace_id` claim in JWT). To switch workspaces, the user re-authorizes. Aggregator can surface the current workspace via `whoami` if needed.
- **Per-category rate limits** (read 100/s, write 25/s, search 300/min, semantic 2/s, reporting 2/s). Annotate accordingly.
- No URL-level narrowing.
- ES256 signing — if we ever want to introspect tokens locally we need an ES256 verifier.

## Effort

**Medium.** Notion-shape with two extras:

- `default_scope = "mcp offline_access openid"` (request all three at consent so the token includes refresh + identity).
- Confirm the protected-resource → AS indirection works through our existing OAuth code (it should — Atlassian goes through the same dance).

## Sources

- AS metadata: https://mcp.attio.com/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.attio.com/.well-known/oauth-protected-resource
- Docs: https://docs.attio.com/mcp/overview
