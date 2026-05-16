# Ramp

`https://mcp.ramp.com/mcp` (and `https://demo-mcp.ramp.com/mcp` for development)

## DCR status

- AS metadata `https://mcp.ramp.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.ramp.com/register`. DCR live-tested.
- **`scopes_supported`** (~27 unique fine-grained `resource:permission` strings): `bills:read`, `bills:write`, `cards:read`, `cards:read_agentic`, `cards:write`, `departments:read`, `entities:read`, `limits:read`, `locations:read`, `memos:read`, `purchase_orders:read`, `reimbursements:read`, `reimbursements:write`, `spend_programs:read`, `transactions:read`, `transactions:write`, `users:read`, `vendors:read`, `treasury:read`, `accounting:read`, `approvals:write`, `comments:write`, `receipts:write`, `trips:read`, `trips:write`, `unified_requests:read`.
- **Three-host OAuth ceremony.** `issuer = app.ramp.com`, authorize at `mcp.ramp.com`, token at `api.ramp.com`. Resource host is `mcp.ramp.com`. Aggregator must follow each indirection.
- **Allowlisted redirect URIs.** Custom clients must email Ramp support to register; only `https://` or `localhost`/`127.0.0.1`, exact host (no wildcard subdomains).
- `token_endpoint_auth_methods`: `none` only (PKCE-public clients).

GA. **Agent Cards is Alpha** ("approved access" required).

## Did Merge do a good job?

**Yes for breadth.** Merge ships **30 tools**. Ramp's first-party MCP **does not publish a complete tool list** — the docs say "specific tool names will drift; categories shouldn't." Documented categories:

- Reads/analysis: transactions, vendors, accounting categories, departments, entities, treasury balances, org chart
- Approvals: transactions, reimbursements, unified requests (POs, fund requests). Bill approvals not yet on MCP.
- Edits: memos, fund assignment, GL coding, card lock/unlock
- Policy Q&A: NL policy checks, Help Center search, decline explanations
- Agent Cards (alpha): includes `ramp_get_agent_card_creds`
- Travel: trips, flight/hotel bookings
- AI Index (separate but on the same server): `ai_index_get_adoption`, `ai_index_get_adoption_by_sector`, `ai_index_get_adoption_by_size`

Merge probably has comparable coverage on the standard CRUD; direct wins on Policy Q&A (built-in NL policy checks), AI Index, and the Agent Cards alpha.

## Would direct integration be advantageous?

**Yes, but blocked on allowlist.** The redirect-URI allowlist is the same friction as Square and ClickUp — we'd need Ramp support to register our redirect URI before any customer can connect.

If unblocked, this is one of the strongest cases for direct integration alongside Datadog and Sentry:

1. **PostHog-shaped fine-grained scope catalog** — 27 resource:permission scopes the user can authorize selectively.
2. **Policy Q&A** with NL checks against the customer's own spend policy.
3. **Agent Cards** — short-lived agentic card credentials (alpha) for autonomous spend.
4. **Visa Intelligent Commerce** integration for agent purchases.
5. **Demo server** (`demo-mcp.ramp.com/mcp`) for development with synthetic data — useful for CI.

## Mechanisms beyond Notion

- **Redirect URI allowlist.** Hard precondition.
- **27 fine-grained scopes** — full PostHog-shaped scope catalog. Aggregator should request the union of read+write scopes the customer needs.
- **Three-host OAuth ceremony** (`app.ramp.com` issuer / `mcp.ramp.com` resource / `api.ramp.com` token endpoint). Pre-existing OAuth code should handle this if it correctly follows discovery, but worth verifying.
- **Financial-grade approvals** are tracked in audit log; bill approvals deliberately off MCP.
- **Read-only sessions vs read-write expire differently.** Read-only: 1 week after last use. Read-write: 24 h after last use. Aggregator should pre-emptively refresh.
- **`cards:read_agentic` scope** — separate from `cards:read`. Financial-grade scope dedicated to agent card usage.
- **Query result cap: 100 rows per call.** Hard server limit.

## Effort

**Blocked on allowlist.** If unblocked: medium-high — full PostHog-shaped `default_scope` work, plus a "promote to read-write" meta-tool for sessions that need write access only when approving spend.

## Sources

- AS metadata: https://mcp.ramp.com/.well-known/oauth-authorization-server
- Resource metadata: https://mcp.ramp.com/.well-known/oauth-protected-resource
- https://docs.ramp.com/developer-api/v1/build-for-ai-agents
- https://docs.ramp.com/developer-api/v1/authorization
