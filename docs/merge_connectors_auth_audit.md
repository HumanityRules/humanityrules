# Merge connectors — authentication audit

How each of the 151 connectors in our Merge Tool Pack
(`MERGE_TOOL_PACK_ID = 312815f3-65f6-4db2-ba06-bfe9ec998284`, snapshotted
2026-05-15) authenticates.

This doc answers two **independent** questions:

1. **What does Merge actually do for this connector today?** What does the
   user see at connect time — an OAuth click-through, an API-key paste box,
   two separate keys to fill in, etc. This is the only question that
   determines today's UX in our broker.
2. **Does the vendor — at official, vendor-controlled infrastructure —
   expose a remote MCP server with OAuth Dynamic Client Registration (the
   "Notion model")?** This is independent of Merge: it tells us where we
   *could* bypass Merge entirely and use the existing `mcp_aggregator.py`
   Notion path.

The two questions don't determine each other. Datadog hosts an official
MCP server with DCR at `mcp.datadoghq.com`, AND Merge integrates Datadog
via a two-key API form ([screenshot evidence][datadog-screenshot]). Both
true at the same time. Earlier versions of this doc conflated the two and
got several entries wrong (Datadog, Vercel). The structure below keeps
them separate.

[datadog-screenshot]: see attachment in the conversation that produced this doc

## Sources

- **Merge per-connector docs**: `https://docs.merge.dev/merge-agent-handler/connectors/<slug>`
  — each page has an `**Authentication:**` line and links to the BYO
  OAuth-app admin-setup page when applicable. Slug-list mismatch between
  Merge's catalog page (111) and the Tool Pack API (151) is real — newer
  connectors aren't on the index but per-connector pages are reachable
  directly.
- **Live Tool Pack catalog**: `GET /api/v1/tool-packs/{id}/connectors/`.
- **Vendor OAuth metadata**, for the second question only:
  `https://mcp.<vendor>/.well-known/oauth-authorization-server` and the
  paired `oauth-protected-resource` document. DCR live-tested against
  `mcp.linear.app/register` and `cf.mcp.atlassian.com/v1/register`. We
  examined `scopes_supported` to flag vendors whose DCR endpoint issues an
  OIDC-only login client (no API-call scope).


---

# Part 1 — How Merge integrates each connector

This is the part that determines today's UX. Source is Merge's own
per-connector docs.

## Headline counts

- **88** OAuth via Merge — Merge ships a default OAuth app and the user
  gets a click-through OAuth flow. All 88 also link to the
  "Application credentials" admin-setup page (BYO OAuth app override).
  - 32 are OAuth-only.
  - 56 also accept some kind of static-token alternative.
- **56** ad-hoc credentials only — no OAuth. User has to generate creds
  in the vendor UI and paste them in.
- **6** public APIs — no auth at all.
- **1** undocumented (`compliancequest`).


## Group A — OAuth via Merge with BYO override (88)

Merge runs the OAuth dance against the vendor for the user. By default
the OAuth app is Merge's own pre-registered app; the customer can override
it with their own `client_id`/`client_secret` per the
"Application credentials" admin-setup flow Merge documents on each
connector page.

### A1 — OAuth only (32)

basecamp, bitbucket, box, canva, docusign, dropbox, foursquare, frameio,
freshbooks, gmail, google_bigquery, google_calendar, google_docs,
google_drive, google_meet, google_sheets, google_slides, google_tasks,
linkedin, oura, quickbooks_online, ramp, slack, spotify, strava, whoop,
workday, xero, youtube, zohocrm, zoom, zoominfo.

### A2 — OAuth or static-token alternative (56)

User picks at connect time. Each entry: API slug — `Authentication:` line
from the docs.

- airtable — OAuth or personal access token
- anaplan — OAuth, basic authentication / auth token, or certificate
  authentication
- asana — OAuth or personal access token
- bitly — OAuth or access token
- calendly — OAuth or personal access token
- clickup — OAuth or API token
- confluence — OAuth or API token
- contentful — OAuth or personal access token
- databricks — OAuth or personal access token
- dynamics365 — OAuth or access token
- factset — OAuth or API key
- figma — OAuth or personal access token
- freshservice — OAuth or API key
- front — OAuth or API token
- github — OAuth or personal access token
- gitlab — OAuth or private token
- gong — OAuth or API key
- google_maps — OAuth or API key
- guru — OAuth or API token
- hubspot — OAuth or private app token
- intercom — OAuth or access token
- jira — OAuth or email and API token
- jira_service_management — OAuth or email and API token
- klaviyo — OAuth or API key
- linear — OAuth or API key
- lucidchart — OAuth or API key
- microsoft_teams — OAuth or access token
- microsoft_teams_gcc — OAuth or access token
- miro — OAuth or access token
- monday — OAuth or API token
- netsuite — OAuth or token-based authentication
- notion — OAuth or integration token
- onedrive — OAuth or access token
- onenote — OAuth or access token
- oracle_hcm — OAuth or basic authentication
- oracle_sales_cloud — OAuth or basic authentication
- oracle_scm — OAuth or basic authentication
- outlook — OAuth or access token
- pagerduty — OAuth or API key
- pipedrive — OAuth or API token
- salesforce — OAuth or username/password + security token
- sentry — OAuth or API token
- servicenow — OAuth or basic authentication
- sharepoint — OAuth or access token
- shopify — OAuth or private app credentials
- smartsheet — OAuth or personal access token
- snowflake — OAuth, key-pair authentication, or programmatic access token
- square — OAuth or personal access token
- supabase — OAuth or personal access token
- vercel — OAuth or API token
- webflow — OAuth or site token
- wordpress — OAuth or application password
- x — OAuth or bearer token
- zendesk — OAuth or API token
- zendesk_sell — OAuth or access token
- zohodesk — OAuth or API token


