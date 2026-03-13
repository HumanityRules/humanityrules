# Documentation

The main developer documentation lives in the docs/ subdirectory. **When creating a new document in `docs/`, always add a reference to it here.**

- **`docs/journal.md`** — Development journal with chronological entries. Consult this when trying to understand what, when, and why decisions were made. Contains reasoning behind architectural choices, implementation decisions, and lessons learned.

- **`docs/bolt_prompt.md`** — The original prompt used to generate the landing page design. Contains the product concept, target personas, and landing page section specifications.

- **`docs/deployment_agent_design.md`** — Design document for the AI-powered deployment agent. Defines implementation steps (reference apps, repo analysis, Dockerfile generation, CDK constructs, deployment plan, code generation, execution UI, agent states) and guiding design principles.

- **`docs/permissions_agent_design.md`** — Design document for the AI-powered permissions agent. Defines the agent's purposes (source code analysis, log analysis, transitive dependencies, blast radius assessment, draft review), required context, and open tasks (CloudTrail setup, CloudWatch access).

- **`docs/domain_model.md`** — Core domain concepts and entity relationships. Defines Workspace, App, Environment, Datastore, Deployment, etc entities with their attributes and how they relate. Read this when implementing features that touch the domain model or when clarifying entity boundaries.

- **`docs/abac_test_plan.md`** — Test plan for the ABAC authorization system. Covers the policy evaluation engine, view-level endpoint access, bootstrapping, cross-org isolation, realistic scenarios, and edge cases.

- **`docs/authorization_design_abac.md`** — Authorization system design (ABAC). Defines identity attributes, resource tags, policies, groups as attribute containers, and bootstrapping. Open when implementing or changing ABAC policies, attributes, tags, or org bootstrap.

- **`docs/okta_oidc_setup.md`** — Step-by-step guide for onboarding customers who use Okta for SSO (OIDC login instead of WorkOS). Open when setting up a new Okta/OIDC customer org or debugging OIDC login/redirect/issuer issues.

- **`docs/policy_enforcement_analysis.md`** — Analysis of Pulumi CrossGuard vs CDK/AWS options for deployment-time policy enforcement. Describes what DOH would need: policy packs (cost guardrails, internal-only, auto-cleanup, security basics), policy groups, and pipeline integration. Open when implementing or designing pre-deploy policy checks or environment-level guardrails.

- **`docs/project_report.md`** — High-level project overview for LLM agents: what DOH is, tech stack, domain model summary, control plane, feature list. Open when you need a quick architectural overview or onboarding context rather than deep design docs.

- **`docs/stack_concept_analysis.md`** — Analysis of the missing "stack" concept: per-(App, Environment) configuration. Explains why DOH has no per-environment config today and the consequences (no staging vs prod differentiation, sequential deploys overwriting App state, no promotion workflow). Open when designing or implementing per-environment app configuration or deployment snapshots.

- **`docs/ui_live_update_contract.md`** — The current live-update contract for HTMX polling, chat-scoped SSE invalidation, and widget fragment refresh patterns. Open when adding or changing live-refresh behavior.

