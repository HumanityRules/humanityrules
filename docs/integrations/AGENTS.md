# Integrations documentation

Hermes agent third-party integrations: OAuth broker, MCP aggregators, Merge connectors, gateway platforms (Slack, Telegram), and device-flow LLM providers. **When creating a new document in `docs/integrations/`, always add a reference to it here.**

- **`docs/integrations/integrations_broker_design.md`** — Architecture for the unified integrations broker: TLS-intercept, direct MCP, Merge, vault, OAuth, and device-flow connections. Carries the map of which broker module in `humr_runtime/integrations/` owns what, the provider catalog field-by-field, and the five credential methods. Open when reasoning about the HUMR/customer trust boundary, adding an integration, or debugging the broker.

- **`docs/integrations/mcp_aggregator_design.md`** — Architecture for direct MCP server integration in Hermes (PostHog is enabled today; Notion's implementation is currently disabled). Open when adding MCP connectors, working on the aggregator, or reasoning about the sandbox ↔ MCP trust boundary.

- **`docs/integrations/device_flow_integration_design.md`** — Broker-run OAuth device flows for LLM providers whose refresh tokens stay outside the sandbox, including Codex/ChatGPT-subscription and Nous Portal: TLS-intercept token injection, HUMR-side refresh-token storage, placeholder auth.json seeding, and model-picker visibility. Open when revisiting device-flow LLM provider integrations.

- **`docs/integrations/merge_integration_design.md`** — Architecture for the Merge.dev integration that surfaces ~150 SaaS connectors to Hermes through one MCP endpoint. Open when working on Merge, debugging Merge tools, or reasoning about which HUMR-tenant secrets can/can't enter customer containers.

- **`docs/integrations/dcr_candidates/`** — Per-vendor research on remote-MCP + DCR candidates. Start with `SUMMARY.md` for the tier ranking. Open when planning the next direct-MCP integration.

- **`docs/integrations/merge_connectors_auth_audit.md`** — Audit of the 151 Merge Tool Pack connectors: how Merge authenticates each one today, which vendors have an official remote MCP + DCR we could use directly, and where Merge defaults to API-key for a DCR-capable vendor. Open when deciding whether to keep a connector behind Merge or move it direct. Snapshot 2026-05-15.

- **`docs/integrations/merge_connectors_ranked.md`** — Popularity ranking of all 151 Merge Tool Pack connectors from external signals (MCP registries, Zapier/Make/Composio, GitHub, BuiltWith, G2). Open when deciding which connectors to disable by default. Regenerate via `docs/integrations/_build_merge_connector_rankings.py`.

- **`docs/integrations/merge-connector-taxonomy.md`** — End-of-flow taxonomy for the 71 Merge Link connectors as shown on the Hermes integrations page (OAuth redirect, API key, subdomain picker, etc.). Open when reasoning about Merge Link UX or connector onboarding patterns. Snapshot 2026-06-20.

- **`docs/integrations/slack_integration_design.md`** — How a Hermes agent connects to Slack via Socket Mode: personal vs company-wide modes, the prefill-URL manifest onboarding flow, why the customer must create the app, and the two-manifest privacy model. Open when working on the Slack integration, the integrations-panel config dialog, or reasoning about Slack token ownership.

- **`docs/integrations/gateway_env_and_restart_design.md`** — How vault-style credentials whose env presence activates a gateway platform binding (Telegram today) reach the in-sandbox Hermes gateway, and how the gateway is restarted in place without redeploying. Open when adding a gateway-activating provider or debugging gateway env propagation.

- **`docs/integrations/telegram_managed_bot_design.md`** — How Telegram connects without the user touching a token: HUMR's manager bot + `t.me/newbot` deep link, the generic `link_poll` vault mode (signed provider state + browser poll endpoint), and managed-token rotation on broker refresh. Open when working on the Telegram integration or adding another link-driven vault provider.