## Group B — Ad-hoc credentials only (56)

No OAuth path. The user has to generate something in the vendor UI and
paste it. Sub-groups by credential shape, since the UX of "paste one
string" is meaningfully different from "paste a key + a secret + a
domain".

### B1 — Single API key / API token (40)

Most common shape. One bearer-style string the user pastes in.

activecampaign, ahrefs, amadeus, apollo, arize, articulate, attio,
aviationstack, bamboohr, cloudflare (`API token or global API key`),
coda, crustdata, duffel, exa, firecrawl, fireflies, firehydrant,
freshdesk, gamma, hex, jenkins, make, n8n, peec, posthog, pubmed, pylon,
quartr, readme, rootly, sabre, sanity, sendgrid, straker,
stripe (`secret API key`), teamwork, tripadvisor, vestaboard, wix, yelp.

### B2 — Two-part credential pair (5)

Two values, both required.

- amplitude — API key and secret key
- datadog — API key + application key (UI also asks for the Datadog site
  domain — observed in the Magic Link form)
- doordash — API credentials
- looker — API credentials
- trello — API key and token

### B3 — Cloud-platform / OAuth-client-credentials (3)

Two-part, but the credentials are scoped IAM identities or
client-credentials grants — security teams will care about how they're
issued and rotated.

- amazon_s3 — IAM access keys
- adobe_pdf_services — client credentials (OAuth `client_credentials`
  grant, non-user-bound)
- greenhouse — client credentials

### B4 — Username + password (2)

Plain primary login.

- kintone — password authentication
- visualping — email and password

### B5 — Vendor-specific oddities (6)

- expensify — "partner credentials" (Expensify's bespoke partner-API
  credentials)
