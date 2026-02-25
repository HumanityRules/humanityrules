# Permissions Agent Design

> Design document for the AI-powered permissions agent that helps users configure IAM permissions for their deployed apps.

---

## Purpose

The permissions agent assists users in configuring least-privilege IAM policies for their ECS task roles. It operates within the permissions editor's chat panel (right side) and has five core capabilities:

- **Source code analysis** — Analyze the app's source code to detect how it uses AWS resources (S3 buckets, DynamoDB tables, SQS queues, etc.) and suggest an initial permission set.

- **Log analysis** — Use CloudTrail and CloudWatch (ECS task logs) to detect permission denials at runtime and suggest permission requests to fix them.

- **Transitive dependency inference** — Suggest implicit permissions that aren't visible in source code or logs. For example, if the app uses an S3 bucket with SSE-KMS encryption, it needs `kms:Decrypt` and `kms:GenerateDataKey`.

- **Blast radius assessment** — When a permission request is submitted for approval, generate a human-readable risk summary for approvers (e.g., "This grants the app write access to all objects in the `prod-customer-data` bucket").

- **Draft review** — When a user manually edits permissions through the editor, review the draft and flag issues (redundant permissions, overly broad access relative to actual code usage, permissions already covered by the base task role).

---

## Agent Context

The agent's context anchor is **app+environment**, not a deployment. `AppPermissions` and `AppPermissionRequest` are keyed on `(app, environment)`, and everything the agent needs (AWS account, region, task role name, log group, repo) is derivable from this pair. A deployment is a **precondition** (infrastructure must exist) but not the context source.

Add a `context_app_permission_request` FK (nullable) to `Conversation`. This gives the agent one-hop access to the app, environment, and current draft statements. Remove the current FK from `AppPermissionRequest` to `Conversation` — the Conversation points to what it's about, not the other way around.

---

## Error Detection: Data Sources and Priority

The agent detects permission errors through multiple sources, prioritized by value vs. setup cost:

**1. Source code analysis** (primary) — Agent analyzes the app's repo to detect AWS resource usage and suggest an initial permission set. Covers most cases upfront before the app even hits a runtime error.

**2. CloudWatch Logs** (runtime, zero setup) — DOH already creates log groups per app. AWS SDK errors (`AccessDeniedException`, `is not authorized to perform`, etc.) appear in app stdout/stderr. Less structured than CloudTrail — requires pattern matching across SDK languages — but free and already there.

**3. CloudTrail `LookupEvents` API** (management events, zero setup) — Every AWS account logs management events by default. The agent can call `LookupEvents` through the existing assumed role to find `AccessDenied` errors on management-plane operations (e.g., `CreateTable`, `ListBuckets`). No infrastructure to provision — just an API call, like `list_resources_for_services` today.

**4. CloudTrail Lake event data store** (data events, deferred) — A DOH-managed event data store in the customer's account with data events scoped to the app's known resources. Provides structured detection of data-plane denials (`GetObject`, `PutItem`, etc.). Significant setup complexity (CloudFormation per customer, event selector lifecycle, Lake query API, cost management) for incremental value over items 1-3. Cherry-on-top feature, mainly for demo wow factor.

### Workflow

1. App is deployed (starts with no extra permissions)
2. User opens permissions editor — agent analyzes source code, suggests permissions with specific resources
3. User reviews, adjusts, approves
4. App runs — if it hits permission denials, CloudWatch Logs and `LookupEvents` catch them
5. Agent detects errors, suggests follow-up permission requests
6. (Phase 2) DOH enables CloudTrail data events scoped to the approved resources, expanding scope as new resources are added

### Data source details

- **CloudTrail** — Logs all AWS API calls including AccessDenied with exact action, resource, and principal. By default only management events are logged. Data events (what apps actually do — GetObject, PutItem) are NOT logged by default. Can be enabled scoped by resource ARN. Cannot filter capture by role — filter by role ARN at query time.
- **CloudWatch Logs** — AWS SDK errors appear in app stdout/stderr. Less structured, requires pattern matching. No additional setup or cost.
- **Other sources** (less central): ECS stopped task reasons (startup failures only), IAM Access Analyzer (policy generation from usage patterns), IAM Policy Simulator (pre-validation, not runtime detection).

---

## Agent Tools

The permissions agent uses a restricted tool set — it doesn't need deployment, git, or infrastructure tools.

**Built-in tools (source code analysis):**
- Read, Grep, Glob — the agent reads the cloned repo in its sandbox to detect AWS resource usage. This is how the initial source code analysis works today.

**New MCP tools to build:**

- **`query_app_logs`** — Assumes the customer's role, calls `FilterLogEvents` on the app's CloudWatch log group, filters for permission error patterns (`AccessDeniedException`, `is not authorized to perform`, `Access Denied`). Log group name derived from environment/app naming convention. Takes a time window parameter.

- **`lookup_access_denied_events`** — Assumes the customer's role, calls CloudTrail `LookupEvents`, filters client-side for the app's task role ARN and `errorCode: AccessDenied`. Management events only. Takes a time window parameter.

**First-turn behavior:** When the conversation starts, the agent introduces itself and presents its capabilities — it does NOT proactively run analysis. The user chooses what to do first (source code analysis, log checks, draft review, etc.).

**Tool scoping:** The `allowed_tools` in `_create_agent_options` should be restricted for PERMISSIONS mode conversations to only the tools above. The current deployment/git/infra tools are irrelevant and add noise to the agent's tool list.

---

## Design Principles

- **DOH controls permissions from inception.** Unlike brownfield environments where you're tightening existing overprivileged policies, apps deployed through DOH start with no permissions. The agent's primary job is suggesting what to add, not what to remove.

- **Agent suggests, user decides.** The agent was previously given the ability to modify permission statements directly via MCP tools, but this was removed (2026-02-18). The current model is that the agent advises through the chat panel and the user makes edits in the editor UI.
