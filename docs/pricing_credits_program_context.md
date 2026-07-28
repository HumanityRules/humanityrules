# Pricing and Credits Program: Initial Context

**Status:** Initial context brief, not an approved design or implementation plan

**Created:** 2026-07-28

**Purpose:** Transfer the product, business, and technical context needed to begin the HumR pricing and credits program in a local repository checkout.

## 1. Working thesis from the program kickoff

Victor's current working model is:

1. A paid subscription includes a monthly allocation of HumR credits.
2. HumR measures model usage and potentially paid external API usage, including services such as X and Tavily.
3. Measured usage consumes credits.
4. When the included balance reaches zero, the customer can continue on demand or upgrade to the next plan.

This is the starting hypothesis for design. It does **not** yet decide:

1. The price of any plan.
2. The number or monetary value of credits included in a plan.
3. Whether credits correspond directly to USD cost, include a markup, or use a more abstract value scale.
4. Whether balances belong to a user, an organization, an app, or another account boundary.
5. Whether unused credits roll over or expire.
6. Whether on-demand usage requires prior opt-in, a spending limit, prepaid funds, or postpaid billing.
7. Which costs consume credits and which are included as unmetered product value.
8. Whether enforcement is immediate, eventually reconciled, or a hybrid.

## 2. Confirmed product and packaging context

Repository guidance describes HumR as pre-beta, pre-product-market fit, and without customers. Pricing and credits are therefore being designed before willingness to pay, expansion behavior, and plan economics have been validated. The initial implementation should preserve learning and reversibility.

### 2.1 Trial and paid-plan hierarchy

The canonical paid-plan names are:

1. `Operator`
2. `Team`
3. `Enterprise`

The trial is a separate evaluation call to action above the paid plans, not a fourth tier. The approved framing is:

> Try HumR on a real workflow. No credit card required. Includes demo credits.

The approved adoption progression is:

> Try a workflow → operate it continuously → share it with a team → govern it across the company.

### 2.2 Approved plan boundaries

Only a small number of package boundaries are currently decided:

1. **Trial:** no credit card; test a real workplace workflow; includes demo credits.
2. **Operator:** an individual operator's account and infrastructure are operated by HumR.
3. **Team:** includes deployment into the customer's AWS account.
4. **Enterprise:** occupies the company-governance end of the ladder, but its detailed package is unresolved.

The earlier phrase `personal plan` is historical language and should not be revived without founder review. The evaluation path should prove a real workplace use case and remain visibly connected to company adoption, rather than positioning HumR as a generic personal assistant.

### 2.3 Explicitly unresolved packaging questions

The prior pricing decision intentionally left these open:

1. All exact prices, including an earlier discussion of `$20`.
2. Demo-credit quantity, duration, eligibility, and conversion mechanics.
3. Operator limits, included resources, support, and infrastructure boundaries.
4. Team seats, SSO, governance features, support, and exact price.
5. Enterprise deployment, governance, support, compliance, procurement, and price.
6. Whether the proposed adoption ladder converts in practice.

There is no current evidence for willingness to pay, trial-to-paid conversion, preferred packaging, plan economics, or team-expansion behavior.

### 2.4 Messaging constraint

Matthew McClure warned that a low-priced individual offer could muddy HumR's company-oriented message. The current ladder addresses the naming and presentation problem, but the pricing program still needs to keep the relationship clear:

1. Trial proves a workplace workflow.
2. Operator keeps that workflow running.
3. Team turns it into company capability and changes the deployment boundary.
4. Enterprise governs broader organizational use.

Credits should support this progression rather than create a parallel consumer category.

### 2.5 Pricing research context

The mission wiki contains a Category Design book summary suggesting that pricing should make the value proposition unmistakable rather than defaulting to an undifferentiated middle. That material is a research heuristic, not a HumR decision. It recommends testing subscription, usage, outcome, service, and bundling models against high-need users and buyers.

## 3. Existing implementation foundations

HumR already has several pieces related to cost or usage. None is yet a complete subscription, credit-ledger, rating, or payment system.

### 3.1 Tenant and ownership model

The top-level tenant is `Organization`. Users join organizations through `OrganizationMembership`; organizations own workspaces, apps, environments, integrations, and other resources.

Relevant current facts:

1. `User.current_organization` identifies the organization in which the user is currently operating.
2. `Organization` has an `llm_preset` choosing Bedrock or Codex for personal assistants.
3. `App` belongs to an organization and workspace.
4. `Environment` belongs transitively to an organization through an AWS account.
5. `Team` introduces customer-owned AWS deployment, while Operator is HumR-operated.
6. All new billing and usage queries on tenant-owned records must include organization scope as part of the lookup.

These entities provide possible attribution dimensions, but they do not decide the commercial account that owns a subscription or credit balance.

### 3.2 Billing UI placeholder

HumR has an org-admin-only settings route at `/settings/billing/` and a placeholder template:

`humanityrules_app/templates/humanityrules_app/settings/billing.html`

It currently says only: “Manage your subscription and billing information.” There is no subscription management, payment-provider integration, invoice model, or credit display behind it.

Repository searches found no product subscription implementation and no Stripe integration. The only Stripe-specific repository material is unrelated integration-candidate research.

### 3.3 Control-plane `LLMUsageLog`

`humanityrules_app.models.LLMUsageLog` is an append-only record described as supporting cost tracking and billing. It records:

1. Organization, user, and control-plane conversation.
2. Source, currently `agent_turn` or `title_generation`.
3. Model alias and resolved model ID.
4. Input and output tokens.
5. Optional USD cost, duration, and number of turns.

Current writes occur in `humanityrules_app/services/agent/agent_service.py` for the control-plane deployment agent and conversation-title generation.

Important boundary: this is not a complete record of activity inside deployed Hermes personal assistants. Title-generation rows currently have token counts but no USD cost. The model also lacks cache token buckets, external API usage, idempotency keys, rating-version references, and ledger semantics. It is useful prior art or an input source, not the credit system itself.

### 3.4 Per-app Bedrock cost subsystem

`humanityrules_app/services/cost/` is implemented and stores daily rows in `AppDailyCost`.

Its current behavior is:

1. Bedrock invocation logs are aggregated by app, environment, UTC day, and normalized model ID.
2. It records four separately priced token buckets: input, output, cache write, and cache read.
3. It calculates USD cost using model and geographic pricing.
4. It does not log prompt or response content.
5. It recomputes recent non-final days to absorb log-delivery lag, then freezes older days.
6. It provides a separate rolling 24-hour total.
7. It is source-agnostic in storage and orchestration, although Bedrock is the only active `CostSource` today.

Useful seams:

1. `AppDailyCost` already has organization, app, environment, source, date, subkey, USD cost, source-specific details, and finality.
2. `CostSource` makes additional after-the-fact cost collectors pluggable.
3. The Bedrock collector can provide durable cost and token evidence for reconciliation.

Limits for credits and enforcement:

1. Data is daily and aggregated, not an immutable event ledger.
2. Attribution is app-level, not necessarily user-level.
3. Bedrock log delivery is delayed, so it cannot by itself guarantee a synchronous stop at zero credits.
4. Past days eventually become unrecoverable from CloudWatch and depend on the durable aggregate.
5. Unknown pricing is deliberately marked unpriced rather than guessed.
6. `AppDailyCost` tracks underlying cost, not customer-facing credit charges, discounts, grants, reversals, or balances.
7. A cost-source failure is logged and skipped; a failed rolling 24-hour source can appear as zero. That resilience is appropriate for an operational panel but cannot be inherited uncritically by billing.
8. The implementation design records live end-to-end verification against a real account as still outstanding.

### 3.5 Hermes WebUI live usage telemetry

The vendored Hermes WebUI emits a best-effort live usage payload while an agent stream is active. It includes session input and output tokens, cache-read and cache-write tokens, estimated USD cost, context-window values, and cache-hit percentage.

The stream comments define an important accuracy boundary: values are exact for the most recent completed model call, while prompt usage after a tool result is appended is only a bounded lower estimate until the next model call reports exact usage. The data is emitted repeatedly for the UI during streaming.

This can support near-real-time balance display, warnings, estimates, or reservations. It is not currently a durable, tenant-scoped financial event stream, and its best-effort estimates should not be the sole source of settled charges.

### 3.6 Hermes WebUI provider cost budget

The vendored Hermes WebUI has an OpenRouter-specific provider cost-history and monthly-budget feature. It:

1. Reads cumulative usage and limits from the OpenRouter key endpoint.
2. Stores local daily snapshots in the active Hermes home.
3. Calculates deltas between snapshots.
4. Lets a user configure a monthly USD budget for display.

This is useful UX and failure-handling prior art, but it is not a HumR commercial control:

1. It currently supports OpenRouter only.
2. Its budget is local WebUI state.
3. It displays percentage used but does not provide a multi-tenant ledger or plan entitlement.
4. It is not authoritative for billing and does not enforce HumR credits.

### 3.7 Platform, organization, and personal credentials

For supported providers, credential resolution follows:

1. Organization-shared credential.
2. Personal credential.
3. Platform-shared credential.

The platform credential is the lowest-priority default. A customer's organization-shared or personal credential overrides it automatically.

This matters commercially because a provider call may use:

1. A HumR-funded platform credential.
2. A customer-funded organization credential.
3. A user-funded personal credential.

The pricing program must decide whether these paths consume the same credits. At minimum, usage events need to preserve credential funding source so rating can distinguish them.

Additional boundaries matter for packaging and risk:

1. Platform credentials currently apply to all organizations; plan- or organization-targeted audiences are reserved but not implemented.
2. Organization opt-out or disabling of an available platform connector is deferred.
3. Shared credential secrets are currently stored as JSON in Postgres, with migration to a dedicated secret store deferred. Platform credentials therefore create a pooled-cost and larger security and abuse-risk surface.

### 3.8 Tavily

Tavily is implemented as a vault provider and can use a platform-wide key. Relevant facts:

1. The API key stays in HumR's credential store.
2. The broker injects it into calls to `api.tavily.com`.
3. The key can also be personal or organization-shared.
4. Key validation calls Tavily's `/usage` endpoint without consuming a search credit.
5. The current code validates and distributes credentials but does not attribute Tavily usage to a HumR account or deduct HumR credits.

A shared Tavily key makes upstream aggregate usage insufficient for customer attribution. Accurate per-account charging will likely require events at the HumR broker or tool boundary, reconciliation against provider usage, or both.

### 3.9 X

X is implemented as a per-user OAuth integration. The HumR control plane stores the refresh token, and the environment broker injects short-lived access tokens into calls to `api.x.com`.

Current code handles connection, refresh, rotation, and revocation. It does not meter endpoint usage or calculate X API cost.

X pricing may depend on endpoint, operation, plan, or provider-account terms. The pricing program must not assume that one HTTP request equals one fixed charge. It needs a versioned source of truth for billable units and a clear answer to who bears the upstream X cost.

### 3.10 Integrations broker as a possible metering seam

Known provider traffic from a sandbox passes through the environment's HumR integrations broker. The broker terminates the sandbox-side TLS connection, replaces placeholder authorization, and forwards the request to the provider.

This creates a potential point for metering API calls because the broker can observe known provider requests and responses. No billing event emission currently exists there. A design must account for:

1. Reliable event delivery from a customer environment to the control plane.
2. Attribution to organization, user, app, environment, provider, and credential source.
3. Idempotency across retries and broker restarts.
4. Privacy, with no unnecessary request or response content stored.
5. Endpoint-specific or response-derived billable units.
6. Behavior when the control plane is unavailable.
7. Whether pre-call authorization is required when credits are nearly exhausted.

### 3.11 Existing verification assets

The repository already contains tests that can anchor the next program:

1. `humanityrules_app/tests/test_cost_subsystem.py` covers Bedrock pricing, token buckets, estimates, refresh windows, freezing, idempotent aggregate updates, rolling 24-hour behavior, job coordination, and permissions.
2. `humanityrules_app/tests/test_shared_credentials.py`, `test_platform_shared_credentials.py`, `test_integrations_tokens_batch.py`, and `test_org_shared_keys.py` cover credential authorization and resolution precedence.
3. `humanityrules_app/tests/test_integrations_x_oauth_callback.py` covers X connection behavior, but no existing test establishes X billable usage or credit deduction.
4. Hermes WebUI tests cover live usage estimates and OpenRouter cost history and budget display; they do not establish commercial entitlements or enforcement.

No reviewed test establishes a subscription, monthly credit grant, ledger debit, balance enforcement, payment webhook, or on-demand charge.

## 4. Recommended conceptual separation