- grafana — service account token (Grafana service-account UI; per-org)
- hibob — service user (requires a non-human user account)
- paypal — API credentials
- sapsf — basic authentication (HTTP basic, SAP's older flavor)
- ukg_pro — username + password + customer API key (three values)


## Group C — Public APIs (6)

No authentication at all.

biorxiv, clinicaltrials, cms_coverage, npi_registry, weather, wikipedia.


## Group D — Undocumented (1)

`compliancequest` has no `Authentication:` line on its docs page.


---

# Part 2 — Vendors with an official remote MCP server + DCR

Independent of Merge. These are vendors that, at infrastructure they
control, host a remote MCP server whose OAuth metadata advertises an
RFC 7591 `registration_endpoint` — i.e. a third-party MCP client
(including Hermes's `mcp_aggregator.py`) can register itself dynamically
and walk a PKCE OAuth flow without any human pre-registering an OAuth app.
This is the same pattern Hermes already runs against Notion today.

A vendor being in this list is **not** a statement about how Merge
integrates them — that's Part 1. It's purely about whether we *could*
bypass Merge for that vendor.

## Qualifying — DCR works AND issues clients with API scopes (29)

The AS metadata advertises a working `registration_endpoint`, and the
issued client carries scopes capable of calling the vendor's API (or
leaves `scopes_supported` empty and validates on the resource server, as
Notion does). Each entry: API slug, registration endpoint, scope hint
where useful.

- **notion** — `mcp.notion.com/register` (no `scopes_supported`;
  resource-server-validated; this is the pattern Hermes already runs
  against)
- **linear** — `mcp.linear.app/register` (DCR live-tested)
- **jira**, **jira_service_management**, **confluence** — share
  `mcp.atlassian.com`; register at `cf.mcp.atlassian.com/v1/register`
  (DCR live-tested)
- **asana** — `mcp.asana.com/register`
- **intercom** — `mcp.intercom.com/register`
- **sentry** — `mcp.sentry.dev/oauth/register` (`org:read`,
  `project:write`, `team:write`, `event:write`)
- **stripe** — `access.stripe.com/mcp/oauth2/register`
- **paypal** — `mcp.paypal.com/register`
- **square** — `mcp.squareup.com/register`
- **figma** — `api.figma.com/v1/oauth/mcp/register` (`mcp:connect`)
- **canva** — `mcp.canva.com/register`
- **wix** — `mcp.wix.com/register`
- **webflow** — `mcp.webflow.com/oauth/register`
- **monday** — `mcp.monday.com/register`
- **airtable** — `airtable.com/oauth2/v1/register` (real Airtable scopes)
- **datadog** — `mcp.datadoghq.com/api/unstable/mcp-server/register`
- **miro** — `mcp.miro.com/register` (`boards:read/write`)
- **clickup** — `mcp.clickup.com/oauth/register` (`read`, `write`)
- **make** — `make.com/oauth/v2/register/mcp`
- **contentful** — `mcp.contentful.com/register`
- **sanity** — `mcp.sanity.io/register` (`global`)
- **posthog** — `oauth.posthog.com/oauth/register/` (171 PostHog scopes)
- **attio** — `app.attio.com/oauth/register` (`mcp` scope per
  WWW-Authenticate)
- **klaviyo** — `mcp.klaviyo.com/register`
- **ramp** — `mcp.ramp.com/register` (real Ramp scopes)
- **gitlab** — `gitlab.com/oauth/register` (real GitLab scopes incl.
  `api`, `mcp`)
- **lucidchart** — `mcp.lucid.app/oauth/register`

## Disqualified — DCR exists at the protocol level but unusable end-to-end (3)

These vendors return a `client_id` from DCR but the issued client can't
actually drive an MCP session. They're listed here separately so we don't
mistake protocol surface for a usable path.

- **vercel** — `vercel.com/api/login/oauth/register`. Two issues: (1)
  AS scopes are OIDC-only (`openid`, `email`, `offline_access`,
  `profile`), so the issued client can identify the user but can't
  request scopes that authorize Vercel API calls; (2) Vercel's own MCP
  docs state *"Vercel MCP only supports AI clients that have been
  reviewed and approved by Vercel"* — even with a `client_id`, an
  unsanctioned client won't get a real session.
- **fireflies** — `mcp.fireflies.ai/register`. AS scopes are
  `profile`, `email` only (login-only).
- **pylon** — `o.auth.usepylon.com/oauth2/register`. AS scopes are
  `openid`, `email`, `profile`, `offline_access` only (login-only).

The disqualifying test, for future audits:

1. Fetch `oauth-authorization-server` and check `scopes_supported`.
2. Empty/unset is OK — Notion-style servers leave it empty and validate
   scopes server-side.
3. If present *and* every scope is in
   `{openid, email, profile, offline_access}`, treat as **disqualified —
   login-only**.
4. Otherwise candidate. Final confirmation needs an end-to-end test
   (Vercel proves an MCP server can publish real-looking metadata and
   still gate clients).

## Vendors with remote MCP but no DCR

These advertise an MCP-flavored OAuth metadata document but point at a
static OAuth provider that requires a vendor-issued `client_id` — i.e.
**not** the Notion model. Listed for completeness.

slack, hubspot, zoom, docusign, smartsheet, pagerduty, gong, github
(points at `github.com/login/oauth`).


---

# Part 3 — How the two parts interact

The interesting cross-cut is "vendor offers DCR AND Merge picks something
non-OAuth anyway", because that's the population where Merge made a
deliberate choice that we wouldn't have to repeat if we bypassed them.

**Vendor DCR + Merge OAuth (21)** — both paths exist; we could swap Merge
for direct DCR with no functional regression: notion, linear, jira,
jira_service_management, confluence, asana, intercom, sentry, square,
figma, canva, webflow, monday, airtable, miro, clickup, contentful,
klaviyo, ramp, gitlab, lucidchart.

**Vendor DCR but Merge picks API key (8)** — likely because Merge needs
REST endpoints the vendor's MCP server doesn't proxy, or because the
vendor's free tier doesn't expose OAuth. If we ever want OAuth-flavored
UX for these, we'd have to leave Merge:

- stripe (Merge: `secret API key`)
- paypal (Merge: `API credentials`)
- wix (Merge: `API key`)
- datadog (Merge: `API key + application key`)
- make (Merge: `API token`)
- sanity (Merge: `API token`)
- posthog (Merge: `API key`)
- attio (Merge: `API key`)

**Vendor DCR disqualified as login-only (3)** — vercel, fireflies, pylon.
Merge's choice to integrate via API key (or pre-approved OAuth app, in
Vercel's case) is consistent with the disqualification: a third party
can't actually use these vendors' DCR endpoints.


---

# Caveats

- Tool Pack catalog drifts; numbers above are a 2026-05-15 snapshot.
- Merge's per-connector docs are terse one-liners. They tell us *what*
  Merge supports, not necessarily what the Magic Link UI actually
  renders. The Datadog screenshot in the conversation that produced this
  doc was the only ground-truth UI check we did. For any connector we
  actually depend on, drive Magic Link once and see what fields the user
  is asked to fill in before declaring this doc authoritative for that
  connector.
- "Bring-your-own OAuth credentials" detection is link-based: every
  connector with `OAuth` in its auth string also links to the (currently
  404) `/admin-setup/add-oauth-credentials` page. The 88/88 ratio is too
  clean to be 88 separately verified capabilities — assume it's a
  templated link.
- DCR working unauthenticated against a public well-known is necessary,
  not sufficient, for Part 2. Vercel is the cautionary example: DCR
  returns a valid `client_id`, but the AS only issues OIDC scopes *and*
  the resource server allowlists clients, so a Notion-style flow can't
  actually drive tool calls. End-to-end test required before bypassing
  Merge for any Part 2 candidate.
