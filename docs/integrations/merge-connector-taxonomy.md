# Merge.dev Connector Taxonomy — Hermes Integrations

> Source: Hermes agent integrations page (`http://localhost:8788/`), section **CONNECTORS**.
> Generated: 2026-06-20. **Complete** — all 71 merge.dev connectors inspected; multi-step flows advanced to confirm their final step.

## How these connectors work

The Integrations page lists 83 entries. Querying the broker API behind it
(`GET /__humr_broker/integrations`) classifies them by `kind`:

| `kind`            | `category`       | count | notes |
|-------------------|------------------|-------|-------|
| `tls_intercept`   | `connector`      | 6     | Google, GitHub, Telegram, Slack, X, … — native/TLS-intercept connectors (NOT merge.dev) |
| `tls_intercept`   | `model_provider` | 5     | Anthropic, Nous, OpenRouter, OpenAI API, OpenAI Codex |
| `mcp_aggregator`  | `connector`      | 1     | PostHog |
| `merge_connector` | `connector`      | **71** | **The merge.dev connectors — the subject of this document** |

### The merge.dev connect flow

Clicking **Connect** on a `merge_connector` card calls
`POST /__humr_broker/integrations/merge/link-token` with `{"connector_slug": "<slug>"}`.
The broker returns a `magic_link_url` of the form
`https://ah-api.merge.dev/magic-link/<token>/` and opens it in a new tab.

That page is **Merge Link** — Merge.dev's hosted account-linking widget (rendered in a
cross-origin `ah-cdn.merge.dev` iframe). The **end of the flow** differs per provider:
this is the "possibility" being catalogued below.

## Taxonomy of end-of-flow possibilities

The distinct flow types observed at the end of the Merge Link widget:

- **A. OAuth (provider redirect)** — the widget shows a "Log in to <provider>" screen with an
  **Open window** button that redirects to the service provider's own OAuth consent screen.
  No secret is typed into Merge.
- **B. API key — single field** — one text field (e.g. "API key", "Personal access token",
  "Access token", "API token"). User pastes a secret obtained from the provider's dashboard.
- **C. API key + tenant identifier** — an API key/token field **plus** an additional field on
  the **same screen** identifying the tenant: an API URL, subdomain / company domain /
  account / region. (e.g. ActiveCampaign = API URL + key; BambooHR = subdomain + key.)
- **D. Multiple secrets** — two or more secret fields together, e.g. **API key + Secret key**
  (Amplitude), or key + secret + passphrase style forms.
- **E. Auth-method selector** — the widget first asks *"How would you like to authenticate?"*
  and offers a choice of methods (e.g. Cloudflare: **API Token** vs **Global API Key**); each
  branch then leads to its own form (usually B or C).
- **F. Multi-step — tenant first, then auth** — step 1 collects only a **Base URL / subdomain**
  (no secret yet); clicking **Next** advances to a second step that is itself OAuth or an
  API-token form. (e.g. Atlassian Confluence / Jira: Atlassian site name first.)

- **G. Error / not available** — the magic link loads but the widget shows *"We encountered an
  unexpected error — Please try again later"* (consistent across retries). The connector cannot
  be linked from this Merge config. (e.g. LinkedIn.)

> Note: A, B, C, D, E describe what the **first/only** screen shows. F flows are multi-screen;
> their final auth step is recorded in the detail column where advanced. `C+D` = a tenant
> identifier plus two or more secrets on the same screen.

---

## Per-connector classification

Legend for **Flow**: A = OAuth redirect · B = API key (single field) · C = API key + tenant field · D = multiple secrets · E = auth-method selector · F = multi-step (tenant → auth) · ? = pending

