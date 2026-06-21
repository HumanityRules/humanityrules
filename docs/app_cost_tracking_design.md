# App Cost Tracking — Design

**Status:** implemented 2026-06-15 in `humanityrules_app/services/cost/`; tests in `humanityrules_app/tests/test_cost_subsystem.py`. Live end-to-end verification against a real account is the remaining step.
**Scope:** per-day, per-app **Bedrock invocation cost** on the app-detail page, built as a generic cost subsystem so other cost sources (Fargate, data transfer, …) plug in later without touching the model, view, chart, or job.

**Implementation notes (where reality extended this doc):**
- **Job model:** the worker only runs claimable model rows, so a `CostRefreshJob` model was added (status lifecycle, claimed by `app__label` in `job_worker._worker_loop`). The HTMX poll terminates on its status.
- **Pricing:** the 4.x rates are **not** machine-fetchable (the pricing page is JS-only; the bulk + Query Price List APIs carry only Claude 2/3). Rates were captured by hand from the pricing page (US East (Ohio), 2026-06-15) into `bedrock_pricing.py` as **both tiers' absolute rates** (`global` and `geo`), not a derived ratio. Cache-write uses the 5-minute-TTL column (logs don't expose TTL). To refresh: re-copy the two pricing-page tables.
- **Layout — keep the Django glue conventional.** An earlier cut built `services/cost/` as a *simulated app* (its own `models.py` with `Meta.app_label`, `views.py`, `urls.py`, `templates/`), which forced a re-import in central `models.py`, a `TEMPLATES['DIRS']` entry, an `include()`, and function-local imports to dodge a cycle. That was reverted as over-complex: models live in central `humanityrules_app/models.py`, the view in `views/apps.py`, the URL in `urls.py`, templates under `templates/humanityrules_app/apps/`. `services/cost/` now holds **only** the logic, flat (no `sources/` or `pricing/` subpackages).
- **`CostSource`** gained `rolling_24h_usd(app)` alongside `collect(...)` so the rolling figure stays source-owned while `cost_refresh` keeps it distinct from the day bins.

This doc records **decisions and pointers**, not values. It deliberately omits two kinds of detail: (a) mechanics you can regenerate — Django/HTMX/boto3/CloudWatch-Insights, ARN anatomy; and (b) **volatile external specifics — prices, rate ratios, percentages, exact API field/enum sets — which must be fetched from the Sources below at implementation time, never trusted from memory or from this doc.** An earlier draft hardcoded pricing multipliers and was wrong; that is the failure mode this rule exists to prevent.

---

## Resolve before coding (don't trust stale values)

1. **Job-worker dispatch/status API — not yet inspected.** Find how background jobs are enqueued and polled (the `App.label` / `run_job_worker` path) before wiring `cost/jobs.py` and the HTMX poll's done-condition.
2. **Pricing — fetch from the pricing page; trust no number in this doc.** Bedrock prices are **absolute per-category amounts** (input, output, cache-write, cache-read), *not* a base price with multipliers — pull all categories per model directly from the pricing page (Sources). Take whatever categories it lists; do not import first-party-Anthropic-API constructs (e.g. cache-TTL tiers). Get the geographic-vs-`global.` price relationship from the pricing page + the global-cross-Region-inference doc. **Do not hardcode any ratio or percentage** — that is exactly the error this doc was rewritten to prevent.
3. **Model location convention.** `AppDailyCost` in `cost/models.py` with `Meta.app_label="humanityrules_app"` (chosen for isolation) vs. the repo's single central `models.py`. migrations land in `humanityrules_app/migrations/` either way.

### Sources to fetch at implementation time (consult, don't memorize)

- **Bedrock pricing** (per-model absolute rates, per category): https://aws.amazon.com/bedrock/pricing/
- **Global vs geographic cross-Region inference** (price relationship, the full set of profile-id tier prefixes): https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html
- **Model invocation log schema**: https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html — note its example **omits** the cache-token fields; the captured record below is ground truth for what's actually delivered.

---

## Locked decisions

