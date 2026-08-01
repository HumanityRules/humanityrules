"""Credits: what usage costs and what the ledger says an organization has left.

- ``plans.py`` — the plan entitlement registry, the per-org override schema, and
  ``effective_plan``, the only way anything reads what an organization may do.
- ``rates.py`` — the frozen, date-versioned rate cards and the model-family
  matching that picks a price for one observed model id.
- ``rating.py`` — the only code that turns BillingUsageEvents into credits, run
  as a periodic tick in the CP job worker.
- ``grants.py`` — grant and expiry ledger entries under the balance lock: the
  one-time trial allotment and the expire-then-grant pair of an Operator renewal.
- ``stripe_lifecycle.py`` — the one module that talks to Stripe: Checkout and
  portal sessions, webhook mirroring into BillingSubscription, and the plan
  transition.
- ``entitlements.py`` — the snapshot the integrations broker enforces against.
- ``billing_page.py`` — the plain values the billing settings page renders.

The models live in ``humanityrules_app/models.py`` with the rest of the app's
tables. ``services/cost/`` is a different subsystem answering a different
question: what usage cost HumR in USD, not what HumR charges for it in credits.
"""
