"""Pluggable, per-app cost subsystem.

Entry points (the HTTP view in ``humanityrules_app/views/apps.py`` and the job worker call in here):
- ``panel.build_panel_context(app)`` — cache-first, read-only context for the app-detail cost panel,
  built from the ``AppDailyCost`` table (instant; never blocks on AWS).
- ``panel.enqueue_refresh(app)`` — ensure a pending ``CostRefreshJob`` exists (deduped).
- ``cost_refresh.run_refresh(job_id)`` — the worker entry point; ``cost_refresh.refresh_app(app)`` does
  the source-agnostic recompute.

Adding a new cost source = one ``CostSource`` implementation (see ``cost_source.py``) added to
``cost_refresh.COST_SOURCES``; the models, cost_refresh, view, chart, and job stay untouched
(see ``docs/app_cost_tracking_design.md``). The models live in ``humanityrules_app/models.py`` with the
rest of the app's tables; everything else is here.
"""