| # | Connector | slug | Flow | End-of-flow detail |
|---|-----------|------|------|--------------------|
| 1 | ActiveCampaign | activecampaign | C | API URL field + API key field. (Settings → Developer → copy API URL & key.) |
| 2 | Airtable | airtable | B | Single **Personal access token** field. (airtable.com/create/tokens → Create new token.) |
| 3 | Amplitude | amplitude | D | **API key** + **Secret key** fields. (Settings → Projects → copy API & Secret key.) |
| 4 | Apollo | apollo | B | Single **API key** field. (developer.apollo.io/keys → Create New Key.) |
| 5 | Asana | asana | A | "Log in to Asana" → **Open window** (OAuth). |
| 6 | Attio | attio | B | Single **Access token** field. (Settings → Developers → Generate access token.) |
| 7 | BambooHR | bamboohr | C | **Company subdomain** (`https://___.bamboohr.com`) + **API key** fields. |
| 8 | Calendly | calendly | A | "Log in to Calendly" → **Open window** (OAuth). |
| 9 | ClickUp | clickup | A | "Log in to ClickUp" → **Open window** (OAuth). |
| 10 | Cloudflare | cloudflare | E | "How would you like to authenticate?" → **API Token (Recommended)** or **Global API Key (Legacy)**. |
| 11 | Confluence | confluence | F→A | Step 1: Atlassian **Base URL** / site name (`___.atlassian.net`, no `/wiki`) → Next → step 2: "Log in to Confluence" **Open window** (OAuth). |
| 12 | Contentful | contentful | B | Single **Access token** field. (app.contentful.com/account/profile/cma_tokens → Generate personal token.) |
| 13 | Databricks | databricks | C | **Workspace URL** + **Personal access token** fields. |
| 14 | Datadog | datadog | C+D | **Datadog site** (datadoghq.com/.eu) + **API key** + **Application key** — tenant + two secrets. |
| 15 | Dropbox | dropbox | A | "Log in to Dropbox" → **Open window** (OAuth). |
| 16 | Dynamics 365 Sales | dynamics365 | F→A | Step 1: **Organization URL** (`yourorg.crm.dynamics.com`) → Next → step 2: "Log in to Dynamics 365 Sales" (Microsoft OAuth). ✓ advanced & verified |
| 17 | Expensify | expensify | D | **Partner user ID** + **Partner user secret** fields. (expensify.com/tools/integrations.) |
| 18 | Figma | figma | F→A | Step 1: **Team ID** (from team URL) → Next → step 2: "Log in to Figma" (OAuth). ✓ advanced & verified |
| 19 | Firecrawl | firecrawl | B | Single **API key** field. (firecrawl.dev/app/api-keys.) |
| 20 | Freshdesk | freshdesk | C | **Subdomain** (`___.freshdesk.com`) + **API key** fields. |
| 21 | GitLab | gitlab | A | "Log in to GitLab" → **Open window** (OAuth). |
| 22 | Gong | gong | D | **Access key** + **Access key secret** fields. (app.gong.io/company/api → Create.) |
| 23 | Grafana | grafana | C | **Instance URL** (`___.grafana.net`) + **Service account token** (`glsa_…`). |
| 24 | Greenhouse | greenhouse | D | **Client ID** + **Client secret** fields (Harvest V3 OAuth credentials, entered manually). |
| 25 | Hex | hex | B | Single **API token** field. (app.hex.tech → Settings → API keys.) |
| 26 | HubSpot | hubspot | A | "Log in to HubSpot" → **Open window** (OAuth). |
| 27 | Intercom | intercom | A | "Log in to Intercom" → **Open window** (OAuth). |
| 28 | Jenkins | jenkins | C+D | **Instance URL** + **Username** + **API token** (basic-auth style). |
| 29 | Jira | jira | F→A | Step 1: **Jira site domain** (`___.atlassian.net`) only → Next → step 2: "Log in to Jira" (Atlassian OAuth). ✓ advanced & verified |
| 30 | Jira Service Management | jira_service_management | C+D | **Site URL** (`___.atlassian.net`) + **Email** + **API token** — single screen (basic auth, *not* OAuth). |
| 31 | Klaviyo | klaviyo | B | Single **API key** field. (Settings → API keys → Create Private API Key.) |
| 32 | Linear | linear | A | "Log in to Linear" → **Open window** (OAuth). |
| 33 | LinkedIn | linkedin | G | Magic link loads but shows **"We encountered an unexpected error"** (reproduced on retry). |
| 34 | Looker | looker | C+D | **Instance URL** + **Client ID** + **Client secret** (API3 keys). |
| 35 | Microsoft Teams | microsoft_teams | A | "Log in to Microsoft Teams" → **Open window** (OAuth). |
| 36 | Miro | miro | A | "Log in to Miro" → **Open window** (OAuth). |
| 37 | Monday.com | monday | B | Single **API token** field. (Admin → API.) |
| 38 | n8n | n8n | C | **Instance URL** + **API key** fields. |
| 39 | Notion | notion | A | "Log in to Notion" → **Open window** (OAuth). |
| 40 | OneDrive | onedrive | A | "Log in to OneDrive" → **Open window** (OAuth). |
| 41 | Outlook | outlook | A | "Log in to Outlook" → **Open window** (OAuth). |
| 42 | PagerDuty | pagerduty | D | **API key** + **PagerDuty user email** fields. |
| 43 | PayPal | paypal | D | **Client ID** + **Client secret** + **Environment** (sandbox/production). |
| 44 | Pipedrive | pipedrive | G | Magic link loads but shows **"We encountered an unexpected error"** (reproduced on retry). |
| 45 | Salesforce | salesforce | D | **Instance domain** (My Domain) + **Username** + **Password** + **Security token** + **Organization ID** — full basic-auth, 5 fields. |
| 46 | Sanity | sanity | C+D | **Project ID** + **Dataset name** + **API token**. |
| 47 | SendGrid | sendgrid | C | **Data region** dropdown (US / Global) + **API key**. |
| 48 | Sentry | sentry | A | "Log in to Sentry" → **Open window** (OAuth). |
| 49 | ServiceNow | servicenow | F→A | Step 1: **Instance URL** (`___.service-now.com`) only → Next → step 2: "Log in to ServiceNow" (OAuth). ✓ advanced & verified |
| 50 | SharePoint | sharepoint | A | "Log in to SharePoint" → **Open window** (OAuth). |
| 51 | Shopify | shopify | F→A | Step 1: **Shop domain** (`___.myshopify.com`) only → Next → step 2: "Log in to Shopify" (OAuth). ✓ advanced & verified |
| 52 | Smartsheet | smartsheet | A | "Log in to Smartsheet" → **Open window** (OAuth). |
| 53 | Snowflake | snowflake | E | "How would you like to authenticate?" → **Key-pair authentication** or **Programmatic access token**. |
| 54 | Square | square | B | Single **Access token** field. (Square Developer Dashboard → Credentials.) |
| 55 | Stripe | stripe | B | Single **Secret API key** field. (dashboard.stripe.com/apikeys.) |
| 56 | Supabase | supabase | A | "Log in to Supabase" → **Open window** (OAuth). |
| 57 | Trello | trello | D | **API key** + **API token** fields. (trello.com/power-ups/admin.) |
| 58 | TripAdvisor | tripadvisor | B | Single **API key** field. (tripadvisor.com/developers.) |
| 59 | Vercel | vercel | B | Single **API token** field. (vercel.com/account/tokens.) |
| 60 | Weather | weather | G | Magic link loads but shows **"We encountered an unexpected error"** (reproduced on retry). |
| 61 | Webflow | webflow | B | Single **API token** field. (Site settings → Apps & integrations → API access.) |
| 62 | Wix | wix | C | **Site ID** + **API key** fields. (Wix API Keys Manager.) |
| 63 | WordPress | wordpress | F→A | Step 1: **Blog URL** (WordPress.com site domain) only → Next → step 2: "Log in to WordPress" (OAuth). ✓ advanced & verified |
| 64 | Workday | workday | C+D | **REST API base URL** + **Tenant** + **Authorization base URL** (+ likely client credentials below) — multi-field tenant config. |
| 65 | Yelp | yelp | B | Single **API key** field. (yelp.com/developers/v3/manage_app.) |
| 66 | YouTube | youtube | A | "Log in to YouTube" → **Open window** (OAuth / Google). |
| 67 | Zendesk | zendesk | C+D | **Subdomain** (`___.zendesk.com`) + **Email** + **API token** — basic auth. |
| 68 | Zendesk Sell | zendesk_sell | B | Single **Access token** field. (Settings → Integrations → OAuth → Access Tokens.) |
| 69 | Zoho CRM | zohocrm | F→A | Step 1: **Data center** (region) only → Next → step 2: "Log in to Zoho CRM" (OAuth; admin may need an app at api-console.zoho.com). ✓ advanced & verified |
| 70 | Zoho Desk | zohodesk | F→A | Step 1: **Organization ID** only → Next → step 2: "Log in to Zoho Desk" (OAuth). ✓ advanced & verified |
| 71 | Zoom | zoom | A | "Log in to Zoom" → **Open window** (OAuth). |

