# DCR candidates — summary

This directory holds one report per Group 1 vendor identified in
`../merge_connectors_auth_audit.md`. Each report covers:

1. Whether the vendor's first-party MCP server is real and accessible via
   Dynamic Client Registration today.
2. Whether Merge.dev does a good job integrating that vendor.
3. Whether a direct integration (bypassing Merge) is advantageous.
4. What mechanisms beyond the Notion baseline an aggregator would have to
   handle for that vendor.
5. Effort estimate.

The PostHog worktree introduced two patterns we now know we need to
support: **explicit per-resource scopes** at the OAuth consent step, and
**per-session config** that rebuilds the upstream URL with query params
narrowing the tool surface (`?features=`, `?tools=`, `project_id`, etc.).
A connector spec (`DCRConnectorSpec` in `connectors/_common.py`) carries
the static config; per-session config is persisted next to the
client.json/token.json, with meta-tools to inspect and change it.

## Per-vendor verdicts at a glance

Effort buckets:

- **Low**: Notion-shape. No scopes, no per-session config. Drop in a
  connectors/<slug>.py and add to `DCR_CONNECTORS`.
- **Medium**: Adds a `default_scope` string. No per-session config.
- **High**: Full PostHog-shaped — per-session config, meta-tools to
  inspect/change it, dynamic upstream URL.
- **Blocked**: Cannot proceed without vendor action (allowlist, regional
  support, missing docs).

```
+---------------+------------------+------------------+----------------------------------------------------------------+
| Vendor        | Direct wins?     | Effort           | New mechanism beyond Notion                                    |
+---------------+------------------+------------------+----------------------------------------------------------------+
| linear        | marginal         | low              | none                                                           |
| atlassian     | yes for          | medium           | scope tiers, cloudId discovery, endpoint sunset 2026-06-30     |
| (jira/jsm/    | jira+confluence  |                  |                                                                |
| confluence)   |                  |                  |                                                                |
| asana         | marginal         | low              | workspace pinned at consent, preview widgets                   |
| intercom      | no (US-only      | low + region     | hard region restriction                                        |
|               | blocker)         | check            |                                                                |
| sentry        | yes              | high             | path-based scoping, skills system, regionUrl, embedded agents, |
|               |                  |                  | self-hosted instance URL                                       |
| stripe        | yes (security    | medium           | sandbox vs live mode (two connectors)                          |
|               | posture)         |                  |                                                                |
| paypal        | marginal         | medium           | `x-feature-flags` header, sandbox/prod hosts                   |
| square        | n/a              | blocked          | production-only, dynamic surface                               |
|               |                  | (allowlist)      |                                                                |
| figma         | yes if approved  | blocked          | plan-conditional tool visibility, rate-limit signaling         |
|               |                  | (allowlist)      |                                                                |
| canva         | marginal         | medium           | granular per-resource scopes, plan gating                      |
| wix           | no               | skip             | umbrella-tool pattern                                          |
| webflow       | no               | skip             | umbrella-tool, Bridge App requirement                          |
| monday        | marginal         | low              | UI widget tools, marketplace install required                  |
| airtable      | marginal         | medium           | 7 scopes, post-hoc base allowlist, enterprise client_id        |
|               |                  |                  | allowlist                                                      |
| datadog       | yes              | high             | regions, `?toolsets=`                                          |
| miro          | marginal         | low-medium       | 2 scopes, team pinned at install                               |
| clickup       | n/a              | blocked          | 2 scopes, plan-conditional rate limits                         |
|               |                  | (redirect        |                                                                |
|               |                  | allowlist)       |                                                                |
| make          | yes              | high             | scenarios-as-tools (dynamic), URL narrowing, async execution   |
| contentful    | marginal         | low              | none                                                           |
| sanity        | yes (modest)     | low              | single coarse `global` scope                                   |
| attio         | yes (modest)     | medium           | AS host ≠ resource host, workspace baked into token            |
| klaviyo       | yes (modest)     | medium           | `?read-only=`, `?disable-tools-with-user-generated-content=`,  |
|               |                  |                  | `?company=`                                                    |
| ramp          | yes if approved  | blocked          | 27 fine-grained scopes, three-host OAuth, alpha gating         |
|               |                  | (redirect        |                                                                |
|               |                  | allowlist)       |                                                                |
| gitlab        | yes              | high             | 26 scopes incl. `mcp`/`mcp_orbit`, dual endpoints,             |
|               |                  |                  | self-managed instance URLs                                     |
| lucidchart    | unknown          | defer            | docs sparse                                                    |
+---------------+------------------+------------------+----------------------------------------------------------------+
```

## Recommended implementation order

This ranks by "ratio of customer value to engineering work, with
attention to which mechanisms generalize."

### Tier 1 — ship soon

These are all tractable, several are Notion-shape, and they together
de-risk the medium-tier targets that follow.

1. **linear** — Notion-shape, nothing to learn. (Low.)
2. **asana** — Notion-shape; minor wrinkle about V2 endpoint pinning. (Low.)
3. **contentful** — Notion-shape. AI Actions are first-class tools. (Low.)
4. **sanity** — Notion-shape with `default_scope = "global"`. Adds AI image
   tools, semantic search, and `deploy_studio`. First connector with a
   non-empty scope string. (Low.)
5. **monday** — Notion-shape; flag `all_monday_api` as a security
   concern. (Low.)

### Tier 2 — exercise PostHog-pattern mechanisms

These add `default_scope` + (in some cases) a small per-session config.
They validate the connector spec for the more complex Tier 3 targets.

