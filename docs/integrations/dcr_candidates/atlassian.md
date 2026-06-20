# Atlassian (Jira + Jira Service Management + Confluence)

`https://mcp.atlassian.com/v1/sse` **— deprecated, sunsetting 2026-06-30.**
**New endpoint: `https://mcp.atlassian.com/v1/mcp/authv2`.**

The three Merge connectors `jira`, `jira_service_management`, `confluence`
all hit the same Atlassian MCP server (and a single OAuth grant covers all
three plus Bitbucket Cloud and Compass).

## DCR status

- AS metadata `https://mcp.atlassian.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://cf.mcp.atlassian.com/v1/register`. **DCR live-tested**, returns a working `client_id`.
- `scopes_supported = []` on the AS metadata, but **scopes are required and tiered** at the resource (see below).

## Did Merge do a good job?

**Reasonable, but with notable gaps.** Merge ships:

- `jira`: 42 tools
- `jira_service_management`: 41 tools
- `confluence`: 25 tools

Atlassian's own MCP exposes ~50+ tools in a single bundle, including Bitbucket Cloud (~8 tools) and Compass (~9 tools) which Merge doesn't have at all. JSM coverage on the direct server is **narrower** than Merge's — only `getJsmOpsAlerts`, `getJsmOpsScheduleInfo`, `getJsmOpsTeamInfo`, `updateJsmOpsAlert`. Merge's 41 JSM tools cover service desk request CRUD that the direct MCP intentionally omits today.

So: Merge wins on JSM service-desk surface; direct MCP wins on Bitbucket+Compass and on always-fresh tool inventory.

## Would direct integration be advantageous?

**Yes for Jira+Confluence — split decision for JSM.** Three reasons:

1. **One OAuth grant, three+ products.** Connecting Atlassian once gives the agent Jira, Confluence, JSM ops, Bitbucket, Compass, plus Rovo/Teamwork Graph search. Merge requires separate connections for jira, jsm, confluence and doesn't have Bitbucket/Compass at all.
2. **Teamwork Graph search** (`searchAtlassian`, `fetchAtlassian`) is a unified RAG over the customer's Atlassian data — not exposed by Merge.
3. **Audit logging of MCP actions** is built in.

The catch: if customers actually need service desk request CRUD (create JSM ticket, update fields, transition status), Merge has it and Atlassian's MCP doesn't.

## Mechanisms beyond Notion

This is **the first non-trivial integration** in the candidate list:

- **Tiered permission scopes.** Per-product, per-direction: `read_jira`, `write_jira`, `search_jira`, `read_confluence`, `write_confluence`, `search_confluence`, `read_jsm`, `write_jsm`, `read_bitbucket`, `write_bitbucket`, `read_compass`, `write_compass`, `read_teamwork_graph`, `search_atlassian`. The user (or the aggregator) must request the right combination at consent. This is PostHog-shaped: explicit `default_scope` on the spec, but unlike PostHog the scope set is small and stable.
- **CloudId discovery.** Many tool calls require knowing the user's Atlassian Cloud site ID. The agent calls `getAccessibleAtlassianResources` first, then passes the cloudId on every other call. Not a session-level config — a tool-call argument — but the UX implication is "the agent must learn this dance."
- **Endpoint sunset.** `/v1/sse` retires 2026-06-30. Pin the new `/v1/mcp/authv2` endpoint from day one.
- **First-user-must-be-admin gotcha.** "The first user through 3LO must have access to all apps the requested scopes touch." Site admin must authorize before regular users can.
- **IP allowlists are enforced per-product.** A tool call may pass for Jira and fail for Confluence in the same session. Surface a sensible error.

## Effort

**Medium.** ~PostHog-shaped:

- One `connectors/atlassian.py` covering all three Merge connectors.
- `default_scope` covering the products we want (start with `read_jira write_jira search_jira read_confluence write_confluence search_confluence`).
- No per-session config like PostHog's `?features=` — the scope set is fixed at consent.
- No meta-tools needed.
- One thing the aggregator already handles correctly: the OAuth flow goes through `mcp.atlassian.com` (issuer + authorize) but DCR is at `cf.mcp.atlassian.com` — we already follow that indirection from `oauth-authorization-server` metadata.

## Sources

- https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/
- https://support.atlassian.com/atlassian-rovo-mcp-server/docs/supported-tools-of-the-atlassian-remote-mcp-server/
- https://www.atlassian.com/blog/announcements/remote-mcp-server
