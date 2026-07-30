# Billing and Credits: MVP Design

**Status:** Decided design, pre-implementation

**Created:** 2026-07-28

**Purpose:** The MVP that lets an organization pay for HumR and lets HumR turn them off. Plans, credits, metering, enforcement, and payment — the minimum coherent slice, with deferred complexity named explicitly.

## 1. Scope

In scope: plan tiers on Organization, an append-only credit ledger, LLM metering at the integrations broker, soft enforcement at zero, Stripe subscription for Operator, and a billing page. Trial and Operator are the self-serve plans; Team and Enterprise are sales-led with no self-serve path.

Out of scope (see §10): on-demand overage, grant lots, reservations, reconciliation pipelines, per-seat mechanics, metering of non-LLM providers, durable event spooling, trial suspension automation.

While HumR runs on the shared Codex subscription, marginal inference cost is ~zero. Enforcement therefore protects customer expectations, not HumR's margin; every mechanism below is deliberately soft and fail-open. Hardening comes with the switch to per-token providers.

## 2. Plans and entitlements

### 2.1 Plan definitions live in code

`Organization.plan` is a choices field: `trial`, `operator`, `team`, `enterprise`. Plan definitions (price, grants, entitlements) are a config registry in code, not DB rows. With no customers there is nothing to grandfather; ledger entries stamp version strings so history stays explainable when DB-versioned plans eventually arrive.

Entitlement fields per plan:

1. **`price_usd_month`** — Stripe price; null for trial and enterprise.
2. **`monthly_credit_grant`** — credits granted per billing period; for trial, a one-time grant at signup.
3. **`always_on`** — whether the agent instance stays up permanently.
4. **`trial_runtime_days`** — runtime before suspension when `always_on` is false.
5. **`max_agents`** — how many agent apps the org may deploy.
6. **`customer_cloud`** — may deploy into their own AWS account (Team+).
7. **`bedrock_enabled`** — platform capability boolean (see §2.3).
8. **`on_demand_allowed`** — reserved, false everywhere; the zero-credit code path asks one place.

### 2.2 Per-org overrides

`Organization.plan_overrides` is a JSON dict, default `{}`, keys are plan-config field names. `effective_plan(org)` returns the plan config with overrides applied. Every gate in the app reads only `effective_plan`; nothing else looks at the raw plan or the overrides.

Rules:

1. Overrides replace fields wholly. No merge semantics per field type.
2. Keys are validated on save against the plan-config schema; values are type-checked. A typo'd key is a save error, not a silently dead exception.
3. Overrides adjust entitlements, not payment. `price_usd_month` is rejected as an override key — Stripe charges what the subscription says regardless.

Overrides are the admin escape hatch for every exception: comped credits, extended trials, extra agents, capability grants.

### 2.3 Platform capabilities fold into plans

`Organization.platform_capabilities` (JSON list of infra slugs) is replaced by per-capability booleans in plan config (`bedrock_enabled`), overridable per org via `plan_overrides`. Booleans win over the list because whole-field replacement makes single-capability overrides granular, field names are schema-validated, and the plan dataclass enumerates every capability that exists.

The deploy path still needs slugs: `effective_plan` derives `["bedrock-runtime", ...]` from the true booleans via a fixed mapping in code, feeding `AppConfig.platform_capabilities` → CDK task-role grants → `HUMR_PLATFORM_CAPABILITIES` unchanged.

Migration: drop the org field, move the existing demo grant to `plan_overrides = {"bedrock_enabled": true}`, repoint the deploy path and the model-picker gate at `effective_plan`.

### 2.4 Plan numbers

1. **Operator:** $39/month, 2,000 credits/month, always-on, 1 agent.
2. **Trial:** free, 500 credits one-time, 7 days runtime, 1 agent.
3. **Team / Enterprise:** sales-led; entitlements decided per deal via config + overrides.

All are config constants, cheap to change until customers exist. Price is the only value with lock-in (repricing subscribers is grandfathering, deferred).

## 3. The credit unit

1 credit = $0.01 of rated value. Markup lives in the rate card, never in the unit; credits are never displayed as USD. Internally credits are integers (Decimal with 0 fractional places at rest); sub-credit precision exists only inside rating before rounding. Never binary floating point.

## 4. Domain models

Four new models plus the plan field and overrides on Organization. All queries organization-scoped, org filter part of every lookup.

### 4.1 BillingSubscription

1:1 with Organization. Stripe customer id, subscription id, status, current period start/end. A pure Stripe mirror mutated only by webhook handlers. No Invoice or PaymentMethod models — Stripe's portal owns those.

