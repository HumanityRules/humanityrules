# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.**

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/app_deployment_blueprint_spec.md`** — Specification for the app deployment blueprint format and lifecycle. Open when working on blueprint generation, validation, or the deployment pipeline.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/integrations_broker_design.md`** — Architecture for third-party OAuth integrations in Hermes (Google today; Slack/Notion/Linear next). Open when reasoning about the DOH/customer trust boundary, adding a provider, or debugging the broker.

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Datastore, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/policy_proxy_design.md`** — Runtime design for the policy proxy that enforces SSO + ABAC in front of deployed apps. Covers the per-env auth Lambda (Okta OAuth, JWT minting, JWKS), the policy-proxy container (JWT verification, per-request PDP calls with caching), the DOH PDP endpoint contract, and the Personal Assistant specifics (owner tag, global self-referential policy). Open when working on the policy proxy, the auth Lambda, or the PDP endpoint.

- **`docs/mcp_aggregator_design.md`** — Architecture for MCP server integration in Hermes (Notion today). Open when adding MCP providers, working on the aggregator (in `integrations_broker.py`), or reasoning about the sandbox ↔ MCP trust boundary.

- **`docs/device_flow_integration_design.md`** — Broker-run OAuth device flows for LLM providers whose refresh tokens stay outside the sandbox, including Codex/ChatGPT-subscription and Nous Portal: TLS-intercept token injection, DOH-side refresh-token storage, placeholder auth.json seeding, and model-picker visibility. Open when revisiting device-flow LLM provider integrations.

- **`docs/merge_integration_design.md`** — Architecture for the Merge.dev integration that surfaces ~150 SaaS connectors to Hermes through one MCP endpoint. Open when working on Merge, debugging Merge tools, or reasoning about which DOH-tenant secrets can/can't enter customer containers.

- **`docs/dcr_candidates/`** — Per-vendor research on remote-MCP + DCR candidates. Start with `SUMMARY.md` for the tier ranking. Open when planning the next direct-MCP integration.

- **`docs/merge_connectors_auth_audit.md`** — Audit of the 151 Merge Tool Pack connectors: how Merge authenticates each one today, which vendors have an official remote MCP + DCR we could use directly, and where Merge defaults to API-key for a DCR-capable vendor. Open when deciding whether to keep a connector behind Merge or move it direct. Snapshot 2026-05-15.

- **`docs/merge_connectors_ranked.md`** — Popularity ranking of all 151 Merge Tool Pack connectors from external signals (MCP registries, Zapier/Make/Composio, GitHub, BuiltWith, G2). Open when deciding which connectors to disable by default. Regenerate via `docs/_build_merge_connector_rankings.py`.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX polling, chat-scoped SSE invalidation, and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.

- **`docs/app_removal_data_cleanup_audit.md`** — Audit of what `AppRemovalJob`'s flags cover, and the gap: `IntegrationUserCredential` rows have no FK to `App` and are never cleaned up. Open when reworking app removal, debugging phantom reconnects after recreating an app, or auditing GDPR. Snapshot 2026-05-24.

- **`docs/slack_integration_design.md`** — How a Hermes agent connects to Slack via Socket Mode: personal vs company-wide modes, the prefill-URL manifest onboarding flow, why the customer must create the app, and the two-manifest privacy model. Open when working on the Slack integration, the integrations-panel config dialog, or reasoning about Slack token ownership.

- **`docs/gateway_env_and_restart_design.md`** — How vault-style credentials whose env presence activates a gateway platform binding (Telegram today) reach the in-sandbox Hermes gateway, and how the gateway is restarted in place without redeploying. Open when adding a gateway-activating provider or debugging gateway env propagation.

- **`docs/telegram_managed_bot_design.md`** — How Telegram connects without the user touching a token: DOH's manager bot + `t.me/newbot` deep link, the generic `link_poll` vault mode (signed provider state + browser poll endpoint), and managed-token rotation on broker refresh. Open when working on the Telegram integration or adding another link-driven vault provider.

- **`docs/webapps_design.md`** — How a Hermes agent serves user-built web apps at `/webapps/<slug>/` via a Caddy sidecar (port topology, `webapps` CLI, `routes.caddy` regeneration, `X-Forwarded-Prefix` framework configs). Open when working on the webapps mechanism, debugging cross-origin or routing issues, or adding a new framework.

- **`docs/tenant_isolation_rls_note.md`** — Personal note (Victor) about the layered defense plan for cross-tenant data leaks and the flag-gated Postgres RLS shape if it ever gets built. Not implemented. Open when revisiting tenant isolation strategy.

- **`docs/local_dev_login.md`** — How to log in locally as the first superuser via `/auth/dev-login/` (browser) or by minting a session in the Django shell and using it as a `sessionid` cookie (curl). Open when you need to hit an authenticated endpoint without going through WorkOS.