1. **Grain:** `AppDailyCost` keyed `(app, environment, source, date, subkey)`. Bedrock → `source="bedrock"`, `subkey=model_id`. Source-specific detail (token counts) lives in a JSON `details` field, **not** as columns, so the table stays source-agnostic.
2. **Accuracy:** cost from the log's token-count **metadata**; content/data-delivery logging stays **OFF** (privacy). Exact, because cache token counts are in metadata (see below).
3. **Refresh = cache-first:** the page reads the table instantly, enqueues a recompute job, and an HTMX poll swaps the chart in when the job finishes. Follow the polling contract in `docs/ui_live_update_contract.md`.
4. **Geo pricing:** geographic inference profiles cost **more** than `global.`; the model-id prefix encodes the tier (see the normalize note below). Apply per-tier absolute rates from the pricing page; if only the global rate is on hand, fall back to the documented premium and label the cost an estimate. Get the actual relationship/rates from Sources. Folded into `cost_for(...)` as the single source of truth.
5. **Isolation:** a self-contained `services/cost/` package with a pluggable `CostSource` interface; the `cost_refresh` module owns all reusable logic (window selection, freeze, upsert, rolling-24h). Adding a future source = one new file + a `COST_SOURCES` entry.

---

## Non-obvious facts (from AWS docs + a real record)

**Real log record** (`/devopshero/bedrock-invocations`, content logging disabled):

```json
{
    "timestamp": "2026-06-16T00:20:50Z",
    "accountId": "266117665083",
    "region": "us-east-1",
    "requestId": "c47d391b-fc83-4e79-93f9-4c5f477c02b1",
    "operation": "InvokeModelWithResponseStream",
    "modelId": "arn:aws:bedrock:us-east-1:266117665083:inference-profile/us.anthropic.claude-sonnet-4-6",
    "input": {
        "inputContentType": "application/json",
        "inputTokenCount": 3,
        "cacheReadInputTokenCount": 0,
        "cacheWriteInputTokenCount": 18779
    },
    "output": { "outputContentType": "application/json", "outputTokenCount": 12 },
    "identity": { "arn": "arn:aws:sts::266117665083:assumed-role/doh-default-hermes-vmendi01-task-role/21648c392d544730bcf8b4c42032021d" },
    "inferenceRegion": "us-east-2",
    "schemaType": "ModelInvocationLog",
    "schemaVersion": "1.0"
}
```