### 4.2 BillingLedgerEntry

Append-only. No updates, no deletes; corrections are new entries.

1. **`organization`** — FK, indexed.
2. **`type`** — `grant` / `charge` / `adjustment` / `expiry`.
3. **`amount`** — signed Decimal credits. Positive grants, negative charges/expiry.
4. **`idempotency_key`** — unique. Per-source formats, e.g. `grant:{subscription_id}:{period_start}`, `charge:{org_id}:{date}`. Duplicate insert is a no-op; this is what makes retried webhooks, re-run jobs, and resent usage reports safe.
5. **`usage_event`** — nullable FK to BillingUsageEvent, set on charges.
6. **`description`** — human-readable line for the billing page.
7. **`metadata`** — JSON for type-specific facts: grants stamp `plan` + `plan_version`, charges stamp `rate_card_version` + usage breakdown.
8. **`created_at`** — nothing else; nothing updates.

Versions are stamped on the entries they influenced, at write time. No ledger-wide version columns.

### 4.3 BillingBalance

1:1 with Organization; transactional projection of the ledger. Exists to be the `SELECT FOR UPDATE` row so concurrent writes serialize. Invariant: equals `SUM(entries)` at all times; checkable by a management command. Every write path goes through the lock + idempotency key — no exceptions, or reconciliation happens by hand.

### 4.4 BillingUsageEvent

The metering fact, separate from money. Rating turns one event into charge amounts; the FK from ledger entries is the audit trail.

Source-agnostic, on the AppDailyCost pattern: the billable units live in a per-source `quantities` JSON, never as columns. No consumer aggregates quantities in SQL — rating reads events row by row, and every aggregate view (billing page, balance) reads the ledger — so a new source adds a validator entry, not a migration.

1. **`organization`**, **`app_id`** + **`app_slug`**, **`owner_username`** — attribution as reported by the broker. App attribution is a snapshot (plain id + slug columns, no FK): app removal and environment teardown delete App rows, and billing history must outlive them — including rating's `charge:{org_id}:{app_id}:{date}` key for events rated after the app is gone. Session-level attribution inside the agent is deliberately not captured.
2. **`source`** — `llm` for now; `tavily`, `x`, `bedrock` reserved.
3. **`subkey`** — source-specific sub-dimension; for `llm`, the provider model id as observed.
4. **`quantities`** — JSON dict of the source's billable units, schema-validated per source at ingest (exact key set, non-negative ints). For `llm`: `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` — disjoint buckets (input excludes cache reads; reasoning is informational, already inside output).
5. **`occurred_at`** — event time at the broker; rating selects the rate card active at this time.
6. **`idempotency_key`** — unique, minted by the broker at event creation.
7. **`rated_at`** — null until rating has charged it; unpriced events stay visibly unrated (§6.3).

## 5. Rate cards

### 5.1 In code, versioned by effective date

A list of frozen rate-card versions in `humanityrules_app/services/billing/rates.py`, each with `effective_from` and its rates. A price change is a new entry; existing entries are never edited. The active card is derived: latest version with `effective_from <= now`. Rating uses `rates_for(occurred_at)`, so late-arriving events are priced by the card in effect when the usage happened.

This covers time-versioning only. Per-org grandfathering (two orgs on different cards simultaneously) is deferred to DB rows.

### 5.2 Contents

One effective model row, anonymous to customers (the UI says "agent usage", never a model name). Keyed internally by observed model id so a second model slots in. Four buckets priced separately — blended per-token rates misprice cache-heavy agent workloads.

Anchor: **1.5x** the per-token price HumR would pay on OpenRouter for the equivalent model, rounded to friendly credit numbers. Indicative starting values (verify live provider prices at implementation time; they drift):

1. Input: ~190 credits per 1M tokens.
2. Output (reasoning folds in, matching OpenAI billing): ~1,500 credits per 1M tokens.
3. Cache read: ~19 credits per 1M tokens.
4. Cache write: 0 — OpenAI doesn't charge it.

Rates are product prices, not costs: they charge what HumR would charge on a per-token provider, so the eventual Codex → OpenRouter/Baseten switch is an internal swap, not a customer-visible repricing.

### 5.3 Rating rules

