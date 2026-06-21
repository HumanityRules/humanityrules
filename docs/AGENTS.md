# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.** Subfolder indexes cover focused areas — add new docs there first when they belong in that area.

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/app_deployment_blueprint_spec.md`** — Specification for the app deployment blueprint format and lifecycle. Open when working on blueprint generation, validation, or the deployment pipeline.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/integrations/AGENTS.md`** — Index for Hermes agent third-party integrations (OAuth broker, MCP, Merge, Slack, Telegram, device-flow providers). Open when working on the integrations sub-system.

- **`docs/permissions_broker_design.md`** — How the IAM task-role permissions editor moves from the HUMR UI into the Hermes WebUI as a self-referential editor (this deployment requests permissions for its own task role), the `/permissions/*` JSON contract over the renamed `humr_broker`, and the phase split (phase 1 = editor pane only; phase 2 = agent tools + skill + sidebar/chat layout). Open when working on the Hermes permissions editor, the broker rename, or the permissions API.

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Datastore, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/policy_proxy_design.md`** — Runtime design for the policy proxy that enforces SSO + ABAC in front of deployed apps. Covers the per-env auth Lambda (Okta OAuth, JWT minting, JWKS), the policy-proxy container (JWT verification, per-request PDP calls with caching), the HUMR PDP endpoint contract, and the Personal Assistant specifics (owner tag, global self-referential policy). Open when working on the policy proxy, the auth Lambda, or the PDP endpoint.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX polling, chat-scoped SSE invalidation, and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.

- **`docs/app_cost_tracking_design.md`** — Design for per-day, per-app cost tracking on the app-detail page, starting with Bedrock invocation cost and built as a generic, pluggable cost subsystem (`services/cost/`). Records the locked decisions plus the non-obvious facts from AWS docs and a real log record (cache-token fields, inference-profile-ARN modelId, geo ×1.1 premium, ARN→app forward mapping). Open before implementing cost tracking or adding a new cost source.

- **`docs/app_removal_data_cleanup_audit.md`** — Audit of what `AppRemovalJob`'s flags cover, and the gap: `IntegrationUserCredential` rows have no FK to `App` and are never cleaned up. Open when reworking app removal, debugging phantom reconnects after recreating an app, or auditing GDPR. Snapshot 2026-05-24.

- **`docs/webapps_design.md`** — How a Hermes agent serves user-built web apps at `/webapps/<slug>/` via a Caddy sidecar (port topology, `webapps` CLI, `routes.caddy` regeneration, `X-Forwarded-Prefix` framework configs). Open when working on the webapps mechanism, debugging cross-origin or routing issues, or adding a new framework.

- **`docs/tenant_isolation_rls_note.md`** — Personal note (Victor) about the layered defense plan for cross-tenant data leaks and the flag-gated Postgres RLS shape if it ever gets built. Not implemented. Open when revisiting tenant isolation strategy.

- **`docs/local_dev_login.md`** — How to log in locally as the first superuser via `/auth/dev-login/` (browser) or by minting a session in the Django shell and using it as a `sessionid` cookie (curl). Open when you need to hit an authenticated endpoint without going through WorkOS.