The design should keep five concerns separate even if the first implementation is compact.

### 4.1 Metering

Produce normalized, immutable usage facts, for example model token buckets or one Tavily search operation. Metering answers **what happened**.

### 4.2 Rating

Convert a usage fact into customer-facing credits according to a versioned rate card. Rating answers **how many credits this event costs**.

The rate card may differ from raw vendor USD cost. That choice is still open.

### 4.3 Ledger and balance

Record grants, charges, purchases, expirations, refunds, corrections, and administrative adjustments as append-only entries. The balance should be derived from ledger entries or maintained as a transactional projection, not treated as a mutable number without history.

### 4.4 Entitlements and enforcement

Determine what a subscription permits, whether a call can start, what happens at zero, and whether on-demand usage is enabled. Entitlements answer **may this account continue**.

### 4.5 Payment and invoicing

Manage subscription lifecycle, payment methods, on-demand charges, invoices, taxes, failed payments, and provider webhooks. Payment state should not be conflated with usage facts or ledger history.

This separation is a recommendation for design clarity, not an approved architecture.

## 5. Candidate requirements for the first design

### 5.1 Accounting integrity

1. Every charge must be attributable to exactly one commercial account and organization.
2. Retries, duplicate webhooks, repeated provider reports, and reconciliation jobs must not double-charge.
3. Rates and plan terms must be versioned so historical charges remain explainable after pricing changes.
4. Corrections should use reversal or adjustment entries rather than rewriting settled history.
5. Credit arithmetic should use fixed precision, never binary floating point.
6. Customer-visible balances must reconcile to underlying usage and ledger entries.
7. Provider-reported usage and HumR-observed usage need a documented conflict policy.

### 5.2 Multi-tenant safety

1. Every query on subscription, ledger, balance, usage, or entitlement records must be organization-scoped.
2. Client-supplied IDs must be resolved with the organization filter in the same lookup.
3. Cross-org platform-credential usage must retain tenant attribution without exposing one customer's activity to another.
4. Team and Enterprise administration must distinguish who can view usage, change limits, purchase credits, or enable on-demand billing.

### 5.3 Product behavior

1. Show included credits, consumed credits, remaining credits, renewal date, and whether on-demand is active.
2. Warn before exhaustion using thresholds that do not imply false real-time precision for delayed sources.
3. Explain major usage categories without exposing private prompts, responses, or unnecessary API payloads.
4. Define the exact zero-credit experience for foreground turns, scheduled jobs, background agents, and API calls.
5. Avoid silently creating billable on-demand usage without explicit customer understanding and authorization.
6. Preserve a useful upgrade path from Trial to Operator to Team to Enterprise.

### 5.4 Operations and support

1. Provide an auditable admin view for usage, rating decisions, grants, charges, adjustments, and provider reconciliation.
2. Support rate-card changes without redeploying every customer environment where practical.
3. Surface unpriced or unattributed usage rather than silently treating it as free or charging an estimate.
4. Monitor delayed ingestion, event loss, negative balances, reconciliation drift, and failed payment state transitions.
5. Define data retention for detailed usage events versus aggregated financial records.

## 6. Central design decisions still required

The first local design session should resolve or explicitly defer these questions.

### 6.1 Commercial account and balance owner

1. Is Trial and Operator balance owned by a user, an organization with one member, or a subscription account?
2. Does Team use one pooled organization balance, per-seat allocations, or both?
3. Can one organization have multiple subscriptions or cost centers?
4. How are apps and environments attributed when several users share them?

### 6.2 Meaning of a credit

1. Is one credit fixed to a USD amount?
2. Does the customer receive the same number of credits per dollar across plans?
3. Are margins encoded in the credit conversion, subscription price, or both?
4. Can different usage categories have different markups or minimum charges?
5. How are free internal operations, promotional grants, and demo credits represented?

### 6.3 Monthly grants

1. When are subscription credits granted?
2. Do they expire, roll over, or have caps?
3. What happens on upgrade, downgrade, cancellation, refund, failed payment, or mid-cycle proration?
4. Are purchased on-demand credits distinct from expiring subscription credits?
5. Which balance is consumed first when several grants exist?

### 6.4 Included versus metered value

Possible charge categories include:

1. Model inference.
2. X, Tavily, Browser Use, and other paid APIs.
3. Agent runtime or compute.
4. Storage, data transfer, deployed Web Apps, scheduled execution, or other infrastructure.
5. Seats, governance, customer-cloud operation, support, and service.

The system should not assume every internal cost becomes a customer credit charge. Packaging may intentionally include some resources, meter others, or use fair-use boundaries.

### 6.5 Customer-provided credentials

For customer-funded API keys or OAuth accounts:

1. Should HumR credits be untouched because HumR does not bear the upstream variable cost?
2. Should HumR charge a smaller orchestration or platform fee?
3. Does behavior vary by plan?
4. How should the UI explain which credential source funded an operation?

### 6.6 Enforcement timing

1. Which sources can be authorized before execution?
2. Which can only be measured after execution?
3. How much grace or negative balance is allowed for delayed Bedrock logs and in-flight work?
4. Do scheduled or background jobs pause at zero, finish the current unit, or continue under on-demand rules?
5. How does the system behave if the control plane or ledger is unavailable?

### 6.7 Exhaustion and on-demand

1. Is on-demand opt-in or opt-out?
2. Does it have a hard monthly spending cap?
3. Can org admins disable it for members or workspaces?
4. What happens to active agent turns and external calls at the boundary?
5. Which upgrade should be offered when credits run out, and is upgrade based on plan capabilities, usage economics, or both?

### 6.8 Trial abuse and risk

1. How is trial eligibility determined?
2. Are demo credits tied to a verified person, organization, payment method, or domain?
3. What rate limits and fraud controls exist independently of the credit balance?
4. Can expensive models or APIs be excluded from Trial?
5. How are promotional grants distinguished from recurring subscription grants?

## 7. Suggested workstreams

These are candidate workstreams, not an approved implementation sequence.

### 7.1 Product and economics

1. Define target gross-margin guardrails and cost exposure.
2. Model Trial, Operator, Team, and Enterprise packages.
3. Define the credit unit and candidate rate cards.
4. Test comprehension and willingness to pay with prospective operators and buyers.
5. Decide customer-provided-credential treatment.

### 7.2 Usage inventory and metering coverage

1. Inventory every variable-cost source and who currently pays it.
2. Classify each source as synchronous, delayed, provider-reported, or estimated.
3. Identify attribution dimensions and data gaps.
4. Define normalized usage-event schemas and idempotency identifiers.
5. Define reconciliation against Bedrock, Tavily, X, and future providers.

### 7.3 Ledger and entitlements

1. Define commercial account, subscription, grant, ledger-entry, balance, and rate-card models.
2. Define transactional charge and reservation behavior.
3. Define month renewal, expiry, rollover, upgrade, downgrade, and adjustment rules.
4. Define zero-credit and on-demand state transitions.
5. Define tenant-scoped read and administrative permissions.

### 7.4 Payment lifecycle

1. Select the payment and subscription provider.
2. Map provider products and prices to versioned HumR plans.
3. Design webhook idempotency and lifecycle handling.
4. Define invoices, receipts, tax, refunds, disputes, and failed payments.
5. Keep payment-provider state synchronized without making it the only financial audit record.

### 7.5 Customer and operator experience

1. Replace the billing placeholder with plan, balance, renewal, usage, and payment views.
2. Add low-balance warnings and clear exhaustion choices.
3. Provide usage breakdowns at the right level for Operator and Team.
4. Give org admins controls for on-demand, caps, members, and potentially workspaces.
5. Make delayed or estimated usage visible without overwhelming the customer.

### 7.6 Migration and verification

1. Decide whether existing `LLMUsageLog` or `AppDailyCost` records seed any opening usage history.
2. Add invariants and concurrency tests before enabling charges.
3. Test duplicate events, late events, reversals, plan changes, failed payments, and cross-tenant access.
4. Shadow-rate usage before customer enforcement.
5. Reconcile shadow results against provider invoices before launch.

## 8. Early architectural risks