1. The rating function is the only code that turns usage into credits. Input: BillingUsageEvent. Output: credit amount + breakdown dict for entry metadata.
2. Unknown model or source: refuse and flag, never guess or charge zero. Same rule as the Bedrock cost subsystem.
3. Charge granularity: rate per event, but write one charge entry per org/app/day (idempotency key `charge:{org_id}:{app_id}:{date}`, amount accumulated transactionally). Keeps the ledger human-readable. The current day's entry accumulates; closed days are frozen forever.
4. The rating job is one global periodic task running every minute: pick up unrated events (partial index on `rated_at IS NULL`, `skip_locked` against overlapping runs), then per org in its own transaction — lock the balance, upsert day entries, mark events rated. Enforcement depends on this cadence: the broker's balance snapshot is only as fresh as rating.
5. `services/cost/` (Bedrock USD cost) stays separate. It answers "what did it cost us in USD"; rating answers "what do we charge in credits". Different numbers, different owners.

## 6. Metering at the integrations broker

### 6.1 Why the broker

The broker (`template_repos/hermes_agent/humr_runtime/integrations/tls_intercept.py`) terminates sandbox TLS for all metered providers and injects real credentials; the sandbox holds only placeholders. Codex traffic already routes through it (`chatgpt.com` in `tls_providers.TLS_INTERCEPT_PROVIDER_SPECS`, category `model_provider`, alongside OpenRouter, OpenAI API, Anthropic, Nous — and Tavily and X as connectors). The broker runs as root outside the nono sandbox, so metering requires zero cooperation from agent code and survives any agent-side change.

Environment reporting is trusted. Operator runs on HumR infra; Team/Enterprise (customer AWS) will be trusted too until a customer exists in those tiers.

### 6.2 The tap

1. Model-provider responses: a streaming parser on the response relay (`_relay_response_body` / `_relay_chunked_stream`) watches for the final usage payload in the SSE stream. Constraints: bounded memory, never delays or breaks the byte relay, observe-only. The relay is deliberately unbuffered (buffering caused a production memory incident); the tap must preserve that. Parse failure = log "unmetered" and relay untouched — a broken tap must never break the agent.
2. Compressed responses: for metered model providers the broker strips `Accept-Encoding` on the request so usage stays readable. LLM SSE is rarely compressed; this makes it deterministic.
3. Connector requests (future): request-level counting; request bodies are already fully buffered in the broker. Not implemented in MVP — Tavily rides free until it proves expensive, X is user-funded via per-user OAuth and charges no credits (customer-funded credentials consume no credits as the standing default).

### 6.3 Delivery

1. Events post directly to a CP endpoint — async, batched (every N events or T seconds), never blocking the relay. Transport and auth already exist: `humr_client.py` (`HUMR_ENV_BEARER` to `HUMR_CONTROL_PLANE_URL`), with the policy proxy's `activity_reporter.py` as the flush-loop precedent.
2. No spool. CP unreachable = drop the batch with a log line. The failure mode is undercharging, acceptable while inference cost is flat. Idempotency keys are minted at event creation anyway, so a spool can be added later without double-charge risk.
3. Attribution: the broker is a per-app sidecar (an environment hosts many agent apps, each with its own broker), and it already knows `owner_username` + `app_slug` — every event carries org, app, and owning user. The env bearer names only the environment, which is why the payload must carry the app identity.
4. The CP endpoint validates the env bearer, resolves the org, and inserts BillingUsageEvents (duplicate keys ignored). Rating runs as a periodic job over unrated events.

### 6.4 Sources outside the broker

1. **Bedrock** bypasses the broker (loopback SigV4 signer in `aws_signer.py`). It is enterprise-only and keeps its existing CloudWatch → `AppDailyCost` path; whether Bedrock usage charges credits at all is an Enterprise-deal question, deferred.
2. **Hermes state.db** (`session_model_usage` table, persistent volume) records every model call including auxiliary paths, with cost estimates. Not part of the pipeline; it is the free cross-check for validating tap numbers during the shadow period.

## 7. Enforcement

### 7.1 At the broker

1. The broker caches an entitlement snapshot — `{credits_remaining, monthly_grant, renewal_date, plan, exhausted}` — refreshed event-driven, no timers. Three triggers: every usage-event post returns the current snapshot in its response (active orgs stay fresh while spending); in the exhausted state the broker checks the CP synchronously on each incoming model call instead of trusting the cache (instant unblock on upgrade/renewal — the moment that matters most); and a `GET /__humr_broker/billing` with a cache older than a minute refreshes lazily so the WebUI card is honest on idle orgs. Failure semantics: no snapshot yet → fail open; exhausted and CP unreachable → keep refusing on last-known state.
2. At exhaustion the broker refuses `model_provider` requests with a structured 402 whose body says credits are exhausted and how to resolve (upgrade, or wait for renewal). Connector traffic is never blocked — user-funded or negligible, and blocking it breaks workflows for no economic reason.
3. Threshold: block at −10% of the monthly grant (−200 credits for Operator), not at zero. Delayed events and in-flight turns make exact-zero enforcement dishonest without reservation machinery; a fixed negative floor is simple and truthful. Negative balances roll into the next grant.
4. Scheduled and background agent work hits the same wall — no special case.

