"""Credits: what usage costs and what the ledger says an organization has left.

- ``rates.py`` — the frozen, date-versioned rate cards and the model-family
  matching that picks a price for one observed model id.
- ``rating.py`` — the only code that turns BillingUsageEvents into credits, run
  as a periodic tick in the CP job worker.

The models live in ``humanityrules_app/models.py`` with the rest of the app's
tables. ``services/cost/`` is a different subsystem answering a different
question: what usage cost HumR in USD, not what HumR charges for it in credits.
"""
