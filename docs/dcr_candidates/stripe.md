# Stripe

`https://mcp.stripe.com`

## DCR status

- AS metadata `https://mcp.stripe.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://access.stripe.com/mcp/oauth2/register`. DCR live-tested.
- `scopes_supported = []`. Coarse, server-validated. Stripe describes OAuth as having "more granular permissions and user-based authorization" than secret keys but does **not** publish scope strings for clients to request.

## Did Merge do a good job?

**Mixed.** Merge ships a huge **89 tools** for Stripe — far more than Stripe's first-party MCP (~25). Merge covers the full Stripe REST surface (subscription billing, invoicing, refunds, disputes, products, prices). Stripe's own MCP is read-and-admin-leaning and explicitly omits creating PaymentIntents/Charges, Checkout Sessions, transfers, payouts, webhooks. From Stripe's docs page: "the surface is still evolving."

So Merge is the broader surface today, but the auth model Merge picked — **`secret API key`** — is a bigger compromise. Stripe's API key has full account access, and Merge's connector flow asks the user to paste it directly.

## Would direct integration be advantageous?

**Yes, primarily for security posture.** Three reasons:

1. **OAuth token is auditable and revocable per session.** Sessions are listed in the Dashboard at user settings → OAuth sessions. Merge's API-key path has no such control.
2. **Sandbox/live separation is enforced at the OAuth grant level.** Admins enable MCP separately for sandbox vs. live mode. Merge's API-key form does not enforce this — the user pastes whichever key they have.
3. **Built-in docs/knowledge tools** (`search_stripe_documentation`, `search_stripe_resources`, `fetch_stripe_resources`) — agents can query Stripe knowledge directly. Merge has none.

The trade-off: direct loses 60+ tools Merge ships. Worth keeping Merge available for power users who need the full Stripe REST surface.

## Mechanisms beyond Notion

- **Sandbox/live mode separation.** Admins enable MCP separately at `dashboard.stripe.com/settings/mcp` for each. The aggregator should let the customer pick which mode to connect, and the spec needs an "environment" field that rebuilds upstream URL appropriately.
- **No Connect / connected-account picker.** Notable gap in Stripe's MCP — agents acting on platform accounts cannot be scoped to a specific connected account via URL or tool argument we could find. Aggregator can flag this in the connector's description for transparency.
- **No scope strings.** Coarse OAuth grant — no `default_scope` to set.
- **Treasury "agentic finance" tools** (autonomous money movement, bill payment, card management) are early-access waitlist only — not on the remote server. Don't plan around them.

## Effort

**Straightforward, with one design choice.** Notion-shape connector module — no scopes, no per-session config — **except** for sandbox/live mode. Two reasonable paths:

- Treat `mcp-stripe-sandbox` and `mcp-stripe-live` as two separate connectors in the spec list. Cleaner; matches how Stripe's UI thinks about it.
- One `connectors/stripe.py` with a `mode` field in per-session config and a meta-tool to flip it. More work, less obvious to the user.

Recommend the two-connector path.

## Sources

- https://docs.stripe.com/mcp