### 7.2 User experience

1. The agent surfaces the 402 error text naturally in conversation, so even with zero UI work the user learns why calls fail.
2. The broker exposes its cached snapshot same-origin to the WebUI: `GET /__humr_broker/billing` on the existing control API (`/__humr_broker/*` Caddy route, same pattern as the integrations cards). One poller per agent app (each app's WebUI polls its own broker); enforcement and display read the same state and cannot disagree.
3. The webui-extension renders a credits card in the bottom-left sidebar area (Lovable-style): a quiet meter normally, "running low — upgrade" below a warning threshold (~20% remaining), "out of credits — upgrade or renews on {date}" at exhaustion. Same component, three states. Upgrade links to the CP billing page.

## 8. Stripe and plan lifecycle

1. Stripe Checkout for Operator signup; Stripe customer portal for card management and cancellation. Monthly billing only.
2. Webhooks handled: subscription created, renewed (invoice paid), canceled, payment failed. Webhooks mutate **only** BillingSubscription.
3. A single transition function derives `Organization.plan` from subscription state: active → `operator`; canceled or payment failed past retries → `trial`. Plan is the entitlement source of truth and legitimately diverges from payment (friends-and-family orgs, enterprise invoiced outside Stripe, comps) — hand-set plan with no Stripe record is a supported state, which is why plan is not derived directly from the subscription.
4. Renewal grants: the invoice-paid webhook writes a `grant` entry (`grant:{subscription_id}:{period_start}`) and an `expiry` entry writing off the previous period's remainder. One grant alive at a time — pooled balance and grant coincide, which is what makes grant lots deferrable.
5. Payment failure: Stripe smart retries run their course; downgrade on final failure. No custom dunning.
6. Trial→Operator upgrade: checkout completes → transition function flips plan → monthly grant entry. The 500-credit trial grant is a one-time entry at org creation.

## 9. Trial lifecycle (decided, deferred)

Decided design, not implemented in MVP; trials are hand-created for now.

1. At `trial_runtime_days` after first agent deployment: suspend, not destroy — service scaled to zero, persistent root kept. Upgrading wakes a trained agent with memory and integrations intact.
2. Suspended UI state: "your agent is asleep — upgrade to wake it", not a broken page.
3. Retention: 30 days suspended, warning email, then real teardown.
4. Upgrade at any point resumes via the same plan-transition function.

## 10. Deferred complexity

Named so nothing sneaks up. The ledger and UsageEvent survive all of it; these are additions, not rewrites.

1. **Plan-change history** — a BillingPlanEvent audit model. First thing real customers make missed.
2. **DB-versioned plans and rate cards** — required the day anything is grandfathered.
3. **Grant lots** — multiple coexisting grants (promo, top-up) with per-lot expiry and draw order. Trigger: the first "here's 500 extra credits, valid two weeks".
4. **On-demand / overage** — opt-in, caps, prepaid top-ups or Stripe metered billing.
5. **Reservations** — authorize-execute-settle for exact zero-stops; needed when marginal cost is real.
6. **Reconciliation** — tap vs. provider invoices with a conflict policy; relevant after the per-token provider switch.
7. **Team mechanics** — seats, per-member visibility, multiple cost centers.
8. **Non-LLM metering** — per-provider billable-unit catalogs at the broker (X prices per endpoint, not per request).
9. **Spool + flush** — durable event delivery from the per-app brokers.
10. **Trial abuse controls** — eligibility rules, rate limits independent of balance.
11. **Fraud-resistant reporting** — required before untrusted customer-cloud tiers meter themselves.

## 11. Implementation order

1. CP models + plan config + `plan_overrides` + capability migration (§2, §4).
2. Rate cards + rating job (§5).
3. Stripe checkout, webhooks, transition function, grants (§8).
4. Broker tap + CP ingest endpoint (§6). Shadow period: meter and rate real usage without enforcement; validate against state.db and re-check grant/trial credit numbers against the observed burn distribution.
5. Enforcement + WebUI credits card (§7).
6. Billing page replacing the placeholder at `/settings/billing/` (plan, balance, usage, upgrade).

Model cleanup (Bedrock gating, provider anonymity in the UI) already shipped ahead of this design.