- The AWS doc example **omits** `input.cacheReadInputTokenCount`, `input.cacheWriteInputTokenCount`, and `inferenceRegion`. They are present even with all data-delivery modalities disabled, because they are **metadata, not content**. This is what makes exact, cache-aware cost possible without logging prompts/responses.
- **Token buckets are disjoint, each priced separately:** `inputTokenCount`, `cacheReadInputTokenCount`, `cacheWriteInputTokenCount`, `outputTokenCount`. Each has its **own** rate — they are **not** multiples of the input price (pull the actual rates from Sources). The record above is a cache-priming call — 3 fresh tokens, 18 779 written to cache — so naively using `inputTokenCount` alone would drastically undercount. The four operations (`InvokeModel`, `InvokeModelWithResponseStream`, `Converse`, `ConverseStream`) differ only in delivery; **sum across them**, never branch.
- **`modelId` is the inference-profile ARN**, not a short id. Normalize: take the segment after the last `/`, then strip the tier prefix (e.g. `global.`, `us.`, `eu.`, `apac.` — confirm the full set in the CRIS doc, Sources) → underlying model key for the rate lookup. The **prefix is the geo-pricing signal** (decision 4) — read it, then strip it.
- **ARN → app mapping is FORWARD.** `identity.arn` role = `doh-{env.slug}-{app.slug}-task-role`, truncated to 64 chars. `app_name = app.slug` is set in `services/jobs/app_config_builder.py` (NOT `app.name`, which some agent tools pass — a latent inconsistency; the canonical deploy path uses the slug). Env and app slugs both contain hyphens, so reverse-parsing the role name is ambiguous. Instead, for each environment the app is deployed to, **build the expected role name and filter the query on it**.
- **Log retention is bounded** — read the current value from `bedrock_logging_utils.LOG_GROUP_RETENTION_DAYS`. A day that ages out of that window can **never** be recomputed → the cache table *is* the durable history, and a frozen (`is_final`) past day must never be re-queried.
- **Geo tier affects price (AWS docs):** global cross-Region inference is cheaper than geographic; the model-id prefix tells you which tier was used (get the figure/rates from Sources). Price is billed by the **source region** (the env's region, where logs land) — not `inferenceRegion`.

---

## Query strategy

CloudWatch **Logs Insights** (not `FilterLogEvents`): one query does server-side aggregation —
`stats sum(inputTokenCount), sum(cacheReadInputTokenCount), sum(cacheWriteInputTokenCount), sum(outputTokenCount), count() by bin(1d), identity.arn, modelId`, filtered to the app's role(s) over the needed window. Async (`start_query` → poll `get_query_results`).

**Prior art for inspiration, but no need to copy:** `services/agent/tools/query_app_logs.py` (log querying in a customer account) and `_get_aws_session_for_environment(env)` in `services/infra_customer/iam_utils.py` (assumed-role session).

**Rolling "last 24h" headline** = a separate `now-24h … now` sum, always recomputed. It is **not** the sum of calendar-day bins — keep them distinct.

---

## Refresh / freeze logic (cost_refresh, source-agnostic)

Per pull: recompute **today** + **yesterday** (absorbs Bedrock delivery lag) + any **non-final gap day** within the retention window; mark days `< today-1` as `is_final=True`; never re-query final days.

---

## Package layout

```
humanityrules_app/
  models.py                       # AppDailyCost, CostRefreshJob (with the rest of the app's tables)
  urls.py                         # apps/<slug>/cost-panel/ → views.app_cost_panel
  views/apps.py                   # app_cost_panel — HTMX fragment endpoint (enqueue + render)
  templates/humanityrules_app/apps/  # _app_cost_panel.html, _app_cost_chart.html (inline SVG bars, no JS chart lib)
  services/cost/                  # the self-contained logic, flat:
    __init__.py                   # package overview only
    cost_source.py                # CostSource protocol + DailyCostRow dataclass
    cost_refresh.py               # COST_SOURCES, run_refresh(job_id), refresh_app: window/freeze/upsert/rolling-24h
    panel.py                      # cache-first panel context + inline-SVG chart geometry + enqueue_refresh
    bedrock.py                    # Bedrock source: CloudWatch Insights query → DailyCostRow
    bedrock_pricing.py            # ARN normalize, per-model absolute rates ($/1M: in/out/cache-write/cache-read), geo tier, cost_for()
```

`AppDailyCost`: `organization`, `app`, `environment`, `source`, `date`, `subkey`, `cost_usd`, `details` (JSON), `is_final`, `updated_at`; unique `(app, environment, source, date, subkey)`.

```python
@dataclass
class DailyCostRow:
    environment_id; date; subkey: str; cost_usd: Decimal; details: dict

class CostSource(Protocol):
    key: str
    def collect(self, app, start_date, end_date) -> list[DailyCostRow]: ...
```

All pricing/token knowledge stays inside `bedrock.py` + `bedrock_pricing.py`; the generic layer never sees a token or a model id.

---

## Seams into existing code (the only touches)

1. **`templates/humanityrules_app/apps/app_detail.html`** — one `{% include "humanityrules_app/apps/_app_cost_panel.html" %}`. The panel **self-loads** via HTMX, so `build_app_detail_context` is **unchanged**.
2. **`urls.py`** — one `path(...)` for the cost-panel fragment; the view sits in `views/apps.py` (`app_cost_panel`).
3. **Job worker** — `job_worker._run_cost_refresh_thread` calls `cost_refresh.run_refresh(job_id)`.
4. **Migration** — the `AppDailyCost` / `CostRefreshJob` tables under `humanityrules_app/migrations/`.

## Payoff

Adding e.g. Fargate cost later = write `fargate.py` implementing `CostSource`, add it to `cost_refresh.COST_SOURCES`. Model, cost_refresh, view, chart, and job are untouched; the chart gains a stacked series automatically.