---

## Summary by flow type (all 71 classified)

| Flow | Meaning | Count | Connectors |
|------|---------|-------|-----------|
| **A** | OAuth redirect (single screen) | 19 | Asana, Calendly, ClickUp, Dropbox, GitLab, HubSpot, Intercom, Linear, Microsoft Teams, Miro, Notion, OneDrive, Outlook, Sentry, SharePoint, Smartsheet, Supabase, YouTube, Zoom |
| **F→A** | Tenant identifier first → then OAuth | 9 | Confluence, Dynamics 365 Sales, Figma, Jira, ServiceNow, Shopify, WordPress, Zoho CRM, Zoho Desk |
| **B** | Single API key / token field | 15 | Airtable, Apollo, Attio, Contentful, Firecrawl, Hex, Klaviyo, Monday.com, Square, Stripe, TripAdvisor, Vercel, Webflow, Yelp, Zendesk Sell |
| **C** | API key + tenant identifier (1 screen) | 8 | ActiveCampaign, BambooHR, Databricks, Freshdesk, Grafana, n8n, SendGrid, Wix |
| **D** | Multiple secrets (no tenant URL) | 8 | Amplitude, Expensify, Gong, Greenhouse, PagerDuty, PayPal, Salesforce, Trello |
| **C+D** | Tenant identifier + multiple secrets | 7 | Datadog, Jenkins, Jira Service Management, Looker, Sanity, Workday, Zendesk |
| **E** | Auth-method selector (branches) | 2 | Cloudflare, Snowflake |
| **G** | Error — "unexpected error" (not linkable) | 3 | LinkedIn, Pipedrive, Weather |
| | **Total** | **71** | |

