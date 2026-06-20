# PayPal

`https://mcp.paypal.com` (production), `https://mcp.sandbox.paypal.com` (sandbox)

## DCR status

- AS metadata `https://mcp.paypal.com/.well-known/oauth-authorization-server`.
- `registration_endpoint = https://mcp.paypal.com/register`. DCR live-tested, returns a working `client_id`.
- `scopes_supported = []`. PayPal's MCP docs route the reader to PayPal's general "Apps, scopes, and credentials" reference — the MCP server itself doesn't enumerate scopes.

But: PayPal's docs also say the deployment model uses **Developer Dashboard client_id/secret turned into a Bearer access token, with a hosted login + consent screen for end-user authorization** (`~/.mcp-auth` cache). DCR is advertised but not the documented deployment path.

## Did Merge do a good job?

**Yes — Merge has the broader surface.** Merge ships **62 tools**. PayPal's first-party MCP exposes ~28 core + 3 commerce-flagged:

- Catalog (`create_product`, `list_product`, `show_product_details`)
- Disputes (`list_disputes`, `get_dispute`, `accept_dispute_claim`)
- Invoices (7)
- Payments / Orders (5)
- Reporting (2)
- Shipment Tracking (3)
- Subscriptions (7)
- Gift-card commerce (gated, 3 tools, only when `x-feature-flags: commerce:true` header is sent)

Merge picks `API credentials` (client_id/secret) for connection, which matches PayPal's preferred path. The user friction is similar either way.

## Would direct integration be advantageous?

**Modest.** Going direct gets us:

- **`x-feature-flags` header gating.** This is novel — PayPal advertises tool surface gating via HTTP header (`commerce:true` enables gift-card tools). PostHog uses `?features=` query string; PayPal uses headers. Same idea, different transport.
- **Sandbox vs production explicitly separate hosts.**

Going direct loses: 30+ Merge tools and the broader PayPal REST API surface that Merge funnels through.

We'd want PayPal direct only if a customer specifically needs the gift-card commerce flow or wants strict sandbox/production isolation. Otherwise Merge is fine.

## Mechanisms beyond Notion

- **`x-feature-flags` HTTP header** — gates the gift-card commerce tools (`commerce:true`). First "URL knob via header" we've seen. Aggregator needs to plumb session-level headers, not just URL query strings.
- **Two transports on the same host:** `/sse` and `/http`. Pick `/http` for streamable HTTP.
- **Sandbox vs production = different host.** Same pattern as Stripe.
- No scope strings, no `?features=`/`?tools=` query params.
- Financial mutations go through the same tool surface the LLM sees by default — no read-only mode flag. Aggregator should consider exposing one (block `*_create`, `*_refund`, `*_pay_*` tools server-side) or surfacing tool annotations on writes.

## Effort

**Medium.** ~Notion-shape with three additions:

- Two-connector pattern (sandbox + production) like Stripe.
- Per-session header config for `x-feature-flags`, with a meta-tool to flip it. Hermes's aggregator does NOT yet plumb session-level upstream headers — this would be a small extension to the per-session config used by PostHog.
- Optional read-only-mode wrapper around tool dispatch.

## Sources

- https://docs.paypal.ai/developer/tools/ai/mcp-quickstart/
- https://docs.paypal.ai/developer/tools/ai/agent-tools-ref
- https://docs.paypal.ai/llms.txt
