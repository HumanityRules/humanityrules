# Square

`https://mcp.squareup.com/sse`

## DCR status

- AS metadata `https://mcp.squareup.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.squareup.com/register`. DCR live-tested, returns a working `client_id`.
- `scopes_supported = []`.
- **Allowlist-gated.** Per Square's docs: "Square maintains an allowlist of MCP clients in order to protect against malicious client registration attempts. New clients are added via Square developer forum, MCP category." Even though DCR returns a `client_id`, our redirect URI must be pre-approved by Square before the OAuth flow actually completes. **This is the Vercel/Figma pattern.**

Status: **beta** (explicit).

## Did Merge do a good job?

**Yes for breadth, no on the auth path.** Merge ships **90 tools** for Square — the broadest of any candidate. Square's first-party MCP doesn't enumerate a tool list at all; it frames itself as "a single dynamic surface over the Square REST API" covering customers, orders, items "and more." Effective surface is whatever the Square REST API exposes, dispatched at runtime.

Merge wins on transparency (named tools) and probably on real coverage (write tools, granular reads). Square's MCP wins only if you trust the dispatcher to pick the right call.

## Would direct integration be advantageous?

**No, unless Square approves us.** Two reasons against going direct today:

1. **Allowlist gating.** Submitting a redirect URI for review delays integration and creates an external dependency. We'd need to coordinate with Square's developer support before any customer can connect.
2. **Production-only remote.** `mcp.squareup.com` only hits production. Sandbox is local-server only (`SANDBOX=true` env var). Agents running against the remote MCP are always touching real money. **Significant safety implication.**

Worth revisiting once Square exits beta and (a) lifts the allowlist or (b) ships a sandbox host.

## Mechanisms beyond Notion

- **Client allowlist.** Aggregator can detect this — DCR returns a `client_id`, but the authorization endpoint will reject the unknown redirect URI. Surface a clear "this client must be allowlisted by Square" error.
- **Production-only.** Aggregator should add a prominent warning in the connector card / consent flow.
- Local server has `DISALLOW_WRITES=true` and `SQUARE_VERSION=YYYY-MM-DD` — not confirmed for the remote endpoint.

## Effort

**Blocked on Square's allowlist.** If/when unblocked, the implementation is straightforward (Notion-shape + a production-warning notice). For now: don't ship.

## Sources

- https://developer.squareup.com/docs/mcp
- https://github.com/square/square-mcp-server
