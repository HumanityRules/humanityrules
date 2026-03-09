# App Deployment Blueprint Specification

> Product and domain specification for the app deployment refactor. This document captures the agreed behavior and entity boundaries. It is intentionally not an implementation plan.

---

## Purpose

Refactor the app deployment experience so it is task-native instead of centered around the generic `/chat/` UI.

The target interaction model is the same as the Permissions Editor:

- Left panel: the task artifact
- Right panel: the conversation

For app deployment, the left panel must represent both:

- the **`App`** being discovered and defined
- the **`DeploymentBlueprint`** being authored for a specific environment

---

## Main Decisions

- Remove the global `Chat` item from the main left sidebar.
- Do not send users into the generic `/chat/` page for app deployment work.
- Use one agent and one continuous conversation for the full deployment workflow.
- Introduce **`DeploymentBlueprint`** as the deployable artifact for one `(app, environment)` pair.
- Keep **`Deployment`** as the execution/history record for one attempt to apply a blueprint.
- Rename the `Deployment` status `deployed` to `succeeded`. Deployment is an attempt record; its states should describe attempt outcomes, not ongoing status.
- Drop the `superseded` status from `Deployment`. Whether a succeeded deployment is still the current one is now the blueprint's concern (`active`), not the deployment's.
- Future direction: rename `Deployment` to `DeploymentAttempt` when practical. This is not required for the first refactor.
- Do not introduce a separate `AppDeploymentDraft` model. The blueprint itself is the environment-specific task artifact.
- The left panel should show both the `App` section and the `DeploymentBlueprint` section, in that order.

---

## Domain Model

### App

`App` is the stable identity of the thing being deployed.

`App` should own the fields that answer: "What thing in this repository are we talking about?"

Agreed fields for `App`:

- `workspace`
- `repository`
- `name`
- `slug`
- `repo_subpath` (the app root within the repository)
- `app_type`
- `build_strategy`
- declaration/interface fields for environment variables and secrets

Strong current leaning for `App` as well:

- `dockerfile_path`
- `container_port`
- `health_check_path`
- `health_check_command`

Reasoning:

- These describe the app's source/build/interface more than environment-specific runtime variation.

### DeploymentBlueprint

`DeploymentBlueprint` is the desired deployable state for one `(app, environment)` pair.

It answers: "How should this app run in this environment?"

`DeploymentBlueprint` should own environment-specific deployment/runtime configuration, including:

- parent `app`
- target `environment`
- tracked branch for that environment
- CPU
- memory
- environment variable values/bindings
- secret values/bindings
- datastore binding
- subdomain
- other environment-specific runtime settings

The blueprint is the environment-specific artifact within the left panel of the deployment editor.

### Deployment

`Deployment` remains the concrete execution/history record.

It represents one attempt to apply a blueprint and stores attempt status, outputs, timestamps, and logs.

Core rules:

- A `Deployment` must point to the exact `DeploymentBlueprint` it applied.
- `Deployment` does not have its own `app` or `environment` FKs. Both are reachable via the blueprint. The blueprint is the single source of truth for what app and what environment.

### Conversation

Conversation context changes over the lifecycle:

1. After repository selection in `New App`, the conversation starts with `workspace + repository` context.
2. After repo analysis produces enough identity data, the system creates the `App`.
3. The conversation becomes app-scoped.
4. Once the target environment and deployable configuration exist, the system creates the `DeploymentBlueprint`.
5. The conversation becomes blueprint-scoped for the active deployment task.

The conversation should point first to `App`, then to `DeploymentBlueprint`.

---

## Left Panel Structure

The left panel should show two explicit sections:

1. `App`
2. `DeploymentBlueprint`

This reflects the actual workflow:

- the agent first discovers and defines the app identity and source/build/interface fields
- then the agent authors the environment-specific deployable state
- then deployment applies that blueprint

The UI should not flatten these into one undifferentiated form.

The user should be able to see clearly whether the conversation is currently:

- still figuring out what the app is
- or already refining how that app should run in a specific environment

The `App` section should appear first, with the `DeploymentBlueprint` section underneath it.

---

## Blueprint States

Minimal agreed states for `DeploymentBlueprint`:

- `draft`
- `deploying`
- `failed`
- `active`
- `discarded`

Behavior:

- `draft` is editable.
- `deploying` means a deployment is currently applying the blueprint.
- `failed` remains editable and retryable.
- `active` means the blueprint is the current successful desired state for that `(app, environment)` pair.
- `discarded` is deferred in product behavior but reserved as the state for abandoning a draft blueprint.

