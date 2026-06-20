# Datadog

`https://mcp.datadoghq.com` (US1) + regional siblings (`us3`, `us5`, `eu`, `ap1`, `ap2`)

**MCP endpoint path:** `/api/unstable/mcp-server/mcp` (NOT `/mcp` — the bare
path returns 404). Confirmed via the protected-resource metadata at
`/.well-known/oauth-protected-resource` and the `mcp_resource_uri` field
returned by DCR registration.

## DCR status

- AS metadata `https://mcp.datadoghq.com/.well-known/oauth-authorization-server`. **OAuth 2.1 explicit.**
- `registration_endpoint = https://mcp.datadoghq.com/api/unstable/mcp-server/register`. DCR live-tested, returns a working `client_id`.
- `scopes_supported = []`. Datadog defines two MCP-specific RBAC permissions (not OAuth scopes): `mcp_read` and `mcp_write`. Resource-level perms (Monitors Read, etc.) layer on top.
- `client_id_metadata_document_supported`: **absent** — Datadog does not support CIMD.
- `code_challenge_methods_supported = ["S256"]`, `pkce_required = true`.

Status: "under significant development" — effectively beta but in production with limits.

## Blocked: undocumented redirect_uri allowlist on /authorize

**Direct integration is blocked** until Datadog allowlists our redirect host.
Verified 2026-05-16 by independent reproduction.

**What we tried.** Standard DCR + PKCE flow against `mcp.datadoghq.com`:

1. POST to `registration_endpoint` with `redirect_uris = ["https://hermes-datadog-test.chsandbox.com/__doh_broker/integrations/datadog/oauth/callback"]` → **201 Created**, our redirect URI echoed back verbatim in the response.
2. GET `/api/unstable/mcp-server/authorize` with `client_id=<the registered id>` and that exact `redirect_uri` → **400 "Invalid redirect_uri"** (plain text, no `error_description`).

**The allowlist (independently reproduced).** Same DCR-then-authorize loop with
varying `redirect_uri`s, no redirect-following, captured `/authorize`'s
response status:

- `http://localhost/...` (any port, any path) → **302 to consent page**
- `http://127.0.0.1:<port>/...` → **302**
- `http://[::1]:<port>/...` → **302**
- `https://localhost:<port>/...` → **302**
- `https://claude.ai/...` (any path) → **302**
- `https://app.datadoghq.com/...` → **302**
- Everything else, including arbitrary paths on our own host (`/cb`,
  `/oauth/callback`, the full `__doh_broker/...` path) → **400 Invalid redirect_uri**

The decision is purely host-based on the URI; the path doesn't matter. The
OIDC `application_type` parameter (`web` vs `native` vs absent) does not
affect the outcome — so this is not the OIDC native-vs-web spec angle.

**No CIMD escape hatch.** Per the MCP spec, an AS supporting Client ID
Metadata Documents `SHOULD fetch metadata documents when encountering
URL-formatted client_ids`. Datadog rejects URL-shaped `client_id` values up
front with `400 Invalid client_id` — without ever attempting a metadata
fetch. So the spec's preferred path for clients with no prior relationship
is unavailable.

**Where this lives in the MCP spec.** Datadog's behavior is permitted, even
required, by the MCP authorization spec (linked from Datadog's own docs at
`docs.datadoghq.com/bits_ai/mcp_server/`):

- "Authorization servers **MUST** validate exact redirect URIs against
  pre-registered values to prevent redirection attacks." (Open Redirection)
- "Authorization servers **SHOULD** only automatically redirect the user
  agent if it trusts the redirection URI. If the URI is not trusted, the
  authorization server MAY inform the user and rely on the user to make the
  correct decision." (Open Redirection)
- "Authorization servers **MAY** implement domain-based trust policies:
  Allowlists for trusted domains (for protected servers)... Servers maintain
  full control over their access policies." (Trust Policies)

Where Datadog deviates from the *spirit* of the spec but not the *letter*:
they keep the `registration_endpoint` open as a honeypot — DCR succeeds for
any URI — while enforcing the actual gate at `/authorize` with no signal
back to the caller about why. This is the same anti-pattern documented for
Square, ClickUp, Ramp, and Figma in this audit.

**Datadog's docs steer hosted brokers to API keys.** From
`docs.datadoghq.com/bits_ai/mcp_server/setup/`: *"If you cannot go through the
OAuth flow (for example, on a server), you can provide a Datadog API key and
application key as `DD_API_KEY` and `DD_APPLICATION_KEY` HTTP headers."* The
literal "for example, on a server" is the only doc-side acknowledgement of
the constraint we hit.

## Did Merge do a good job?

**No — Merge significantly under-shoots.** Merge ships **49 tools** as `API key + application key` (paste both in a form). Datadog's first-party MCP exposes **~110+ tools across 18 toolsets**:

