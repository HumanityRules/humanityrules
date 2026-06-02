---
name: posthog
description: Guide a user through PostHog after they connect it. PostHog's MCP exposes ~350 tools across many product areas; this skill helps you ask the right questions and narrow the surface to what the user actually needs.

version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [PostHog, Analytics, FeatureFlags, Experiments, ErrorTracking, Logs, LLMAnalytics, DataWarehouse, MCP]
---

# PostHog

Use this skill when the user mentions PostHog — "I connected PostHog", "help me with PostHog", "set up PostHog", or any request that touches PostHog data (events, flags, experiments, dashboards, errors, logs, LLM analytics, warehouse).

## Why this skill exists

PostHog's MCP server exposes ~350 tools across ~30 product areas. Loading the full surface burns the agent's tool budget on capabilities the user isn't using. There's a `posthog-set-config` tool (reached via `integrations_call_tool`) that scopes the surface to what the user actually needs — your job is to help them pick.

## Onboarding flow

When the user has just connected PostHog (or first asks for help with it), follow this script:

1. **Briefly orient them.** PostHog covers a lot of ground:
   - Product analytics (events, insights, dashboards, cohorts, persons)
   - Feature flags & experiments
   - Session recordings & heatmaps
   - Error tracking & APM/tracing
   - Logs
   - LLM analytics (evaluations, prompts, scorers, traces)
   - Data warehouse (external sources, sync jobs, views, SQL)
   - CDP / hog_functions / workflows
   - Surveys, notebooks, alerts, annotations
2. **Ask what they want to do today.** A focused workflow gets a focused surface. Examples:
   - "Run an experiment / launch a feature flag" → `flags`, `experiments`
   - "Triage errors" → `error_tracking`, `logs`, `events`
   - "Investigate user behavior" → `insights`, `dashboards`, `persons`, `cohorts`, `sql`
   - "Watch LLM apps" → `llm_analytics`, `prompts`
   - "Build / debug a data pipeline" → `data_warehouse`, `data_schema`, `sql`
3. **Pick a project (strongly recommended).** If the user has multiple projects in PostHog, ask for the project they're working in. Get the id from PostHog → Project Settings → "Project ID". Without pinning, every call goes through a `switch-project` round-trip.
4. **Apply the config.** Call `integrations_call_tool(tool_id="posthog-set-config", args={"features": [...], "project_id": "..."})`. The catalog reloads automatically.
5. **Confirm and proceed.** Tell the user what's now in scope, then start the workflow.

## Key tools

All PostHog tools — including the three config tools below — are reached the same way: `integrations_call_tool(tool_id="...", args={...})`. They appear in `integrations_search_tools` results once PostHog is connected.

- **`posthog-list-feature-categories`** — exhaustive list of `?features=` values you can pass.
- **`posthog-get-config`** — read the current scoping.
- **`posthog-set-config`** — write it. `args`: `features` (list), `tools` (list), `organization_id` (str), `project_id` (str). All optional and unchanged if omitted; pass `[]` to clear features/tools, `""` to clear the org/project pin. Catalog reloads on success.
- **`integrations_search_tools(query, limit=10)`** — discover specific PostHog tools after scoping. Search inside the narrowed surface.
- **`integrations_describe_tool(tool_id)`** — get the full input schema for a tool.
- **`integrations_call_tool(tool_id, args)`** — invoke a tool. If `mutates=true`, summarize the action and confirm with the user first.

## Feature categories cheat sheet

The categories accepted by `?features=` (hyphens and underscores are equivalent):

- **`workspace`** — projects, organizations, users
- **`actions`, `events`, `cohorts`, `persons`, `annotations`** — analytics primitives
- **`insights`, `dashboards`, `sql`** — analysis
- **`flags`, `experiments`, `early_access_features`** — experimentation
- **`surveys`** — surveys
- **`notebooks`** — notebooks + comments
- **`error_tracking`** — issues, grouping rules, symbol sets
- **`logs`** — log query, alerts, sparklines
- **`llm_analytics`, `prompts`** — LLM observability + eval suite
- **`data_warehouse`, `data_schema`** — external sources, sync jobs, views
- **`hog_functions`, `hog_function_templates`, `workflows`** — CDP
- **`alerts`, `activity_logs`, `conversations`** — operational
- **`debug`, `sdk_doctor`, `reverse_proxy`, `search`, `docs`** — meta

If the user's request doesn't map cleanly to one category, prefer 2-4 categories over the full firehose.

## Patterns

**User: "I connected PostHog, now what?"**
→ Run the onboarding flow. Ask what they want to do today. Don't dump the full feature list — orient them in 1-2 sentences and ask.

**User: "Show me my insights" (without prior scoping)**
→ Call `posthog-get-config` first. If `features` is empty and the agent's tool budget can handle it, search; otherwise call `posthog-set-config` with `features=["insights","dashboards"]` first, then search.

**User: "Why is the experiment not shipping?"**
→ Scope to `["experiments","flags"]`, search for `experiment-results`, `experiment-stats`, `experiment-status`, and inspect with `integrations_describe_tool`. PostHog has rich operational tools (launch/pause/end/ship-variant) — use them, not just CRUD.

**User asks across categories** (e.g. "an error spike correlates with my new flag")
→ Scope to `["error_tracking","flags","logs"]` and reason across all three.

## Don'ts

- **Don't construct PostHog URLs or API calls yourself.** Use `integrations_call_tool`. The aggregator handles auth.
- **Don't assume `connected` means the user is in the right project.** Ask, then call `posthog-set-config` with `project_id`.
- **Don't broaden the surface without asking.** If the user wants something outside the current scope, ask before widening — they may want to keep the agent focused.
- **Don't call `mutates=true` tools without confirmation.** Summarize the action and get approval first.