---

## Deployment States

`Deployment` is an attempt record. Its states describe attempt outcomes, not ongoing status.

Changes from the current model:

- Rename `deployed` to `succeeded`. A deployment that worked is a succeeded attempt, regardless of whether it is still the current one.
- Drop `superseded`. The blueprint's `active` state tracks which desired state is current. There is no need for deployments to retroactively change state when a new deployment succeeds.

Remaining states are unchanged: `pending`, `building`, `pushing`, `deploying`, `starting`, `succeeded`, `failed`, `rolled_back`, `torn_down`, `teardown_pending`, `tearing_down`.

---

## New App Workflow

### Entry

The default workspace detail page does not contain a permanent chat panel.

`New App` still starts from the workspace detail page with repository selection.

### Flow

1. User clicks `New App`.
2. User selects a repository.
3. System creates a conversation scoped to the selected workspace and repository.
4. The agent analyzes the repository.
5. The agent determines the app identity/build/interface parameters needed to create the `App`.
6. System creates the `App`.
7. The conversation becomes app-scoped.
8. The agent selects the target environment based on workspace and account context.
9. Once the target environment is known and deployment configuration is ready, the system creates the `DeploymentBlueprint`.
10. The conversation becomes blueprint-scoped.
11. The user and agent continue in the deployment editor: app section plus blueprint section on the left, conversation on the right.
12. When deployment is triggered, the system creates a `Deployment` from that blueprint.

### Important Constraint

The blueprint should not exist without an app behind it.

That is why repository analysis must happen before `App` creation, and `App` creation must happen before `DeploymentBlueprint` creation.

---

## Workspace Detail Behavior

- The default workspace detail page does not have a permanent chat panel.
- Not-yet-successful apps remain in the main Apps list.
- For v1, do not add a separate generic Blueprints section to workspace detail.
- Unlaunched or failed-first-deploy apps should be represented in the Apps list with an appropriate status and a clear resume action.

Agreed behavior:

- `Resume Setup` should take the user back into the deployment editor/conversation for that app.

Discard/delete behavior is deferred.

---

## App Detail Behavior

- The default app detail page does not have a permanent chat panel.
- The app detail page remains the stable page for an existing app: configuration, environments, history, and management.
- Deployment work should happen in the dedicated deployment editor, not inline on the app detail page.

The redeployment branch for already-existing apps is intentionally deferred from this specification, except where it affects naming and entity boundaries.

---

## First Deployment UX

### During Deployment

After deployment is triggered from the deployment editor:

- The user stays in the same workspace.
- The blueprint remains the active task surface.
- The left panel should transition from editable draft to deployment/progress view.
- The right panel remains the same conversation.

### On Success

Do not auto-redirect to app detail.

Instead:

- Keep the user in the deployment editor.
- The agent should summarize what happened.
- The UI should provide explicit links to:
  - the deployed app
  - the app detail page

This preserves continuity and keeps the deployment task readable after completion.

### On Failure

- Keep the user in the same deployment editor.
- The same blueprint remains editable and retryable.
- Retries happen from the same blueprint.

---

## Environment-Specific Branch Tracking

- `App` should not have its own branch field.
- By default, a `DeploymentBlueprint` uses `Repository.default_branch`.
- If an environment needs a different branch, `DeploymentBlueprint.branch` stores that override.

---

## Environment Variables and Secrets

Agreed model:

- `App` owns the declaration/interface:
  - what variables and secrets exist
  - metadata such as description, placeholder, required/optional, auto-generated intent
- `DeploymentBlueprint` owns the environment-specific values and bindings

Reason:

- The app declares what it needs.
- The blueprint says how that need is satisfied in a specific environment.

---

## Deferred Topics

The following topics are intentionally out of scope for this specification:

- The user-facing redeployment workflow for already-existing apps
- Blueprint discard UX
- Early-app deletion / abandoned app cleanup UX
- Full historical blueprint versioning beyond what is needed for current and in-progress state
- The future rename from `Deployment` to `DeploymentAttempt`

---

## MCP Tooling Note

This model requires MCP tools that let the agent manage `App` and `DeploymentBlueprint` incrementally during the conversation.

The current monolithic `deploy_app` flow is not sufficient for the target interaction model, because the new workflow separates:

- repository analysis
- app creation and updates
- blueprint creation and updates
- deployment triggering

This is a tooling consequence of the specification, not an implementation plan.

---