`core`, `alerting`, `apm` (preview), `cases`, `dashboards`, `dbm`, `ddsql`, `error-tracking`, `feature-flags`, `kubernetes`, `llmobs`, `networks`, `onboarding`, `product-analytics`, `reference-tables`, `security`, `software-delivery`, `synthetics`, `workflows`.

Examples: `search_datadog_logs`, `get_datadog_metric`, `analyze_datadog_logs`, `get_datadog_trace`, `apm_search_spans`, `apm_latency_bottleneck_analysis`, `upsert_datadog_dashboard`.

Datadog explicitly warns: "Enabling all toolsets increases the number of tool definitions sent to your AI client, which consumes context window space. `toolsets=all` works best with clients that support tool filtering." This is a **direct precedent for the PostHog `?features=` work** we already did.

Merge's 49 tools cover the basic monitoring surface but are absent from APM, kubernetes, security, error-tracking, llmobs, software-delivery, synthetics, and workflows.

## Would direct integration be advantageous?

**Yes if we get allowlisted, no until then.** Without the allowlist this is a
pure downgrade vs Merge — Merge's API-key flow works today; ours doesn't.

If Datadog allowlists our redirect host, the value case is unchanged from the
original audit:

1. **Massively wider tool surface** (49 vs 110+).
2. **OAuth instead of two API keys** — better security posture, audit trail.
3. **Region routing handled cleanly.** Datadog has six regional sites, no central router.
4. **Toolsets filtering** maps directly to our PostHog session-config pattern.

## Mechanisms beyond Notion

The mechanisms we'd still need *if/when unblocked* are unchanged from the
pre-investigation audit. Same effort tier as PostHog.

- **Region routing.** Six possible bases (`app.datadoghq.com`, `us3.datadoghq.com`, `us5.datadoghq.com`, `app.datadoghq.eu`, `ap1.datadoghq.com`, `ap2.datadoghq.com`). GovCloud (`app.ddog-gov.com`, `us2.ddog-gov.com`) **not supported.** Aggregator must surface a region picker at consent and rebuild the upstream URL.
- **`?toolsets=` query param** for session-level narrowing. Default = `core` only. Examples: `?toolsets=synthetics`, `?toolsets=core,synthetics,software-delivery`, `?toolsets=all`. Maps cleanly to PostHog's `?features=` config.
- **`max_tokens` per-call argument** for response truncation. The agent passes this; we just pass-through.
- **Datadog-specific RBAC perms** (`mcp_read` / `mcp_write`) sit on top of resource-level perms. Admins can enforce read-only.
- **APM toolset is Preview, signup-required** — flag in connector description.
- **Built-in telemetry**: `datadog.mcp.session.starts`, `datadog.mcp.tool.usage`, plus Audit Trail "MCP Server" events. Tool calls are user-attributed.
- HIPAA-eligible.
- Rate limits: 50 calls / 10s burst, 5000/day, 50,000/month.

## Effort

**Code-side effort is already paid.** `connectors/datadog.py` exists in tree,
unit-tested locally (default `?toolsets=core`, `set-toolsets` validates the
19-name frozenset, config persists, `not_connected` path returns the right
shape with `connect_kind=oauth_dcr_pkce`). It is **not** in
`DCR_CONNECTORS`, so the aggregator never instantiates a Datadog backend
today. Re-enabling is one line in `connectors/__init__.py`.

**Remaining gating work:**

1. Get `hermes-*.chsandbox.com` (and the eventual production domain)
   allowlisted by Datadog. No self-serve form documented; will need a
   conversation with Datadog support / partnerships.
2. Confirm whether the allowlist is per-host or per-host-pattern — if Datadog
   only takes exact hosts, every customer-facing Hermes hostname needs to
   exist on their list. If so, we may need to relay through a single
   DOH-controlled redirect host and forward the code back to the customer
   container, similar to how Merge's relay works for tokens.
3. Re-test the full DCR + PKCE flow end-to-end once allowlisted.

## Sources

- https://docs.datadoghq.com/bits_ai/mcp_server/
- https://docs.datadoghq.com/bits_ai/mcp_server/setup/ — "If you cannot go through the OAuth flow (for example, on a server)..." API-key fallback.
- https://docs.datadoghq.com/bits_ai/mcp_server/tools/
- https://docs.datadoghq.com/account_management/rbac/permissions/#mcp
- https://modelcontextprotocol.io/specification/draft/basic/authorization — Open Redirection, Trust Policies, Client ID Metadata Documents priority order.
- Live AS metadata: `https://mcp.datadoghq.com/.well-known/oauth-authorization-server`
- Live protected-resource metadata: `https://mcp.datadoghq.com/.well-known/oauth-protected-resource`
- Reproduction script: probe scripts in this session's transcript (DCR + `/authorize` with `follow_redirects=False`, snapshot 2026-05-16).
