# Agent-driven deployments feature flag

`settings.AGENT_DEPLOYMENTS_ENABLED` (in `humanityrules_site/settings.py`) gates the
agent-driven deployment surface. It is **`False`** today: users deploy **only from
templates**. The agent-backed "Deployment" editor is hidden in the UI and blocked at the
route level, but all of its backend code is left intact so the flow can be restored by
flipping a single boolean.

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

**Routes (404 when off)** — gated via the `base.require_agent_deployments` decorator:

- `views/deployment_editor.py`: `deployment_editor`, `deployment_editor_new`,
  `deployment_editor_reset`, `deployment_editor_app_section`,
  `deployment_editor_blueprint_section`, `deployment_editor_fork`.
- `views/chat.py`: `chat_app_deploy` (the human-readable URL shortcut into an
  `APP_DEPLOYMENT` conversation; not linked from the UI, gated for completeness).

## What is NOT affected

These paths never touch the agent and keep working with the flag off:

- Deploy from template (`views/template_deploy.py`).
- Redeploy, teardown, remove app (`views/apps.py`).
- The Deployment Log tab on the app detail page.

The deployment machinery underneath (`DeploymentBlueprint` / `Deployment` models, the
build/push/deploy pipeline) is shared by both paths and is untouched.