1. **Treating cost aggregation as a ledger.** `AppDailyCost` is mutable during its lag window and lacks financial history semantics.
2. **Treating one observed request as one billable unit.** X and future providers may price by endpoint, response, data volume, or plan.
3. **Charging shared-key usage without attribution.** Provider key totals alone cannot reliably divide usage across customers.
4. **Synchronous hard stops with delayed usage.** Bedrock logs arrive after execution, so exact zero-balance enforcement requires reservations, estimates, grace, or another real-time signal.
5. **Conflating provider credits, USD, and HumR credits.** These are separate units and should have explicit names and conversions.
6. **Mutable rate logic without history.** A changed rate must not alter the explanation of a historical charge.
7. **Double counting.** `LLMUsageLog`, Bedrock logs, provider reports, and broker observations may describe overlapping activity.
8. **Ignoring credential funding source.** Platform, organization, and personal credentials can have different economics.
9. **Using payment-provider objects as the internal ledger.** Webhooks can be delayed or duplicated, and provider objects do not represent every HumR grant or adjustment.
10. **Leaking sensitive activity in billing detail.** Usage explanations should avoid prompts, responses, email contents, and raw third-party payloads.
11. **Letting a credit balance become an authorization oracle across tenants.** Every lookup and mutation must preserve organization scope.
12. **Embedding volatile provider prices in durable documentation.** Rate sources must be refreshed and versioned at implementation time.

## 9. Non-goals of this document

This brief does not:

1. Approve plan prices or credit quantities.
2. Select Stripe or any other payment provider.
3. Define the final data model or API contracts.
4. Decide whether credits map directly to cost.
5. Decide rollover, expiration, proration, reservations, or overage rules.
6. Promise that every existing cost record can be used for billing.
7. Replace a future product requirements document, economics model, architecture design, threat model, or implementation plan.

## 10. Source map

### 10.1 Mission wiki

1. `/workspace/wikis/humanity-rules/operations/decisions/operator-team-enterprise-pricing-ladder.md`
2. `/workspace/wikis/humanity-rules/operations/decisions/personal-plan-as-company-use-case-trial.md`
3. `/workspace/wikis/humanity-rules/operations/suggestions/prosumer-vs-enterprise-messaging-clarity.md`
4. `/workspace/wikis/humanity-rules/concepts/category-design/law-15-your-pricing-should-be-free-or-ultra-expensive-avoid-the-middle.md`

The wiki is authoritative for founder-approved product context and historical provenance. The repository is authoritative for implementation facts.

### 10.2 Prior conversations

1. **2026-07-23 to 2026-07-24, Matthew McClure reflection:** surfaced the demo-credit-to-paid path, an earlier `$20/month` idea, the need to protect enterprise message clarity, and trust around deployment ownership.
2. **2026-07-24, tier naming discussion:** proposed Trial, Operator, Team, and Enterprise; Victor approved the separate no-card trial presentation plus the three paid-plan names and the Team customer-AWS boundary.
3. **2026-07-28, pricing and credits kickoff:** introduced the current subscription-includes-credits thesis, metering of model and paid API usage, credit deduction, and the on-demand-or-upgrade behavior at zero.

The wiki pages above preserve the durable conclusions from the earlier sessions. Exact prices and detailed packaging remain unresolved.

### 10.3 Repository documentation and code

1. `docs/app_cost_tracking_design.md`
2. `docs/platform_shared_credentials_design.md`
3. `docs/integrations/integrations_broker_design.md`
4. `docs/domain_model.md`
5. `humanityrules_app/models.py`
6. `humanityrules_app/services/agent/agent_service.py`
7. `humanityrules_app/services/cost/`
8. `humanityrules_app/views/integrations/provider_tavily.py`
9. `humanityrules_app/views/integrations/provider_x.py`
10. `humanityrules_app/templates/humanityrules_app/settings/billing.html`
11. `template_repos/hermes_agent/vendor/hermes-webui/api/providers.py`
12. `template_repos/hermes_agent/vendor/hermes-webui/api/streaming.py`
13. `template_repos/hermes_agent/vendor/hermes-webui/api/usage.py`
14. `template_repos/hermes_agent/vendor/hermes-webui/tests/test_streaming_live_usage_estimate.py`
15. `template_repos/hermes_agent/vendor/hermes-webui/tests/test_provider_cost_history.py`
16. `template_repos/hermes_agent/vendor/hermes-webui/tests/test_provider_cost_budget.py`

## 11. Recommended next artifact

After review of this context brief, the next useful artifact should be a focused decision document or PRD that resolves the commercial account boundary, meaning of a credit, included-versus-metered categories, and exhaustion behavior before locking a technical schema.