## Higher-level buckets (mapping to "API key form vs OAuth app")

Collapsing the fine-grained types into the two macro-possibilities the question asked about,
plus the two edge cases discovered:

- **OAuth app (provider redirect)** — **28** connectors (A + F→A). The widget never collects a
  secret; it shows an *"Open window"* button that sends the user to the provider's own consent
  screen. 9 of these first collect a tenant identifier (subdomain / site / org / domain /
  region) so Merge knows which instance to send the OAuth request to, then redirect.
- **Credential entry form (API key / token / basic auth)** — **38** connectors (B + C + D + C+D).
  The user pastes secrets generated in the provider's dashboard directly into Merge. Sub-shapes:
  single token (B, 15) · token + tenant field (C, 8) · two-or-more secrets (D, 8) · tenant +
  multiple secrets incl. username/password (C+D, 7).
- **Auth-method selector** — **2** connectors (E). The widget first asks *"How would you like to
  authenticate?"* and the user picks a method, each leading to its own credential form:
  - Cloudflare → *API Token (Recommended)* or *Global API Key (Legacy)*
  - Snowflake → *Key-pair authentication* or *Programmatic access token*
- **Error / not available** — **3** connectors (G). LinkedIn, Pipedrive, and Weather load the
  magic link but render *"We encountered an unexpected error — Please try again later"*
  (reproduced on retry; minting the link token succeeds, so the failure is on Merge's side —
  likely an unconfigured/unavailable integration in this Merge account).

## Notable observations

- **Atlassian is not uniform.** Jira and Confluence end in **OAuth** (after a site-name step),
  but **Jira Service Management** uses a **basic-auth form** (site URL + email + API token) —
  three different sub-flows from the same vendor family.
- **Microsoft products are all OAuth** (Dynamics 365, Microsoft Teams, OneDrive, Outlook,
  SharePoint) — Dynamics additionally collects the org URL first.
- **Tenant identifier is the most common pre-field**: 24 connectors require a subdomain / site
  URL / instance URL / domain / region / org-id somewhere in the flow (the C, C+D, and F→A
  groups), reflecting self-hosted or multi-region products.
- **"API key" is not monolithic** — it ranges from a single pasted token (Stripe, Airtable) to
  full basic-auth with username + password + security token + org id (Salesforce).
- **Every Merge connector's *first* screen is one of**: "Log in to X" (OAuth), "Enter your
  account details" (credential form), "How would you like to authenticate?" (selector), or the
  error screen. The label on the first screen is a reliable signal of the flow type.

## Method / reproducibility

- Connector inventory: `GET http://localhost:8788/__humr_broker/integrations` → 71 items with
  `kind == "merge_connector"`.
- Magic links minted via `POST http://localhost:8788/__humr_broker/integrations/merge/link-token`
  with body `{"connector_slug": "<slug>"}` → returns `{magic_link_url, link_token}`.
- Each magic link opened at `https://ah-api.merge.dev/magic-link/<token>/` and the Merge Link
  widget (cross-origin `ah-cdn.merge.dev` iframe) inspected visually. Multi-step (F) flows were
  advanced by entering a dummy tenant value and clicking **Next** to reveal step 2.
- No real credentials were entered and no account was actually connected; all dummy values were
  placeholders used only to reach the next screen.
