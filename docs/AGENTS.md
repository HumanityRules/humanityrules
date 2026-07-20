# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.** Subfolder indexes cover focused areas — add new docs there first when they belong in that area.

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/integrations/AGENTS.md`** — Index for Hermes agent third-party integrations (OAuth broker, MCP, Merge, Slack, Telegram, device-flow providers). Open when working on the integrations sub-system.

- **`docs/permissions_broker_design.md`** — How the IAM task-role permissions editor moves from the HUMR UI into the Hermes WebUI as a self-referential editor (this deployment requests permissions for its own task role), the `/permissions/*` JSON contract over the renamed `humr_broker`, and the phase split (phase 1 = editor pane only; phase 2 = agent tools + skill + sidebar/chat layout). Open when working on the Hermes permissions editor, the broker rename, or the permissions API.

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/policy_proxy_design.md`** — Runtime design for the policy proxy that enforces SSO + ABAC in front of deployed apps. Covers the per-env auth Lambda (Okta OAuth, JWT minting, JWKS), the policy-proxy container (JWT verification, per-request PDP calls with caching), the HUMR PDP endpoint contract, and the Personal Assistant specifics (owner tag, global self-referential policy). Open when working on the policy proxy, the auth Lambda, or the PDP endpoint.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX self-terminating polling and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.

- **`docs/app_cost_tracking_design.md`** — Design for per-day, per-app cost tracking on the app-detail page, starting with Bedrock invocation cost and built as a generic, pluggable cost subsystem (`services/cost/`). Records the locked decisions plus the non-obvious facts from AWS docs and a real log record (cache-token fields, inference-profile-ARN modelId, geo ×1.1 premium, ARN→app forward mapping). Open before implementing cost tracking or adding a new cost source.

- **`docs/app_removal_data_cleanup_audit.md`** — Audit of what `AppRemovalJob`'s flags cover, and the gap: `IntegrationUserCredential` rows have no FK to `App` and are never cleaned up. Open when reworking app removal, debugging phantom reconnects after recreating an app, or auditing GDPR. Snapshot 2026-05-24.

- **`docs/webapps_design.md`** — How a Hermes agent serves user-built webapps at their own hostname (`<slug>-<agent-host>`) via a Caddy sidecar, including port topology, the `webapps` CLI, and `routes.caddy` regeneration. Open when working on the webapps mechanism, debugging cross-origin or routing issues, or adding a new framework.

- **`docs/tenant_isolation_rls_note.md`** — Personal note (Victor) about the layered defense plan for cross-tenant data leaks and the flag-gated Postgres RLS shape if it ever gets built. Not implemented. Open when revisiting tenant isolation strategy.

- **`docs/local_dev_login.md`** — How to log in locally without WorkOS: the `/auth/dev-login/` picker (DEBUG only) to log in as any existing user or create a new test user through the real onboarding flow, plus minting a session in the Django shell for `sessionid`-cookie curl. Open when you need to hit an authenticated endpoint without going through WorkOS.

- **`docs/platform_shared_credentials_design.md`** — Companion to `docs/shared_credentials_design.md`. Two extensions to credential sharing: a **platform tier** (HumR shares a credential with all customer orgs — new `PlatformSharedCredential`, ABAC-free, resolution ladder `org-shared > personal > platform`) and **OAuth/device sharing** (a control-plane login + central token-refresh service for Codex-style logins, used by both platform and org sharing). Records the locked decisions and the Phase 1 (Tavily vault key) / Phase 2 (OAuth/device) split. Open when working on platform credentials or OAuth/device sharing.
