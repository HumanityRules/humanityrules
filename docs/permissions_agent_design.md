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

## Open Tasks

### 1. Pass app/environment context to the agent

The agent needs to know the app slug, environment slug, AWS account ID, region, repository URL, and task role name. This context is available through the `AppPermissionRequest` → `App` / `Environment` chain.

### 2. CloudTrail setup on customer accounts

Required for log-based permission analysis. Key considerations:

- **Management events vs. Data events** — Management events (e.g., `CreateBucket`, `DeleteTable`) are cheap and enabled by default in most accounts. Data events (e.g., `GetObject`, `PutItem`) are where most permission denials happen but cost significantly more and are often not enabled.
- **Existing trails** — The customer may already have CloudTrail enabled. We need to discover and hook into existing trails rather than always creating a new one.
- **CloudTrail Lake vs. S3 + Athena vs. CloudWatch Logs** — Different query mechanisms with different cost/latency tradeoffs. Needs detailed analysis.
- **Multi-region** — CloudTrail can be configured per-region or as an organization trail. We need to handle both cases.

### 3. CloudWatch log access for ECS task logs

Required for detecting runtime permission errors from the app's own logs (not just CloudTrail API-level denials). The ECS task already writes to CloudWatch Logs. The agent needs:

- The log group name (derivable from the environment/app naming convention)
- Permission to call `FilterLogEvents` or `GetLogEvents` on the customer's account
- A strategy for searching relevant time windows (not scanning all history)



## Discussion about sources of information for detecting permission errors

We discussed sources of information for detecting when a DOH-managed app has IAM permission errors:

  CloudTrail
  - Logs all AWS API calls including AccessDenied errors with exact action, resource, and principal.
  - Limitation: by default only logs management events (control-plane like CreateBucket, ListBuckets). Data events (what apps actually do — GetObject, PutObject, Query, GetItem) are
   NOT logged by default.
  - Solution: you can enable data events on a trail, scoped by resource ARN to control cost. DOH knows the exact resources from the permission statements, so it can scope tightly.
  For low-traffic internal apps with dedicated resources, cost is negligible.
  - Cannot filter capture by role — advanced event selectors don't support userIdentity. But you filter by role ARN at query time, which is fine.

  CloudWatch Logs (application logs)
  - Already available — DOH creates log groups per app. AWS SDK errors (AccessDeniedException, is not authorized to perform, etc.) appear in app stdout/stderr.
  - Less structured than CloudTrail — requires pattern matching across different SDK languages/formats.
  - No additional setup or cost.

  Other sources mentioned (less central):
  - ECS stopped task reasons (startup-time failures only)
  - IAM Access Analyzer (policy generation from usage patterns)
  - IAM Policy Simulator (pre-validation, not runtime detection)

  Conclusion: both CloudTrail and CloudWatch Logs are worth pursuing. CloudTrail gives structured, precise data (action + resource) but needs data events enabled. CloudWatch Logs
  are free and already there but need parsing.

---

## Design Principles

- **DOH controls permissions from inception.** Unlike brownfield environments where you're tightening existing overprivileged policies, apps deployed through DOH start with no permissions. The agent's primary job is suggesting what to add, not what to remove.

- **Agent suggests, user decides.** The agent was previously given the ability to modify permission statements directly via MCP tools, but this was removed (2026-02-18). The current model is that the agent advises through the chat panel and the user makes edits in the editor UI.