6. **miro** — `boards:read`, `boards:write`. Team pinned at consent. (Low-medium.)
7. **stripe** — Sandbox vs live mode as two connectors. No scopes. (Medium.)
8. **canva** — Granular per-resource scopes (read+write split). (Medium.)
9. **airtable** — 7 scopes; flag enterprise allowlist gotcha. (Medium.)
10. **attio** — Confirms our OAuth code follows AS-vs-resource
    indirection. (Medium.)
11. **atlassian (jira+jsm+confluence)** — Tier 1 of the scope-tier
    targets. Single connector covering three Merge connectors plus
    Bitbucket and Compass for free. **Endpoint sunset 2026-06-30** —
    deadline pressure. (Medium.)

### Tier 3 — full PostHog-shape

These need per-session config and meta-tools, the same shape we already
built for PostHog. The work generalizes — once one of these is solid the
others fall in line.

12. **datadog** — 18 toolsets via `?toolsets=`, six regional hosts. The
    most direct PostHog clone in mechanism. **Highest customer value of
    the Tier 3 set** because Merge's 49 tools dramatically under-shoot
    Datadog's 110+. (High.)
13. **klaviyo** — 2-3 boolean URL toggles + `?company=`. Smaller scope
    than PostHog/Datadog but exercises the same plumbing. (Medium-high.)
14. **make** — Scenarios-as-tools means dynamic per-session tool
    discovery + async execution beyond MCP timeout. **The most novel
    mechanism in the audit.** (High.)
15. **paypal** — Header-based session config (`x-feature-flags`) instead
    of query params. Sandbox vs prod = two connectors. (Medium.)
16. **sentry** — Layered OAuth, path-based scoping, skills system,
    embedded-agent tools. **The most sophisticated vendor in the audit.**
    (High.)
17. **gitlab** — Self-managed instance URL handling, 26 scopes, dual
    endpoints. Heaviest of the Tier 3 set. (High.)

### Tier 4 — defer or skip

- **lucidchart** — sparse docs. Recheck in 1-2 months.
- **wix**, **webflow** — umbrella-tool pattern doesn't fit our catalog
  model. Merge already serves these well.
- **intercom** — US-only blocker. Merge handles EU/AU customers; direct
  integration is a downgrade for non-US tenants.

### Blocked by vendor allowlists

- **square** — wait for Square to lift its client allowlist or exit beta.
- **figma** — apply to Figma MCP Catalog if we want the canvas-authoring
  features.
- **clickup** — submit redirect URI for review.
- **ramp** — coordinate with Ramp support to register our redirect URI.

## Mechanisms inventory

What new aggregator capabilities are required beyond what the Notion +
PostHog implementations cover today.

### Already handled by the PostHog worktree

- `default_scope` on the spec.
- Per-session config persisted next to client.json/token.json.
- Meta-tools to inspect/change session config.
- Dynamic upstream URL rebuilt per-call from session config.

### Still required for Tier 3+ work

- **Region picker at consent.** Datadog has six regions, no central
  router. The integration card needs a region dropdown before the OAuth
  start. Sentry and GitLab self-hosted have the same shape with arbitrary
  user-supplied URLs.
- **Per-session HTTP headers**, not just URL query params. PayPal uses
  `x-feature-flags`; the aggregator currently rebuilds URLs but doesn't
  send per-session headers upstream.
- **Disable tool-list caching for "dynamic" connectors.** Make's
  scenarios-as-tools and Figma's plan-conditional tool visibility both
  break the assumption that tool lists are static across sessions.
- **Async execution beyond MCP timeout.** Make scenarios run for 40
  minutes; the timeout response includes `executionId` and a follow-up
  poll tool. Aggregator must surface this as a normal tool result, not
  treat the timeout as a failure.
- **Configurable upstream host per connection.** Sentry self-hosted,
  GitLab self-managed, Datadog regions. The connector spec today
  hard-codes `upstream_url` and `oauth_metadata_url`. For these vendors
  the customer supplies the host at connect time and the spec resolves
  metadata against that host.
- **Two-connector pattern for sandbox/production.** Stripe and PayPal
  both have separate sandbox hosts. Either two connectors per vendor or a
  per-connection `mode` field. Recommend two connectors for clarity.
- **Allowlist precondition signaling.** When DCR succeeds but the OAuth
  authorization rejects an unknown redirect URI (Square, ClickUp, Ramp,
  Figma), surface a clear "this client must be allowlisted by <vendor>"
  error in the integrations panel rather than a generic OAuth failure.
- **Cross-vendor consistency on tool annotations.** Some vendors mark
  write tools as destructive / requires-confirmation (Attio, ChatGPT
  preview tools). Aggregator should honor those annotations in its
  catalog, surfacing them through `integrations_describe_tool` so the
  agent's mutation classifier doesn't have to reverse-engineer them
  from tool names.

## What's NOT in this audit

- **OIDC-only DCR** vendors (Vercel, Fireflies, Pylon, Figma) — Group 1
  disqualified them. Vercel was the canonical "DCR endpoint exists but is
  unusable" case; this audit excludes them by design. See the parent
  `merge_connectors_auth_audit.md` for the disqualification logic.
- **Vendors not in our Merge Tool Pack.** GitHub MCP, OpenAI/Anthropic
  first-party connectors, Notion (already integrated), PostHog (in
  flight), and any vendor Merge doesn't carry are out of scope.
- **Self-hosted vendor MCPs** without a `.well-known` endpoint exposed.
  GitLab self-managed and Sentry self-hosted both work in principle, but
  this audit only covers the cloud SaaS path.

## Sources

- `../merge_connectors_auth_audit.md` — the parent audit that produced
  the candidate list.
- Vendor docs and OAuth metadata, cited per file.
- The PostHog worktree at `.claude/worktrees/posthog-mcp/` — the working
  reference for the connector spec and per-session-config pattern.
