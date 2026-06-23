# Agent-driven deployments feature flag

`settings.AGENT_DEPLOYMENTS_ENABLED` (in `humanityrules_site/settings.py`) gates the
agent-driven provisioning surface — for both **apps** and **environments**. It is
**`False`** today: users deploy apps **only from templates** and provision environments
**only through a plain setup form**. The agent-backed editors are hidden in the UI and
blocked at the route level, but all of their backend code is left intact so the flow can
be restored by flipping a single boolean.

## Why it exists

Agent-driven deployments (chatting with the agent to author/redeploy an app) are no
longer the way users deploy — template deploy is. Rather than delete the agent code, we
gate it behind a flag so it is easy to revert if agent deployments come back, and so the
disabled surface is documented rather than lost to git history.

## To re-enable

Set `AGENT_DEPLOYMENTS_ENABLED = True` in `humanityrules_site/settings.py`. Nothing else
is required — the UI entry points reappear and the routes start serving again.

## What the flag controls

**UI (hidden when off)** — gated via the `agent_deployments_enabled` template variable,
injected by `humanityrules_app.context_processors.feature_flags`:

- `templates/humanityrules_app/apps/app_detail.html` — the top-right "Deployment" button.
- `templates/humanityrules_app/workspaces/workspace_detail.html` — the "New App" button.
  When off it links straight to the template picker; when on it opens the source-picker
  modal (From Repository / From Template). The `_new_app_source_modal.html` and
  `_repo_picker_modal.html` includes are also gated.
- `templates/humanityrules_app/environments/environments.html` — the **New Environment**
  button is the single gate for the environment flow: when on it opens the account-picker
  modal (→ agent setup editor); when off it jumps straight to the setup form. The
  `_aws_account_picker_modal.html` include is gated to the on state.

Everything downstream of that entry point is flag-free (the agent editor is reached only
from the New Environment gate when on):

- `views/environments.py::populate_environment_entrypoint` — environment cards always route
  to the detail page.
- `templates/humanityrules_app/environments/environment_detail.html` — always shows a
  "Retry Provisioning" button for a failed environment (no agent "Resume Setup").
- `templates/humanityrules_app/environments/environment_setup_form.html` — the AWS account
  is an in-page dropdown (re-fetches the form on change to reload that account's hosted
  zones), so no account-picker step is needed for the form flow.

**Routes (404 when off)** — gated via the `base.require_agent_deployments` decorator:

- `views/deployment_editor.py`: `deployment_editor`, `deployment_editor_new`,
  `deployment_editor_reset`, `deployment_editor_app_section`,
  `deployment_editor_blueprint_section`, `deployment_editor_fork`.
- `views/chat.py`: `chat_app_deploy` (the human-readable URL shortcut into an
  `APP_DEPLOYMENT` conversation; not linked from the UI, gated for completeness).
- `views/environment_editor.py`: `environment_editor`, `environment_editor_new`,
  `environment_editor_environment_section`, `environment_editor_reset`.

## What is NOT affected

These paths never touch the agent and keep working with the flag off:

- Deploy from template (`views/template_deploy.py`).
- Redeploy, teardown, remove app (`views/apps.py`).
- The Deployment Log tab on the app detail page.
- **Environment setup form** (`views/environments.py::environment_setup_form`) — the
  non-agent path: creates an `Environment` in `PENDING` status, which the job worker
  picks up and provisions. Always available, like template deploy.
- **Environment provisioning-log tab** (`environment_provisioning_log` +
  `_environment_provisioning_log.html`) and **retry** (`environment_retry`,
  `error` → `pending`).
- Environment teardown.

The provisioning machinery underneath (`DeploymentBlueprint` / `Deployment` /
`Environment` / `EnvironmentLog` models, the build/push/deploy and CDK pipelines) is
shared by both paths and is untouched. The agent is only an authoring surface on top.
