# Handoff: Stripe mental-model simplification session

Working session handoff, 2026-08-06. Delete this file when the work is done.

## Where this stands

The `simplify-mental-model` skill (`.claude/skills/simplify-mental-model/SKILL.md`) governs this work. Stage 1 and Stage 2 are DONE (2026-08-06): Victor agreed to P1–P4, P7, P8 and deferred P5, P6. Codex implemented everything through the `codex-implement` skill; two fresh-context review rounds each found one real gap (round 1: malformed subscription events were retained-then-dropped, now they raise so retention rolls back and Stripe retries; round 2: the same fate for missing `data.object`, extended the raise) and both were fixed, none declined. Full billing suite green at 271 tests across all nine billing modules. Sonnet prose pass applied. **Everything is uncommitted on main.** We are at Stage 3 step 1: Victor reviews first; nothing is committed without his word.

## What is being simplified

The Stripe/billing lifecycle, believed correct and heavily hardened over 2026-08-05/06 (commits 392bb66c, e54bd297, 575b1a63, 56842731, 72a676a5 — read their messages for the design history). Architecture: webhook handlers record facts on the `BillingSubscription` mirror; one reconciler derives plan and grants. Source of truth: `docs/billing_design.md` §8 items 1–13. Core files: `humanityrules_app/services/billing/stripe_lifecycle.py`, `grants.py`, `entitlements.py`, `billing_page.py`, `humanityrules_app/views/stripe_webhook.py`, models `BillingSubscription`/`StripeWebhookEvent`/`BillingLedgerEntry`/`BillingBalance`, command `humr_billing_verify`.

## The analysis (fresh-context subagent, complete)

Verdict: the architecture is right; the reducible complexity is that the clean story lives in docstrings while the code tells it through inline guard walls, duplicated rituals, and caller-identity flags. ~22 retained concepts to follow webhook → credits; most essential. No guard is deletable — each defends a distinct delivery scenario.

Proposals ranked by leverage:

1. **P1** — merge `restart_trial_credits` + `grant_operator_period_credits` into one named money operation ("reset balance to grant", the design doc's own concept); keep both names as key-minting wrappers. Pure factoring of the most safety-critical duplicate.
2. **P2** — extract `_apply_subscription_event`'s four inline guard walls into predicates named by scenario (e.g. `_would_resurrect_canceled_subscription`); handler reads as the module docstring's story. Pure extraction.
3. **P3** — split `_upsert_subscription_mirror` into two writers with one null-semantics contract each (subscription events: verbatim full state; invoice facts: upsert-if-present, statusless create). Kills `allow_statusless_create` and the `status=None` sentinel. **Only proposal that changes semantics** (absent fields on subscription events would null rather than keep); needs its own discussion AND new tests — existing helpers always build complete payloads, so the missing-field edge is thinly covered.
4. **P4** — `transition_organization_plan` returns the transition (previous, resulting) instead of the caller diffing `organization.plan` around the mutating call.
5. **P5** — add `keeps_operator` and `renewal_date` properties to `BillingSubscription` next to `is_pending_cancellation`; entitlements + billing_page stop re-deriving them separately.
6. **P6** — mint all idempotency-key formats in one place; `humr_billing_verify` currently re-spells them by convention.
7. **P7** — renames: `_apply_failed_invoice` (applies nothing), explicit `elif` in the dispatcher, collapse `EntitlementSnapshot`/`snapshot_payload` double spelling.
8. **P8** — doc-only: restate §8's five guard rules as two axioms ("the mirror follows exactly one subscription until it reaches terminal state"; "among the followed subscription's events, newest wins and canceled wins ties") with the guards as corollaries.

Explicitly do NOT touch: facts-then-reconcile split, event retention as atomicity unit, invoice.paid as sole payment proof, per-event trial-reset keys, the dumb webhook view, `plans.effective_plan` as the single gate, the long module docstrings.

## Test safety net

94% line coverage over the billing services under 224 behavior-level tests (raw event dicts in → mirror/ledger/plan/balance asserted; no internal mocking) — refactors that change outcomes will fail tests. Uncovered lines are malformed-payload early-returns and SDK glue. Backstops: `uv run python manage.py humr_billing_verify` (read-only invariants; run manually, ≥ monthly cadence documented) and the live E2E rig below. P3 is the exception: it needs new tests for its new contract.

## Process conventions (Victor's standing rules — follow exactly)

1. Discuss before changing, one item at a time; when explaining, offer piece-by-piece. Brief replies; numbered lists; no markdown tables.
2. Implementation goes through the `codex-implement` skill: Codex (`codex exec --full-auto -m gpt-5.6-sol -c model_reasoning_effort=xhigh`, brief states what/why never how, run in background with stdout to a scratchpad log) implements including tests; NEVER review or run the tests yourself.
3. Review by a FRESH-CONTEXT subagent (not the main session) — Victor's explicit instruction; findings go back to Codex as suggestions; Codex justifies declines; disagreements are Victor's to arbitrate. Every review round this week found real money bugs — do not skip it.
4. After review: Sonnet 5 subagent prose pass on new/changed comments and docstrings (verbatim objective in the skill). Comments = current mental model only, no history.
5. Nothing is committed without Victor's explicit word. Work on main directly. No backward compatibility, ever.

## Live E2E rig (localhost)

Dev server on :8000 (ngrok `humanityrules.ngrok.io` → webhook endpoint `we_1U1WKFAcRixsZxlcUE1ez5SR`, test mode, api_version 2026-06-24.dahlia). `.env`: `STRIPE_WEBHOOK_SECRET_NGROK` signs local deliveries and wins over `STRIPE_WEBHOOK_SECRET` (which must stay = the prod endpoint's secret — `sync_secrets.py` ships `.env` verbatim to prod). Browser login: `http://127.0.0.1:8000/auth/dev-login/?email=vmendi@gmail.com&next=/settings/billing/` in Victor's Chrome (Claude in Chrome). Test org `org-local` is currently Trial, 500 credits, canceled mirror. Checkout uses Link (saved test Visa 4242; test-mode code 000000). Renewal/dunning simulation: Stripe test clocks on a throwaway customer with `organization_id` metadata (see billing memory for the recipe; renewal invoices pay ~1h after period rollover — advance the clock the extra hours).

## Unrelated open item (not this session's scope)

Prod deploy of migrations 0037+0038 + one prod test-mode checkout to close the hardening arc.
