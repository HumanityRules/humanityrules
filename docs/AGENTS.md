# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.**

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/app_deployment_blueprint_spec.md`** — Specification for the app deployment blueprint format and lifecycle. Open when working on blueprint generation, validation, or the deployment pipeline.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/integrations_broker_design.md`** — Architecture for third-party OAuth integrations in Hermes (Google Workspace today; Slack/Notion/Linear next). Answers where the refresh token lives (DOH control plane), what crosses the DOH/customer boundary (nothing — access tokens stay in the broker's memory, the sandbox sees only a placeholder), and how the in-container HTTPS forward proxy terminates TLS with an on-demand DOH-signed leaf and swaps the Authorization header. Open when reasoning about the trust boundary, adding a new provider, or debugging the integrations broker.

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Datastore, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/policy_proxy_design.md`** — Runtime design for the policy proxy that enforces SSO + ABAC in front of deployed apps. Covers the per-env auth Lambda (Okta OAuth, JWT minting, JWKS), the policy-proxy container (JWT verification, per-request PDP calls with caching), the DOH PDP endpoint contract, and the Personal Assistant specifics (owner tag, global self-referential policy). Open when working on the policy proxy, the auth Lambda, or the PDP endpoint.

- **`docs/mcp_aggregator_design.md`** — Architecture for MCP server integration in Hermes (Notion today; others tomorrow). Covers the MCP aggregator process (folded into integrations_broker.py), OAuth 2.1 with DCR+PKCE, token custody outside the sandbox, tool discovery via `notifications/tools/list_changed`, and the WebUI OAuth callback flow. Open when adding MCP providers, working on the aggregator, or reasoning about the sandbox ↔ MCP trust boundary.

- **`docs/merge_integration_design.md`** — Architecture for the Merge.dev Agent Handler integration that surfaces ~150 third-party SaaS connectors to Hermes through one MCP endpoint. Answers why the Merge API key cannot live in the customer container and how the broker reaches Merge through DOH-side endpoints authenticated with `DOH_ENV_BEARER`. Covers `origin_user_id = doh_{user.pk}_{app_slug}`, Magic Link flow with no callback URL, the streaming-HTTP MCP relay, and the per-connector card UI. Open when working on Merge integration, debugging Merge tools, or reasoning about which DOH-tenant secrets can/can't enter customer containers.

- **`docs/merge_connectors_auth_audit.md`** — Two-part audit of the 151 connectors in our Merge Tool Pack. **Part 1** (how Merge integrates each connector today) categorizes by what the user sees at connect time: OAuth via Merge with BYO override (split OAuth-only vs OAuth-or-token), ad-hoc creds (single API key / two-part pair / cloud IAM / username+password / vendor-specific), public APIs, undocumented. **Part 2** (independent of Merge) lists vendors that host an official remote MCP server with OAuth Dynamic Client Registration we could use directly, including the OIDC-only-scopes disqualifier (Vercel/Fireflies/Pylon). **Part 3** cross-cuts the two, calling out where Merge picks API-key for a DCR-capable vendor. Open when deciding whether to keep a connector behind Merge or move to a direct integration, or when reasoning about which credential the user will be asked to paste. Snapshot 2026-05-15.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX polling, chat-scoped SSE invalidation, and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.
