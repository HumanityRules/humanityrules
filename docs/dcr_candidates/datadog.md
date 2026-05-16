# Datadog

`https://mcp.datadoghq.com` (US1) + regional siblings (`us3`, `us5`, `eu`, `ap1`, `ap2`)

## DCR status

- AS metadata `https://mcp.datadoghq.com/.well-known/oauth-authorization-server`. **OAuth 2.1 explicit.**
- `registration_endpoint = https://mcp.datadoghq.com/api/unstable/mcp-server/register`. DCR live-tested, returns a working `client_id`.
- `scopes_supported = []`. Datadog defines two MCP-specific RBAC permissions (not OAuth scopes): `mcp_read` and `mcp_write`. Resource-level perms (Monitors Read, etc.) layer on top.

Status: "under significant development" — effectively beta but in production with limits.

## Did Merge do a good job?

**No — Merge significantly under-shoots.** Merge ships **49 tools** as `API key + application key` (paste both in a form). Datadog's first-party MCP exposes **~110+ tools across 18 toolsets**:

`core`, `alerting`, `apm` (preview), `cases`, `dashboards`, `dbm`, `ddsql`, `error-tracking`, `feature-flags`, `kubernetes`, `llmobs`, `networks`, `onboarding`, `product-analytics`, `reference-tables`, `security`, `software-delivery`, `synthetics`, `workflows`.

Examples: `search_datadog_logs`, `get_datadog_metric`, `analyze_datadog_logs`, `get_datadog_trace`, `apm_search_spans`, `apm_latency_bottleneck_analysis`, `upsert_datadog_dashboard`.

Datadog explicitly warns: "Enabling all toolsets increases the number of tool definitions sent to your AI client, which consumes context window space. `toolsets=all` works best with clients that support tool filtering." This is a **direct precedent for the PostHog `?features=` work** we already did.

Merge's 49 tools cover the basic monitoring surface but are absent from APM, kubernetes, security, error-tracking, llmobs, software-delivery, synthetics, and workflows.

## Would direct integration be advantageous?

**Yes, clearly.** This is one of the strongest cases for direct integration:

1. **Massively wider tool surface** (49 vs 110+).
2. **OAuth instead of two API keys** — better security posture, audit trail.
3. **Region routing handled cleanly.** Datadog has six regional sites, no central router. Aggregator must let the customer pick.
4. **Toolsets filtering** maps directly to our PostHog session-config pattern.

## Mechanisms beyond Notion

This is **a near-clone of PostHog with one extra wrinkle (regions).** Same effort tier as PostHog.

- **Region routing.** Six possible bases (`app.datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`, `app.datadoghq.eu`, `ap1.datadoghq.com`, `ap2.datadoghq.com`). GovCloud (`app.ddog-gov.com`, `us2.ddog-gov.com`) **not supported.** Aggregator must surface a region picker at consent and rebuild the upstream URL.
- **`?toolsets=` query param** for session-level narrowing. Default = `core` only. Examples: `?toolsets=synthetics`, `?toolsets=core,synthetics,software-delivery`, `?toolsets=all`. Maps cleanly to PostHog's `?features=` config.
- **`max_tokens` per-call argument** for response truncation. The agent passes this; we just pass-through.
- **Datadog-specific RBAC perms** (`mcp_read` / `mcp_write`) sit on top of resource-level perms. Admins can enforce read-only.
- **APM toolset is Preview, signup-required** — flag in connector description.
- **Built-in telemetry**: `datadog.mcp.session.starts`, `datadog.mcp.tool.usage`, plus Audit Trail "MCP Server" events. Tool calls are user-attributed.
- HIPAA-eligible.
- Rate limits: 50 calls / 10s burst, 5000/day, 50,000/month.

## Effort

**High.** Plan ~1 week of work, similar to PostHog:

- `connectors/datadog.py` with three meta-tools: `datadog-set-region`, `datadog-list-toolsets`, `datadog-set-toolsets` (set takes a comma-separated list).
- Per-session config persisted next to client.json/token.json — both region and toolset selection.
- Rebuild upstream URL on every catalog load and call: `https://mcp.<region>/?toolsets=<...>`.
- Region picker at consent (UI work in the integrations panel).
- `default_scope = None` (Datadog uses RBAC, not OAuth scopes).

This is the **second-most-justified direct-integration target after Sentry**, and the easiest to ship right after PostHog because the mechanics line up almost exactly.

## Sources

- https://docs.datadoghq.com/bits_ai/mcp_server/
- https://docs.datadoghq.com/bits_ai/mcp_server/setup/
- https://docs.datadoghq.com/bits_ai/mcp_server/tools/
- https://docs.datadoghq.com/account_management/rbac/permissions/#mcp
