"""Credits: what usage costs and what the ledger says an organization has left.

- ``plans.py`` — the plan entitlement registry, the per-org override schema, and
  ``effective_plan``, the only way anything reads what an organization may do.
- ``rates.py`` — the frozen, date-versioned rate cards and the model-family
  matching that picks a price for one observed model id.
- ``rating.py`` — the only code that turns BillingUsageEvents into credits, run
  as a periodic tick in the CP job worker.
- ``grants.py`` — positive ledger entries (trial credits today, renewals later),
  written under the balance lock like every other credit movement.
- ``entitlements.py`` — the snapshot the integrations broker enforces against.

The models live in ``humanityrules_app/models.py`` with the rest of the app's
tables. ``services/cost/`` is a different subsystem answering a different
question: what usage cost HumR in USD, not what HumR charges for it in credits.
"""
