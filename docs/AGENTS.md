# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.**

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/app_deployment_blueprint_spec.md`** — Specification for the app deployment blueprint format and lifecycle. Open when working on blueprint generation, validation, or the deployment pipeline.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/google_workspace_integration_design.md`** — Design for connecting Hermes Personal Assistants to Google Workspace (Gmail, Calendar, Drive). Covers the OAuth flow (DOH-owned GCP client, single registered redirect on devopshero.ai, org-scoped `/oidc/login/` for identity), per-env Secrets Manager token storage, and the `/oidc/login/` + `/oidc/callback/` patches needed to honor `next`. Open when working on third-party integrations for Hermes.

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Datastore, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/personal_assistant_deployment_state.md`** — Current-state report for the Personal Assistant Deployment feature (Hermes Agent template, multi-channel gateway, EFS persistence, Bedrock governance path, operator tooling). Open for context on that feature's scope and open surface.

- **`docs/policy_enforcement_analysis.md`** — Analysis of Pulumi CrossGuard vs CDK/AWS options for deployment-time policy enforcement. Describes what DOH would need: policy packs (cost guardrails, internal-only, auto-cleanup, security basics), policy groups, and pipeline integration. Open when implementing or designing pre-deploy policy checks or environment-level guardrails.

- **`docs/policy_proxy_design.md`** — Runtime design for the policy proxy that enforces SSO + ABAC in front of deployed apps. Covers the per-env auth Lambda (Okta OAuth, JWT minting, JWKS), the policy-proxy container (JWT verification, per-request PDP calls with caching), the DOH PDP endpoint contract, and the Personal Assistant specifics (owner tag, global self-referential policy). Open when working on the policy proxy, the auth Lambda, or the PDP endpoint.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX polling, chat-scoped SSE invalidation, and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.
