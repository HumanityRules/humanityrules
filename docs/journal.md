# DevOpsHero Development Journal

## 2026-01-24 12:15 - Repository Picker Modal Scrollbar Styling

Applied consistent scrollbar styling to the repository picker modal. Added `dark-scrollbar` class, `scroll-smooth`, and `scrollbar-gutter: stable` to match the chat interface scrollbar appearance.


## 2026-01-24 11:30 - Integrate Local Repos into Repository Model

Replaced dynamic `list_deployable_repos` filesystem scan with persisted Repository records.

**The problem:** Two parallel discovery mechanisms — `list_deployable_repos` scanned `deployable_repos/` at runtime returning `file://` URLs, while `list_repositories` queried Repository model from DB. Agent needed two tools for the same conceptual operation.

**The solution:** New management command `seed_local_repos --org=<slug>` scans `deployable_repos/` and creates Repository records with `provider="local"` and `file://` clone URLs. Agent now uses unified `list_repositories` for all repo discovery.

Changes:
- Created `seed_local_repos` management command
- Deleted `list_deployable_repos.py` and removed from mcp_tools
- Removed dead code: `_parse_file_url()` and deprecated `scan_repository(url, branch)` from scan_repository.py
- Renamed `scan_repository_path` to `scan_repository`

Repository model already supported `provider="local"` and `file://` URLs. `repo_service.clone_repository` already handles `file://` URLs by copying directory contents.


## 2026-01-24 01:45 - Git Cloning and Agent Sandboxing

Implemented repository cloning with GitHub App authentication and enabled Claude Agent SDK sandboxing.

### Repository Cloning

New `repo_service.py` handles cloning:
- GitHub repos: Uses installation token in URL (`https://x-access-token:{token}@github.com/...`)
- Local `file://` URLs: Copies to sandbox dir (was returning path directly, changed for consistency)
- Re-uses existing clones for same conversation (clone_id based on conversation ID)
- Clones persist for conversation lifetime (no per-message cleanup)
- TODO: Implement cleanup on conversation close or via periodic job (see devopshero-3ur)

Clone path moved to `settings.CLAUDE_SANDBOX_DIR` (was `CLONE_BASE_DIR` in repo_service).

### Agent Sandboxing

`stream_response()` now clones repo when `conversation.context_repository_id` is set, passes path to agent options. Agent's `cwd` is set to the cloned repo directory.

Enabled `SandboxSettings(enabled=True, autoAllowBashIfSandboxed=True)` to restrict agent's filesystem access.

### TMPDIR Fix

Sandbox initially crashed with `EOPNOTSUPP: watch` errors on `/var/folders/...` paths. The Claude CLI was trying to access macOS system temp (where VS Code sockets exist).

Root cause: `TMPDIR=/var/folders/...` in environment, which is outside the sandbox's allowed paths.

Fix: Override `TMPDIR` to point to our sandbox directory:
```python
env = {
    **get_claude_env(),
    "TMPDIR": str(settings.CLAUDE_SANDBOX_DIR),
}
```

Note: Claude docs mention `CLAUDE_CODE_TMPDIR` for internal temp files, but it's narrowly scoped. Standard `TMPDIR` covers all temp operations.

### Files Changed

- `repo_service.py` — Clone/cleanup functions
- `agent_service.py` — Sandbox config, repo cloning in stream_response
- `deployment_executor.py` — Uses repo_service for cloning
- `app_config_builder.py` — Accepts explicit repo_path parameter
- `mcp_tools.py` — scan_repository uses repo_service
- `settings.py` — Added `CLAUDE_SANDBOX_DIR`


## 2026-01-23 21:30 - Dev Server Reload Exclusions

Added `tmp/` exclusion to uvicorn's file watcher so cloned repos don't trigger reloads.

**The bug:** uvicorn's `--reload-exclude` with relative paths like `tmp` doesn't work. Watchfiles reports absolute paths, but uvicorn's `FileFilter` compares `Path("tmp") in path.parents` directly — a relative Path is never `in` an absolute path's parents.

**The fix:** Use absolute paths:
- `Procfile.tailwind`: `--reload-exclude "$(pwd)/tmp"` (shell expansion)
- `run_dev.py`: `reload_excludes=[str(Path("tmp").resolve())]`

Also added `run_dev.py` for debugger configs and standalone runs. Can't use it from Procfile because `uvicorn.run()` with `reload=True` spawns subprocesses that conflict with honcho's process management.


## 2026-01-23 19:45 - Conversation Context UI

Replaced agent-driven workspace/repository selection with UI-driven context selection. Users now enter conversations from the workspace page with context pre-set.

### Why This Change

The original flow required users to start a conversation, then use agent tools (`select_workspace`, `create_workspace`, `list_workspaces`) to establish context. This was awkward:
- Users had to explain what they wanted before the agent knew where to work
- Agent had to ask clarifying questions about workspace/repo selection
- Context wasn't visible until the agent responded

The new flow: users click "New Conversation" or "New App" from a workspace page, and context is set before the conversation starts.

### Two Entry Points

- **"New Conversation"** — Creates workspace-scoped conversation for managing existing apps
- **"New App"** — Opens repo picker modal, then creates workspace+repo scoped conversation for deploying a new app

This solves the chicken-and-egg problem: apps require a repository, but we can't select from existing apps when creating a new one.

### Implementation

- **Workspace detail page** (`/workspaces/<workspace_slug>/`) — Shows apps, datastores, recent conversations, with action buttons
- **Repo picker modal** — Lists organization's connected repositories for "New App" flow
- **Chat URL params** — `chat_new` accepts `?workspace=<id>&repo=<id>` to set context
- **Chat header** — Displays "Workspace: X" and optionally "Repository: Y" 
- **System prompt injection** — `_build_system_prompt()` appends context section with workspace/repo names and IDs
- **create_app tool** — Now gets repository from `context_repository_id` if not passed explicitly

### Removed Agent Tools

Deleted workspace management tools since workspaces are now UI-only:
- `select_workspace` — Context set via UI
- `create_workspace` — Workspaces created via UI (default auto-created on org setup)
- `list_workspaces` — Not needed without select/create

Updated system prompt to reflect that workspace context comes from UI, not agent tools.

### Learnings

- When renaming URL parameters (e.g., `slug` → `workspace_slug`), must update: URL pattern, view function parameter, template `{% url %}` tags, and any f-strings using the old name
- Templates must be exported from `views/__init__.py` to be accessible via `views.function_name`
- The `<slug:param_name>` syntax has two parts: converter type and parameter name


## 2026-01-23 - GitHub App Integration

Implemented GitHub App OAuth flow to connect organizations to GitHub and sync repositories.

### Design Decisions

- **GitHub App over OAuth App** — GitHub Apps provide org-wide installation, fine-grained permissions, and installation access tokens (short-lived, no PATs to manage).
- **Auto-sync on connect** — Repositories are fetched immediately after OAuth callback, so users see their repos right away.
- **Hybrid sync model** — Initial sync on connect, plus `sync_repositories` agent tool and UI "Re-sync" button for manual refresh.
- **Credentials split** — App-level credentials (ID, private key, client secret) in `.env`; per-org `installation_id` in database (`GitProviderIntegration` model).

### Implementation

- **GitHub service** (`services/github/github_client.py`) — JWT generation for App auth, installation token exchange, repo listing, sync logic that adds/updates/removes `Repository` records.
- **OAuth flow** (`views/github.py`) — `/github/connect` redirects to GitHub App installation, `/github/setup` callback creates `GitProviderIntegration` and triggers sync.
- **Settings UI** — New "Git Integrations" tab showing connection status, repo count, and Re-sync button.
- **Agent tools** — `list_repositories` queries local DB, `sync_repositories` re-fetches from GitHub API.
- **Webhook endpoint** (`/api/github/webhook`) — Stubbed for future auto-deploy on push.

### Configuration

Created GitHub App "DevOps Hero App" under DevOpsHeroAI organization with:
- Permissions: Contents (read/write), Metadata (read), Pull Requests (read/write)
- Events: Push (for future auto-deploy)
- Callback URL: `https://devopshero.ngrok.io/github/callback`
- Setup URL: `https://devopshero.ngrok.io/github/setup`

### Fixes During Testing

- Added `LOGIN_URL = '/auth/login/'` — Django's default `/accounts/login/` doesn't exist in this project.


## 2026-01-23 04:55 - Domain Model Refactor: Workspace as Governance

Major refactor to make Repository a first-class entity and transform Workspace into a governance-only container.

### Key Changes

- **New models:** `GitProviderIntegration` (org-level GitHub/GitLab connection), `Repository` (connected git repos)
- **Workspace simplified:** Removed `primary_repo_url`, `aws_account`, `aws_region` — now purely governance/policy container
- **App sources from Repository:** Added `repository` FK and `repo_subpath` to App model
- **Environment owns region:** Moved `aws_region` from Workspace to Environment
- **Conversation context:** Renamed `workspace` to `context_workspace`, added `context_repository` for UI-driven context selection
- **Default workspace signal:** Auto-creates "Default" workspace when Organization is created

### Rationale

Workspace was overloaded — it coupled governance (apps, policies) with infrastructure (AWS account/region) and source (repo URL). Splitting these concerns enables:
- Multiple repos per workspace (or multiple workspaces sharing repos)
- Apps in different repos deployed to the same governance container
- Future GitHub/GitLab integration as a separate concern

### aws_region Bug Fix

After refactor, `create_environment` wasn't setting `aws_region`, causing `sts..amazonaws.com` errors (empty region). Fixed by:
- Adding `aws_region` parameter to `create_environment` tool (defaults to "us-east-1")
- Updated tool description to instruct agent: confirm region with user before provisioning, since it can't be changed later


## 2026-01-23 00:30 - Add --yes flag to npx cdk command

Added `--yes` flag to the `npx cdk deploy` command in `cdk_utils.py`. Without this flag, npx prompts for confirmation when it needs to install the CDK CLI package, which blocks non-interactive deployments.


## 2026-01-22 04:50 - Convert deploy.py to Django Management Command

Converted the standalone `deploy.py` script to a Django management command `doh_deploy`. The script required manual `.env` path resolution and hardcoded AWS account IDs — both problems solved by leveraging Django's infrastructure.

### Why Management Command

- **`.env` loading** — Django settings already loads `.env` at startup, no path gymnastics needed
- **Database access** — Can look up AWS account by name from the database instead of hardcoding `TARGET_ACCOUNT_ID` and `TARGET_EXTERNAL_ID`
- **Consistent UX** — `python manage.py doh_deploy` fits the Django workflow

### Usage

```bash
python manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox"
python manage.py doh_deploy --app simple-dashboard --account "Humanity Rules Sandbox" --teardown
python manage.py doh_deploy --base --account "Humanity Rules Sandbox" --env prod
```

### Changes

- **Added** `devopshero_app/management/commands/doh_deploy.py`
- **Deleted** `devopshero_app/services/infra_customer/deploy.py`
- **Simplified** `iam_utils.py` — Removed `load_credentials_from_env()`, now only contains `get_assumed_role_session()`


## 2026-01-22 03:45 - Async Environment Provisioning

Made `create_environment` non-blocking. Previously it waited 5-10 minutes for CloudFormation to complete, causing HTTP timeouts and poor UX. Now it returns immediately with PENDING status, and a background worker handles provisioning.

### Architecture

- **Job Worker Pattern** — Renamed `deployment_worker` → `job_worker` to handle multiple job types (deployments and environment provisioning)
- **EnvironmentLog Model** — Added parallel to `DeploymentLog` for tracking provisioning progress
- **Polling via `get_environment_status`** — New MCP tool for agents to check provisioning progress

### Flow

1. Agent calls `create_environment` → Creates `Environment` with `PENDING` status, returns immediately
2. Job worker claims it → Sets `PROVISIONING`, spawns thread
3. `environment_executor` runs CDK → Provisions VPC, ECS cluster, shared ALB
4. Updates to `READY` or `ERROR`
5. Agent polls with `get_environment_status` until ready

### Renamed Files

- `deployment_worker.py` → `job_worker.py`
- `deployment_logging.py` → `job_logging.py`
- `run_deployment_worker.py` → `run_job_worker.py`
- `DOH_RUN_DEPLOYMENT_WORKER` → `DOH_RUN_JOB_WORKER`

### New Files

- `environment_executor.py` — Runs provisioning (parallel to `deployment_executor.py`)
- `get_environment_status.py` — MCP tool to poll progress
- `EnvironmentLog` model + migration

### System Prompt Updates

Updated agent instructions to explain the async flow: check environments with `list_environments`, create with `create_environment`, poll with `get_environment_status` until READY, then proceed with deployment.


## 2026-01-22 02:30 - Simplified ALB Architecture: Removed Dedicated ALB + domain_name

Removed the option for per-app dedicated ALBs and the `domain_name` field from the App model. All apps now use the shared ALB exclusively.

### What Changed

- **Removed `domain_name` from App model** — Apps no longer have custom domain configuration. The URL is derived automatically as `{app_slug}.{environment.shared_alb_hosted_zone}`.

- **Removed dedicated ALB mode** — Previously `deploy_app.py` supported two modes: shared ALB (fast, uses host-based routing) and dedicated ALB (slow, creates per-app infrastructure). Removed the dedicated mode entirely.

- **Simplified `deploy_app.py`** — Renamed `AppWithAlbStack` → `AppStack`, removed `_setup_dedicated_alb()` method, removed `use_shared_alb` and `hosted_zone_id` parameters, removed unused ACM/Route53 imports.

### Why Simplify Now

The dedicated ALB mode was future-proofing for "full isolation" scenarios, but:
1. It significantly complicated the codebase with conditional paths
2. Shared ALB with host-based routing handles our current use cases well
3. If we need per-app ALBs later, we can reimplement with better clarity on actual requirements

### Domain Resolution

With this change, app domains are fully determined by the environment:
- Environment has `shared_alb_hosted_zone` (e.g., `dev.example.com`)
- App gets domain `{app_slug}.{shared_alb_hosted_zone}` (e.g., `my-app.dev.example.com`)
- No per-app domain configuration needed

This also resolves the earlier architectural concern about `domain_name` not fitting multi-environment scenarios (prod vs staging would need different domains per app).

### Files Changed

- `models.py` — Removed `domain_name` field
- `appconfig.py` — Removed `domain_name` from `AppConfig` dataclass
- `app_config_builder.py` — Removed `domain_name` from config construction
- `deploy_app.py` — Major cleanup: removed dedicated ALB, simplified to shared-only
- `deployment_executor.py` — Removed `use_shared_alb` parameter
- `example_apps.py` — Removed `domain_name` from example configs
- `create_app.py` / `mcp_tools.py` — Removed `domain_name` from tool
- `admin.py` — Removed from search fields
- Migration `0019_remove_app_domain_name.py` created


## 2026-01-21 21:10 - App Slug Uniqueness: Global → Per Organization

Changed `App.slug` from globally unique to unique per organization to fix a multi-tenant information leakage issue.

### The Problem

When generating app slugs, the system appends incrementing numbers if the slug already exists (e.g., `my-app` → `my-app-1`). With global uniqueness, this leaks information across tenants: if Tenant B tries to create "analytics-dashboard" and gets "analytics-dashboard-1", they learn that some other tenant already has "analytics-dashboard".

### Decision: Unique Per Organization

Considered three scopes:

- **Per Workspace** — Strongest isolation, but requires workspace slug in AWS resource names (longer, repetitive)
- **Per Organization** — Good isolation (orgs are tenant boundaries), keeps resource names short
- **Global** (previous) — Simplest AWS naming, but cross-tenant leakage

Chose **per organization** because:
1. Organizations are the tenant boundary — no cross-tenant leakage
2. AWS resource names stay short: `doh/{env.slug}/{app.slug}` still works since environments are per-account, accounts are per-org
3. Same-org collisions are acceptable (users probably want to know if a colleague already created that app name)

### Implementation: Denormalized FK

The constraint `unique_together = [["organization", "slug"]]` requires a direct FK from App to Organization. Since App → Workspace → Organization, we added a denormalized `organization` FK to App:

```python
class App(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="apps",
        help_text="Denormalized from workspace for unique constraint",
    )
    workspace = models.ForeignKey(...)
    slug = models.SlugField(max_length=255)  # No longer unique=True

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                name="unique_app_slug_per_org",
            )
        ]
```

The `create_app` tool sets `organization=workspace.organization` and generates slugs with `App.objects.filter(organization=org, slug=slug)`.

### Async Gotcha: select_related for Organization

In `mcp_tools._get_workspace()`, added `select_related("organization")` so the organization is eagerly loaded when fetching the workspace. Without this, accessing `workspace.organization` in async code would require `sync_to_async` since Django lazy-loads related objects synchronously. Pre-fetching avoids this friction.

### Why Not Application-Level Validation Only?

Could skip the denormalized FK and just check uniqueness in `create_app.py`. Rejected because:
- Race conditions without DB-level constraint
- Other code paths (admin, future APIs) would need duplicate validation
- DB constraint is authoritative


## 2026-01-21 - ECS Exec command reference

To shell into a running ECS container for debugging:

```bash
aws ecs execute-command --cluster <cluster-name> \
    --task <task-id> --container <app-name> \
    --interactive --command /bin/sh
```

Removed this from deployment output logs since it cluttered the UI. Keep here for reference.

## 2026-01-21 07:25 - Deployment logging source + streaming output

- Replaced deployment log phase with a source field, added structured template/params logging, and capture CDK/Docker stdout/stderr into DeploymentLog. Added stderr downgrade logic for CDK output and hardened log formatting to avoid formatting exceptions.

## 2026-01-21 - Reduce ECS target group deregistration delay

Set ALB target group `deregistration_delay` to 10 seconds (default is 300). ECS service removal was taking minutes in "Draining" state while waiting for ALB connection draining. Marked as TODO for production where longer graceful draining may be needed.

## 2026-01-20 14:42 - Chat auto-scroll behavior

- Updated chat auto-scroll to unlock on user-initiated scrolling while reattaching immediately when the scrollbar reaches the bottom during streaming.

## 2026-01-20 22:03 - Secrets Manager CLI tools

Added CLI commands to `secrets_utils.py` for managing secrets in customer AWS accounts:
- `--list` — List all secrets (with optional `--include-deleted` for those in retention)
- `--purge-deleted` — Permanently delete secrets scheduled for deletion (bypasses retention period)
- `--account` — Explicit account selection by name or ID (defaults to 266117665083)

Core functions (`list_secrets`, `purge_deleted_secrets`) take a boto3 session parameter for reuse. The CLI handles Django setup and role assumption via `iam_utils`.

## 2026-01-20 14:09 - Fix markdown underscore rendering

- Switched chat markdown rendering to marked.js for both stored and streaming paths so intraword underscores display correctly.
- Updated streaming to buffer by line and re-render on newline/flush using the same renderer for a single codepath.

## 2026-01-20 - App Secrets Support in Agent Flow

Fixed `AccessDeniedException` when apps tried to read secrets from AWS Secrets Manager. The `app_secrets` field was defined in `AppConfig` but never populated through the agent flow.

### Root Cause

The `create_app` agent tool and Django `App` model didn't have an `app_secrets` field. When `app_config_builder.build_app_config()` built the config, `app_secrets` defaulted to `None`. This caused the CDK to skip adding the IAM policy for `secretsmanager:GetSecretValue`.

### Fix

Added `app_secrets` support through the full chain:
- Django `App` model — new JSONField for storing secret configuration
- `create_app` tool — new parameter with normalizer for LLM input quirks
- MCP tool schema — exposed parameter to agent
- `app_config_builder` — passes `app.app_secrets` to `AppConfig`
- System prompt — guidance for agent on detecting and configuring secrets

### Detection Pattern

The agent looks for Secrets Manager access patterns during repository analysis (GetSecretValue calls, config providers, `devopshero/{app}/secrets` paths) and populates `app_secrets` accordingly.

## 2026-01-20 - Moved infra_customer Inside Django App

Moved `infra_customer/` from project root to `devopshero_app/services/infra_customer/`. This eliminates the `sys.path.insert()` hack that was documented in the previous "sys.path Manipulation: Why It's Needed" entry.

The infrastructure code is now a proper Python package importable as `from devopshero_app.services import infra_customer`. Internal imports within the package use relative imports (`from . import deploy_app`).

## 2026-01-20 - Improved Tool Display Titles for Grep/Glob

Enhanced agent chat UI to show more context in tool call titles. Grep and Glob now display both the search pattern and target path (e.g., "Grep — foo.*bar in utils.py"). Also added special handling for Task tool to derive display name from sub-agent type.

## 2026-01-20 - Route53 Domain Discovery Agent Tool

Added `list_hosted_zones` agent tool to discover available Route53 domains in customer AWS accounts. This enables the agent to configure custom domains during deployment conversations.

### Design Decision: Agent-First Approach

Originally considered adding domain discovery to the AWS account connection UI flow with model changes (adding `default_domain` to AWSAccount, `domain` to Workspace). Instead, chose to let the agent handle domain discovery during conversation:

- **No model changes** — domain stays on App where it already is
- **No UI changes** — agent discovers domains on-demand via tool
- **Flexible** — agent can ask contextual questions about which domain to use
- **Fits the product vision** — "AI co-pilot" handles the complexity

### Agent Guidance

Updated system prompt with domain configuration rules:
- Single domain found → auto-select as default (e.g., `myapp.example.com`)
- Multiple domains → ask user which to use
- No domains → deploy with ALB DNS only

Also added requirement for deployment confirmation before proceeding, showing domain and other config clearly.

### Private Zones: Deferred

Filtering to public zones only for now. Private zones require either:
- Internal ALB (not implemented yet)
- Wildcard certificates already validated (workaround)
- Private CA support

Created bead `devopshero-3yg` for wildcard certificate reuse, which would also unlock private zone support.

## 2026-01-20 - Globally Unique App Names

Discovered a naming mismatch: ECS services were created with `{env_slug}-{app_name}` but started with just `app_name`. Investigation revealed deeper issues with the resource naming strategy.

### The Problem

The `resource_prefix` pattern was `devopshero-{env_slug}-{workspace_slug}-{app_name}`, producing names like `devopshero-default-acme-corp-simple-dashboard` (45+ chars). This caused several problems:

- **ALB/TG limits**: AWS limits these to 32 characters, forcing truncation that could cause collisions
- **Inconsistent naming**: Some resources used full prefix, others used shorter variants
- **Workspace collision risk**: The domain model says "multiple workspaces can deploy to the same environment" — if two workspaces had apps with the same name, they'd collide on resources that didn't include workspace_slug

### The Decision: Globally Unique App Names

Adopted the same constraint as Heroku, Render, and Railway: **app slugs must be globally unique across all workspaces**. This simplifies everything:

- **New resource prefix**: `doh-{env_slug}-{app_slug}` (e.g., `doh-default-simple-dashboard` = 26 chars)
- **New ECR path**: `doh/{env_slug}/{app_slug}`
- **Removed workspace_slug**: No longer needed in any resource naming

The `env_slug` remains because the same app can deploy to multiple environments (dev/staging/prod), each needing separate AWS resources.

### Why "doh" Instead of "devopshero"

"DevOps Hero" abbreviates to "DOH" (already in AGENTS.md). Using `doh-` instead of `devopshero-` saves 7 characters per resource name, keeping us well under AWS limits.

### Implementation

1. **Model**: Changed `App.slug` from `unique_together = [["workspace", "slug"]]` to `unique=True`
2. **Validation**: `create_app` now checks global uniqueness, not workspace-scoped
3. **Infrastructure**: Removed `workspace_slug` parameter from `deploy()` and `teardown()`
4. **CLI**: Removed `--workspace` argument from `deploy.py`
5. **AWS Resources**: Changed app-specific resource naming from `devopshero-*` to `doh-*`:
   - ALB, target groups, ECS services, task roles
   
**Not changed**:
- Base infrastructure (VPC, cluster, task execution role, log groups) — looks more aesthetic with full "devopshero", but decision might change in the future.
- Secrets Manager paths (`devopshero/{app}/...`)
- Cross-account AssumeRole name (`devopshero-{external_id}`) — installed in customer accounts via CloudFormation

### Naming Convention Decision: app_name vs app_slug in AppConfig

The `AppConfig` dataclass (infra layer) uses `app_name` but receives `app.slug` from Django. Considered renaming to `app_slug` for consistency but decided against it:

- **Bounded context translation**: Django uses "slug" as the canonical identifier; infrastructure uses "name" when creating AWS resources. The `app_config_builder` translates between these contexts.
- **Natural infra terminology**: `container_name=app_config.app_name` reads better than `container_name=app_config.app_slug`
- **Semantic accuracy**: The slug IS the name used for resources — the comment "used in resource names" is correct

This is acceptable translation between bounded contexts, not a naming inconsistency.

## 2026-01-20 - CDK Tokens: Runtime vs Synthesis Values

Hit an issue where `cluster.cluster_name` returned `${Token[TOKEN.42]}` instead of the actual cluster name when calling AWS SDK at runtime. The ECS service start failed with "Cluster not found."

**The problem:** When importing resources with `Fn.import_value()`, CDK returns tokens — placeholders resolved by CloudFormation during deployment, not by Python at runtime. The `ecs.Cluster.from_cluster_attributes()` call receives a token, and accessing `.cluster_name` just gives you that token back.

**Key insight:** CDK tokens are fundamentally unresolvable at Python runtime. There's no `Token.resolve()` method — that's by design. But you don't need `Fn.import_value` for values you already know.

**The fix:** Use a literal string for `cluster_name` in `from_cluster_attributes()` instead of `Fn.import_value()`:

```python
# Before (returns token)
cluster = ecs.Cluster.from_cluster_attributes(
    scope, "ImportedCluster",
    cluster_name=Fn.import_value(f"{prefix}-cluster-name"),  # Token!
    vpc=vpc,
)

# After (returns actual string)
cluster = ecs.Cluster.from_cluster_attributes(
    scope, "ImportedCluster",
    cluster_name=f"{prefix}-cluster",  # Literal string
    vpc=vpc,
)
```

Now `cluster.cluster_name` returns `"devopshero-default-cluster"` instead of `"${Token[TOKEN.42]}"`.

**Why this works:** The cluster name is deterministic (we define it), unlike VPC/subnet IDs which are AWS-generated. VPC attributes still need `Fn.import_value`, but cluster name doesn't. No separate `cluster_name: str` field needed in `EnvironmentInfrastructure`.

## 2026-01-19 - CDK Stack Refactor: Expose Environment Infrastructure

Exposed `environment_infra` as instance attribute on `AuroraClusterStack` and `AppWithAlbStack`. Previously a local variable, now accessible via `stack.environment_infra` after construction. Needed for external callers to access cluster name, VPC, etc.

Also standardized ECS service naming to use `resource_prefix` consistently (`devopshero-{env}-{workspace}-{app}`) instead of the shorter `{env}-{app}` pattern. This aligns service names with other resources and avoids potential collisions across workspaces.

## 2026-01-19 - LLM Tool Input Sanitization: The String-vs-Object Trap

Deployment crashed at `deploy_app.py:340` when iterating over `environment_variables`. The Django admin showed `"{}"` stored in the JSONField. Root cause analysis revealed a subtle but important lesson about LLM tool calls.

### The Bug

The `App.environment_variables` field expects `[{"name": "FOO", "value": "bar"}, ...]`. The code had:

```python
environment_variables=environment_variables or [],
```

This handles `None` and empty containers (falsy values), but Claude sent `"environment_variables": "{}"` — a **string** containing braces, not an empty object. Non-empty strings are truthy, so `"{}" or []` returns `"{}"`, which gets saved to the database.

### Why Claude Did This

The tool schema just said `"environment_variables": list` with no format guidance. Claude interpreted "no env vars needed" as the string `"{}"` rather than an empty array `[]`. This is a common LLM pattern — they sometimes stringify values when uncertain about the expected structure.

### The Fix

Two-part solution:

1. **Better documentation** — Updated tool description to explicitly show the expected format with an example: `[{"name": "API_KEY", "value": "secret"}]`. Pass `[]` if none needed.

2. **Defensive normalization** — Added `_normalize_environment_variables()` that handles all malformed inputs:
   - `None` → `[]`
   - String `"{}"` → parsed, rejected as non-list → `[]`
   - Dict `{}` → `[]`
   - Validates each item has `name`/`value` keys

### Lesson

When accepting structured data from LLMs, don't trust type hints alone. LLMs can send strings that look like the right type but aren't. Always validate and normalize inputs, especially for nested structures like `list[dict]`. The `x or default` pattern only catches falsy values — it won't save you from a truthy string that happens to contain JSON-like text.

## 2026-01-18 - Deployment Bridge: Closing the Loop from Django to AWS

Long planning session to implement the "deployment bridge" — making the agent actually deploy apps to AWS. Started with a blank slate: the `infra_customer/` CDK code worked from CLI, the agent could create `App` records, but nothing connected them.

### The Core Decision: Bridge vs. Code Generation

The design doc had two interpretations:

- **Option A: Agent generates CDK code** — Maximum flexibility, agent writes Python, system validates via `cdk synth`, errors fed back for retry. Requires sandbox execution.
- **Option B: Agent populates config, fixed CDK deploys** — Keep existing CDK stacks, agent transforms `RepoAnalysisOutput` → `AppConfig`. Safer, faster to ship.

**Decision: Option B (Bridge).** The existing CDK stacks work. The agent's job is configuration, not code generation. We can always add code generation later if patterns emerge that don't fit the templates.

### Worker Design: Polling Thread vs Asyncio

Considered in-process asyncio (`create_task` fire-and-forget) vs polling thread.

**Why polling thread wins:**

- **Natural decoupling** — The `deploy_app` tool just creates a DB record. Worker is a separate concern. If web process restarts between tool call and deployment starting, nothing is lost.
- **Blocking CDK code stays blocking** — No need to wrap `deploy_app.deploy()` in `run_in_executor`. Just call it.
- **Recovery** — On startup, worker can find deployments stuck in `BUILDING` (from a crash) and mark them failed.
- **Testable in isolation** — Run worker standalone, point it at DB, test without web layer.
- **1-second latency is meaningless** — Deployments take minutes. Who cares about 1 second?

The asyncio approach would require `run_in_executor` for CPU-bound CDK operations and has no recovery if the process dies mid-deployment.

**Migration cost is minimal** — When we add Celery/Django-Q later, only `worker.py` changes. The `run_deployment()` function stays identical.

### sys.path Manipulation: Why It's Needed

The `infra_customer/` directory is a **sibling** of `devopshero_app/`, not a child:

```
devopshero/
├── devopshero_app/          ← Django app, in INSTALLED_APPS, on sys.path
│   └── services/deployment/ ← Needs to import from infra_customer
├── infra_customer/          ← NOT a Django app, NOT on sys.path
│   ├── deploy_app.py
│   └── appconfig.py
```

Django puts the project root on `sys.path`, making `devopshero_app` and its children importable. But `infra_customer` is a sibling — Python doesn't search sibling directories. Adding `__init__.py` to `infra_customer` doesn't help because the directory itself isn't discoverable.

The `sys.path.insert(0, infra_customer_path)` hack makes it work. Alternatives (documented in code):
1. Make `infra_customer` a proper package (`pyproject.toml` + `uv pip install -e`)
2. Move `infra_customer` inside `devopshero_app`

For now, the hack is contained in one place and works. Revisit if it causes problems.

### The Bridge Architecture

Three-layer design:

```
Django Models (App, Workspace, Environment, Datastore)
        ↓
app_config_builder.py → appconfig.AppConfig
        ↓
deploy_app.py / deploy_base.py → CDK Stacks → CloudFormation
```

- **`app_config_builder.py`** — Converts Django `App` to `appconfig.AppConfig`. Builds ECR repo name with full context: `devopshero/{env}/{workspace}/{app}`. This naming ensures isolation.
- **`deployment_executor.py`** — Orchestrates the full deployment: get AWS session via AssumeRole, provision environment if PENDING, build AppConfig, call CDK, create logs.
- **`deployment_worker.py`** — Polling loop with atomic claiming via `select_for_update(skip_locked=True)`.

### Log Callback for Real-Time Progress

Added `log_callback: Callable[[str, str, str], None]` to CDK functions. The executor passes a callback that creates `DeploymentLog` entries:

```python
def log_callback(phase, level, message):
    DeploymentLog.objects.create(deployment=deployment, phase=phase, level=level, message=message)
```

Important while developing — we need to see what's happening. The agent can poll `get_deployment_status` to report progress to users.

### Environment Infrastructure Import Centralization

Created `EnvironmentInfrastructure` dataclass and `import_environment_infrastructure()` helper in `deploy_base.py`.

**Why centralize but each stack still imports?** CDK constraint: `Fn.import_value()` creates constructs scoped to a Stack. You can't import in Stack A and pass the construct to Stack B — they're separate CloudFormation templates. The helper centralizes the *logic*; each stack still calls it.

Named it `environment_infra` (not `env_infra`) and used directly (`environment_infra.vpc`) without extracting to local variables. Explicit is better.

### Lazy Environment Provisioning — The Key Insight

The flow handles base infrastructure provisioning correctly:

1. AWS account connects → API callback creates Environment in `PENDING` state
2. User deploys app → Creates Deployment in `PENDING` state
3. Worker picks up deployment
4. `run_deployment()` checks if environment is `PENDING` → provisions base infra first
5. Only then proceeds to `deploy_app.deploy()`

**Why this works:** `Fn.import_value()` is a CloudFormation intrinsic function. During CDK synthesis, it doesn't validate exports exist — it just generates `Fn::ImportValue` JSON. Resolution happens at CloudFormation deploy time. So base stacks can be deployed first, creating exports, then app stacks deploy and resolve the imports.

### Conversation-Deployment: FK → M2M

Changed from `Deployment.conversation` (FK) to `Conversation.deployments` (M2M).

**Why M2M?**
- Deployment becomes a cleaner domain entity, not coupled to chat
- Semantically, conversation "owns" the relationship — "this conversation triggered these deployments"
- More flexible if deployment is referenced from multiple conversations later

**Why not ArrayField/JSONField?** Denormalized, no referential integrity. M2M creates a proper join table with FKs and indexes.

After creating deployment, `mcp_tools.py` links it: `await conversation.deployments.aadd(deployment)`

### Environment Slug: Required, Not Optional

Made `environment_slug` required. Tool description tells LLM: "For environment_slug, always use 'default'."

**Why:** YAGNI. We only have one environment. Optional parameter + fallback code for unused feature = unnecessary complexity. When we support multiple environments, update the tool and let LLM ask users.

### Stack Naming Convention

All resource names now include environment and workspace:

- **Base stacks:** `devopshero-{env}-vpc`, `devopshero-{env}-cluster`
- **App stacks:** `devopshero-{env}-{workspace}-{app}-ecr`, etc.
- **ECR repos:** `devopshero/{env}/{workspace}/{app}`

Prevents collisions when multiple workspaces deploy apps with the same slug.

### Worker Startup

Moved `RUN_DEPLOYMENT_WORKER` check to `settings.py` as `DOH_RUN_DEPLOYMENT_WORKER`. Django pattern: env → settings → code.

Import must be inside `ready()` because `deployment_worker` imports models, and models aren't ready at module load time. Django's `AppConfig.ready()` runs after all apps are loaded.

### Account-Scoped Environments (not Workspace-Scoped)

Initially considered Workspace-scoped environments (Workspace has many Environments). Changed the model during planning:

**Problems with workspace-scoped:**
- N workspaces × M environments = N×M VPCs and clusters (expensive, fragmented)
- "prod" would mean different things for each workspace

**Account-scoped is better:**
- "prod" means something org-wide — same network, same security posture, same compliance boundary
- Cost efficiency — shared VPC and cluster
- Networking — apps in same environment can communicate (same VPC)
- Simpler mental model — "deploy to prod" vs "deploy to workspace-X's prod"

**Implementation:** Environment has `aws_account` FK. Deployment references both `app` (→ Workspace) and `environment` (→ AWSAccount). See `docs/domain_model.md`.

---

## 2026-01-16 - Refactored AuroraClusterStack to Use Fn.importValue

Changed how AuroraClusterStack references the VPC from the base infrastructure.

**Problem:** The previous approach instantiated `VpcStack` inside the app deployment, which risked accidentally creating/updating the VPC when deploying an app. The comment said "it won't be deployed (already exists)" but CDK's `--all` flag would deploy it if it didn't exist.

**Initial fix:** Used `Vpc.from_lookup()` which does AWS API calls at synth time. This worked but created an extra `devopshero-vpc-lookup` CloudFormation stack and required `cdk.context.json` caching.

**Final solution:** Use CloudFormation's native `Fn.importValue` to reference exports from the VPC stack:
- Added AZ exports (`devopshero-az-1`, `devopshero-az-2`) to VpcStack in `deploy_base.py`
- AuroraClusterStack now imports VPC internally using `Vpc.from_vpc_attributes()` with `Fn.import_value()`
- Removed `vpc` and `default_security_group` parameters from AuroraClusterStack constructor

**Why this is better:**
- No extra CloudFormation stack
- No synth-time API calls
- Pure CloudFormation cross-stack references (battle-tested pattern)
- AuroraClusterStack is self-contained

---

## 2026-01-16 - Collapsible Tool Messages in Chat UI

Made tool call messages collapsible using native HTML `<details>`/`<summary>` elements.

**Why:** Tool messages show parameters and results which can be verbose. Collapsing them reduces visual clutter while keeping the info accessible.

**Implementation:**
- Used `<details>` with Tailwind's `group` class for state-based styling
- Chevron arrow rotates via `group-open:rotate-90` with smooth transition
- Hidden default marker with `list-none [&::-webkit-details-marker]:hidden`
- Tool start (spinner) opens expanded; completed tools collapse by default


---

## 2026-01-16 - CLI Test Harness for Main Agent

Built a CLI harness (`test_main_agent.py`) for fast agent iteration without the web UI.

**Why:** Going through the web page for every agent test is slow. Need to iterate quickly on prompts, resume from specific points, and branch conversations.

**Key Features:**
- **Session fork/resume:** Uses Claude Agent SDK's session management. `--conversation-id <uuid>` with `--fork` (default) branches the session; `--no-fork` resumes in place.
- **DB snapshotting:** Copies `db.sqlite3` to `test_db/` by default, so experiments don't pollute the main DB. Use `--no-copy` to persist state across runs.
- **REPL mode:** `--repl` for interactive back-and-forth.

**Design Decision — Parameter Clarity:** Initially had `resume_session_id` as a separate parameter flowing through `stream_response`. Refactored to use `conversation.session_id` as a carrier (set temporarily for fork scenarios). This eliminated redundant parameters:
- `stream_response(conversation, fork_session)` — uses `conversation.session_id` internally
- Fork case: new conversation gets source's `session_id` assigned (not saved) before calling `stream_response`

**Learning:** When forking, you create a NEW conversation but resume from the SOURCE's session. The conversation object can carry the session_id temporarily without persisting it — the agent will assign the new forked session_id after the response.

---

## 2026-01-16 - Fixed Spurious Error Logs for Non-Tool Blocks in Agent Service

Removed misleading error logs that fired when Claude responded with text only (no tool calls).

**The Issue:** `_handle_assistant_message` and `_handle_tool_results` in `agent_service.py` were logging errors when encountering `TextBlock` or non-`ToolResultBlock` content. These logs made it seem like something was wrong, but this is actually expected behavior.

**Why It's Expected:**
- Text is streamed via `SDKStreamEvent` objects during the response
- After streaming completes, an `AssistantMessage` arrives containing the full message (including `TextBlock`s)
- The `TextBlock` in `AssistantMessage` is redundant — text was already processed during streaming
- Similarly, `UserMessage` can contain non-tool-result blocks in certain SDK scenarios

**The Fix:** Replaced error logs with silent skips and explanatory comments:

```python
# In _handle_assistant_message:
if not isinstance(block, ToolUseBlock):
    # TextBlocks are expected here when Claude responds with text only.
    # The text has already been streamed via SDKStreamEvent, so we skip it.
    continue

# In _handle_tool_results:
if not isinstance(block, ToolResultBlock):
    # Non-ToolResultBlock content (e.g., TextBlock) can appear in synthetic
    # UserMessages from the SDK. These are informational and can be skipped.
    continue
```

**Learning:** When working with streaming SDKs, the final "complete" message often contains content that was already processed incrementally. Don't treat this as an error — it's just the SDK providing the assembled result.

---

## 2026-01-15 - Stable Message Input During Conversation Switch

Fixed the message input flickering when switching between conversations. Previously, the entire chat panel content was swapped via HTMX, causing the input field to disappear and reappear.

**Solution:** Used CSS View Transitions with named transition groups. Elements with the same `view-transition-name` on both sides of a transition morph smoothly instead of fading out/in with the rest.

**Changes:**
- `chat.html`: Added `transition:true` to the HTMX swap (`hx-swap="innerHTML transition:true"`)
- `_chat_panel.html`: Added `view-transition-name: chat-panel-content;` to the main container
- `_chat_panel.html`: Added `view-transition-name: message-input;` to the message input div
- `styles.css`: Added view transition animations (fast fade-out, slower fade-in)

**Bonus:** Changed scroll behavior from instant (`scrollToBottom`) to smooth (`scrollToBottomSmooth`) during SSE streaming events for a more polished feel.

**Why this works:** View Transitions match elements by their `view-transition-name`, not content. Even though the form's `hx-post` URL changes between conversations, the input appears stable because both old and new DOM have an element with `view-transition-name: message-input`.

---

## 2026-01-15 - Tool Call Parameters Visible During Execution

Enhanced tool call display to show parameters immediately when a tool starts executing, rather than waiting for completion. Also added consistent max-height constraints with scroll.

**Changes:**
- `_streaming_tool_start.html`: Now displays parameters section (was only showing tool name/spinner)
- `chat.py`: `_render_tool_start()` now passes `params_json` to the template
- All tool templates: Added max-height with overflow scroll to prevent long outputs from dominating the chat
  - Parameters: `max-h-96` (384px)
  - Results: `max-h-[32rem]` (512px)

**Files updated:** `_streaming_tool_start.html`, `_streaming_tool_result.html`, `_message_tool_call.html`, `chat.py`

---

## 2026-01-15 - Dynamic Conversation Title on Workspace Selection

When the agent selects a workspace, the conversation title now updates to "Working on {workspace_name}" in real-time.

**Implementation:**
- `select_workspace.py`: Sets `conversation.title` when pinning workspace
- `chat.py`: Added `_render_title_oob_swap()` to generate OOB swap HTML for title update when `select_workspace` succeeds
- `_chat_panel.html`: Added `id="conversation-title"` to header, plus `handleToolResult()` JS handler to process OOB swaps from tool results
- `chat.html`: Added `id="sidebar-title-{conv.id}"` to sidebar entries, plus `updateSidebarTitle()` JS to sync sidebar when main title changes

**Bug fixes:**
- `_enrich_tool_input()` now catches all exceptions (not just DoesNotExist) to handle invalid UUIDs gracefully
- Added system prompt guidance explaining that users refer to resources by name, and the agent should detect name vs UUID and look up UUIDs via list tools

---

## 2026-01-15 - Tool UI Improvements

Enhanced tool call display with DB-backed name resolution and consistent width.

**Name enrichment:** Added `_enrich_tool_input()` in `agent_service.py` to look up friendly names from the database before rendering. When `deploy_app` is called with `app_id`, we fetch the app name; same for `select_workspace` with `workspace_id`. This ensures the UI shows "Deploy App: my-cool-app" instead of a UUID.

**External tools:** Added `TOOL_MAIN_PARAMS` entries for Claude Agent SDK tools (Read → file_path, Shell/Bash → description).

**Width fix:** Tool call boxes weren't expanding to full width. Added `w-full` to the flex container so it expands before `max-w-[70%]` caps it. Applied to both streaming and non-streaming templates.

---

## 2026-01-15 - Tool Title Main Parameter Display

Enhanced tool call rendering to show the "main parameter" in the title for better scannability. For example, "Create Workspace: django-postgres-app" instead of just "Create Workspace".

**Implementation:**
- Added `TOOL_MAIN_PARAMS` mapping in `mcp_tools.py` linking tools to their primary parameter
- Added `get_tool_main_param()` to extract and format values (handles file URLs, UUIDs, seconds)
- Updated streaming templates and `chat.py` to pass both `tool_name` and `tool_main_param`
- Added `tool_display_name` and `tool_main_param` template filters for page-refresh rendering
- Main param rendered with lighter styling (`font-normal text-gray-500`) for visual hierarchy

**Tools with main params:** create_workspace (name), initiate_aws_connection (account_name), scan_repository (repo_url), create_app (name), create_datastore (name), wait (seconds)

**Tools without:** list_* tools (no params), select_workspace/deploy_app/get_deployment_status (only have UUIDs)

---

## 2026-01-15 - Replace whitenoise with servestatic

Fixed the Django ASGI warning about synchronous iterators in streaming responses.

**The Warning:**
```
StreamingHttpResponse must consume synchronous iterators in order to serve them asynchronously. Use an asynchronous iterator instead.
```

**Root Cause:** Not our SSE streaming code (which correctly uses async generators), but `whitenoise` middleware serving static files. Whitenoise uses synchronous file iteration internally, which triggers this warning under ASGI (uvicorn).

**Solution:** Replaced whitenoise with `servestatic`, an ASGI-native fork created specifically to address this issue. It's a drop-in replacement with identical configuration.

**Changes:**
- `pyproject.toml`: `whitenoise>=6.11.0` → `servestatic>=3.0.0`
- `settings.py`: `whitenoise.middleware.WhiteNoiseMiddleware` → `servestatic.middleware.ServeStaticMiddleware`

---

## 2026-01-15 - Friendly Tool Display Names

Added human-readable display names for MCP tools in the chat UI. Previously, tool calls showed the full MCP-namespaced names like `mcp__devopshero__list_aws_accounts`, which is an implementation detail users don't need to see.

**Solution:** Created a `TOOL_DISPLAY_NAMES` mapping in `mcp_tools.py` that maps full MCP names to properly capitalized labels (e.g., "List AWS Accounts", "Deploy App"). The `get_tool_display_name()` function performs the lookup and falls back to the full name for unknown tools.

**Why a mapping instead of string manipulation:** Using `removeprefix()` would just give `list_aws_accounts`, which is still technical. An explicit mapping allows proper capitalization and the flexibility to choose better names (e.g., "Initiate AWS Connection" vs "initiate_aws_connection").

---

## 2026-01-15 - Workspace Pinning and Repository Model Simplification

Redesigned the agent's workspace and repository handling to provide a cleaner mental model and prevent cross-workspace errors.

### The Problem

The existing design had several issues:
- Both `Workspace` and `App` had `repo_url` fields, creating ambiguity about which was the source of truth
- Tools like `create_app` required explicit `workspace_id` parameters, which could lead to mismatches
- No mechanism to "lock" a conversation to a workspace, risking accidental cross-workspace operations
- Two repository analysis tools (`inspect_repository` and the `repo-analyzer` sub-agent) with unclear differentiation

### Design Decisions

**One Workspace = One Repository = One App (v1)**
- Simplified the model: a workspace binds exactly one repository to an AWS account/region
- Removed `App.repo_url` — apps inherit from `workspace.primary_repo_url`
- Made `Workspace.primary_repo_url` required
- Future: add `repo_path` field for monorepo support

**Workspace Pinning**
- Once a workspace is selected for a conversation, it's immutable
- New `select_workspace` tool pins the workspace to `Conversation.workspace`
- Workspace-scoped tools (`create_app`, `create_datastore`) get workspace from conversation context, not parameters
- Platform tools (AWS connection, workspace creation) remain available in any conversation

**Repository Analysis Clarification**
- Renamed `inspect_repository` → `scan_repository` (quick, pattern-based)
- Renamed `repo-analyzer` sub-agent → `analyze-repository` (deep, LLM-powered)
- System prompt only mentions `analyze-repository`, biasing the agent toward thorough analysis
- Agent should analyze repository BEFORE creating workspace, to inform naming and configuration

### New Tools

- **`initiate_aws_connection`** — Creates pending AWS account, returns CloudFormation URL
- **`select_workspace`** — Pins workspace to conversation (fails if already pinned)
- **`list_workspaces`** — Lists all workspaces in organization

### Tool Changes

- **`create_app`** — Removed `workspace_id` and `repo_url` params; gets workspace from conversation
- **`create_datastore`** — Removed `workspace_id` param; gets workspace from conversation
- **`create_workspace`** — Now requires `primary_repo_url`, validates it's a `file://` URL

### Deployment Flow

The agent now follows this sequence:
1. `list_deployable_repos` — Show available repos
2. User selects a repo
3. `analyze-repository` sub-agent — Deep analysis before any decisions
4. Ask clarifying questions based on analysis
5. `list_aws_accounts` — Check connected accounts
6. `create_workspace` — Bind repo to AWS account/region
7. `select_workspace` — Pin to conversation
8. `create_app` — Configure build/runtime (no repo_url needed)
9. `create_datastore` — If analysis detected database needs
10. `deploy_app` — Initiate deployment


---

## 2026-01-14 - Repository Analysis Sub-Agent

Built an LLM-powered sub-agent that analyzes repositories to detect language, framework, service type, dependencies, and environment variables. Produces structured JSON with evidence for all claims.

**Key files created:**
- `repo_analysis/repo_analysis_schema.py` — Pydantic models for structured output (RepoAnalysisOutput, ServiceConfig, DependenciesConfig, EnvConfig, EvidenceItem)
- `repo_analysis/system_prompt.md` — Sub-agent prompt defining investigation strategy, evidence discipline, and output format
- `repo_analysis/repo_analyzer_config.py` — AgentDefinition with description, prompt, and allowed tools (Bash, Read, LS, Glob, Grep)
- `repo_analysis/test_repo_analysis.py` — CLI test harness with reference app expectations

**Integration:** Added sub-agent to main agent via `agents` parameter in `agent_service.py`. The main agent can invoke it via the SDK's native "Task" tool.

**Design decisions:**
- Uses SDK native sub-agents (not MCP tools) — cleaner invocation, automatic Task tool handling
- Sub-agent has restricted toolset (no Django models, no streaming, no clarifying questions)
- Evidence required for all major claims (file path + excerpt)
- One repo = one app (no monorepo support in v1)

**Test harness:** Validates against 8 reference apps (django_postgres_app, fastapi_app, nextjs_app, phoenix_app, etc.). Uses `query()` function for simple single-shot invocation.

```bash
uv run python -m devopshero_app.services.agent.repo_analysis.test_repo_analysis --app fastapi_app
```

## 2026-01-13 - Chat Input History

Added up arrow key support to recall the last sent message, similar to terminal/shell behavior. Press up arrow when the input is empty to restore the previous message.

## 2026-01-13 - Streaming Markdown Rendering

Added real-time markdown rendering for chat messages using the `streaming-markdown` library (12KB, CDN).

**How it works:**
- **During streaming:** Text chunks are fed to `parser_write()` which renders markdown incrementally with append-only DOM updates
- **On page reload:** Messages stored as `ContentType.MARKDOWN` are rendered client-side using the same library for consistency

**Key files:**
- `chat_view.html` — Imports streaming-markdown, handles SSE events, renders stored markdown on load
- `_message_markdown.html` — Outputs raw markdown in `<script type="text/markdown">` for client-side rendering
- `styles.css` — Custom `.markdown-content` styles (headers, lists, code blocks, etc.) since `@tailwindcss/typography` not installed
- `agent_service.py` — Messages saved as `ContentType.MARKDOWN`
- `chat.py` — Streaming container uses `<div>` with `markdown-content` class

**Why not server-side rendering:** Considered `mistune` but using the same library client-side ensures identical output for streaming and page reload.

## 2026-01-13 - Fix Streaming Message Order and Tool Rendering

Fixed multiple issues with how messages appear during streaming vs after page reload.

### Problem 1: Messages Out of Order During Streaming

When the agent called a tool without producing text first, messages appeared in wrong order during streaming (text above tool box) but correct after reload (tool box above text).

**Root cause:** The initial `start` event created `#streaming-message` immediately. If the agent called a tool without text, this empty container sat above the tool box. After tool completion, a second `start` created another element with duplicate IDs. JavaScript's `getElementById` found the first (wrong) one, so text went to the container above the tool box.

**Fix:** 
- Only yield `start` when the first `text_delta` arrives (lazy creation)
- Added `has_started_streaming` flag to `StreamingContext`
- Yield `text_flush` only when there was actual streaming to flush

### Problem 2: Tool Results Not Rendered Fully

During streaming, tool boxes showed minimal status bars. After reload, they showed full details (Parameters + Result sections).

**Fix:** Updated `_render_tool_start()` and `_render_tool_result()` in `chat.py` to render the same rich HTML as `_message_tool_call.html` template, including:
- Agent avatar icon
- Parameters section with pretty-printed JSON
- Result section with pretty-printed JSON

### Problem 3: MCP Result Not Unwrapped

Tool results showed raw MCP wrapper `[{"type": "text", "text": "..."}]` during streaming but parsed content after reload.

**Fix:** Added `_extract_mcp_text_content()` helper in `chat.py` (mirrors `chat_filters.py`'s `json_pretty` logic) to unwrap MCP content blocks before display.

### Problem 4: Multiple "Thinking..." Indicators

When multiple tools ran back-to-back, empty streaming placeholders appeared between them.

**Fix:** Introduced `thinking` event type:
- Shows animated "Thinking..." indicator while waiting for agent response
- Uses dedicated `#thinking-indicator` placeholder with OOB swap (prevents duplicates)
- `start` event replaces thinking indicator when text begins
- `tool_start` event replaces thinking indicator when tool begins
- After each tool completes, thinking indicator reappears

### Problem 5: Redundant Typing Indicator

Had both "typing indicator" (shown on message send) and "thinking indicator" (shown during streaming).

**Fix:** Removed typing indicator entirely. The thinking indicator now serves as the single unified "waiting for agent" state.

---

## 2026-01-12 - Chat Streaming Architecture

Simplified the streaming architecture by eliminating the queue-based indirection. The SSE endpoint now runs the agent directly.

### Server Side

**Endpoints:**

- **`chat_send` (POST, sync)** — Creates user message in DB, returns user bubble HTML + typing indicator. Does not wait for agent.

- **`chat_stream` (GET, async)** — SSE endpoint. Runs forever in a loop:
  1. Load conversation from DB
  2. If last message is from user → run agent directly via `agent_service.stream_response()`
  3. Yield SSE events as they come from the generator
  4. Sleep 1s, send keepalive, repeat

**Agent streaming (`agent_service.stream_response`):**

An async generator that yields `StreamEvent` objects. Each event has a `type` and optional `data`:

- **`start`** — Begin new message container
- **`text_delta`** — Chunk of text to append (`{"text": "..."}`)
- **`text_flush`** — Finalize current text before tool execution
- **`tool_start`** — Tool execution beginning (`{tool_use_id, name, input}`)
- **`tool_result`** — Tool completed (`{tool_use_id, name, result, status, duration_ms}`)
- **`complete`** — Agent finished responding
- **`error`** — Something went wrong

Text accumulates in a local variable. On tool call or completion, accumulated text is persisted to DB as a Message.

### Client Side

**SSE connection** — HTMX SSE extension connects on page load:
```html
<div id="messages" hx-ext="sse"
     sse-connect="{% url 'chat_stream' ... %}"
     sse-swap="sse-start,sse-text-delta,...">
```

**Event handling** — Most events use HTMX's default swap (append HTML to `#messages`). Three events need JavaScript interception via `htmx:sseBeforeMessage`:

- **`sse-text-delta`** — Parsed as JSON, text appended to `#streaming-text` element (no DOM swap, just `textContent +=`)
- **`sse-text-flush`** — Removes cursor, clears `id` attributes so next `start` can create fresh elements
- **`sse-complete`** — Same as flush, plus removes `streaming-active` class

**UI element lifecycle:**

1. **`sse-start`** → Inserts `<div id="streaming-message">` with `<p id="streaming-text">` and blinking cursor. Also OOB-removes typing indicator.
2. **`sse-text-delta`** (repeated) → JS appends text to `#streaming-text`
3. **`sse-text-flush`** (optional, before tool) → Cursor removed, IDs cleared
4. **`sse-tool-start`** → Appends spinner HTML with `id="tool-{id}"`
5. **`sse-tool-result`** → OOB replaces `#tool-{id}` with completion status
6. **`sse-start`** (after tool) → Creates new streaming container for post-tool text
7. **`sse-complete`** → Final cleanup, cursor removed

**OOB (Out-of-Band) swaps** — Used for targeted replacements outside the main append flow:
- Typing indicator removal: `<div id="typing-indicator" hx-swap-oob="outerHTML"></div>` (replaces with empty div)
- Tool result: `<div id="tool-{id}" hx-swap-oob="outerHTML">...</div>` (replaces spinner with result)

**Key files:**
- `devopshero_app/views/chat.py` — Endpoints + SSE formatting
- `devopshero_app/services/agent/agent_service.py` — Agent generator
- `devopshero_app/templates/devopshero_app/chat/chat_view.html` — Client JS

---

## 2026-01-11 - Remove ask_user Tool and AskUserQuestion Handling

**Decision:** Removed the `ask_user` MCP tool and related `AskUserQuestion` handling to simplify the codebase before adding new features.

**What was removed:**
- `devopshero_app/services/agent/tools/ask_user.py` — The tool implementation (128 lines)
- `_convert_ask_user_question_to_choice()` in agent_service.py — Converted Claude Code's built-in AskUserQuestion to CHOICE messages
- `_extract_deferred_choice()` in agent_service.py — Extracted deferred choice data from tool results
- All special-case handling for ask_user in the message processing loop

**Why:** The ask_user tool added significant complexity:
- Required special handling to create CHOICE messages with correct ordering
- Had input normalization bugs (JSON strings vs dicts)
- Needed deferred_choice pattern to fix message ordering issues
- Created maintenance burden with two code paths for user questions (our tool + Claude's built-in)

**Current state:** The agent can no longer programmatically present interactive choice buttons to users. If the model needs user input, it must ask in natural language and wait for a text response. The CHOICE message type and `_message_choice.html` template remain in the codebase but are currently unused.

**Future consideration:** If interactive choices are needed again, consider a simpler approach or rely on Claude Code's built-in `AskUserQuestion` tool (which would appear as a TOOL_CALL card rather than custom UI).

---

## 2026-01-11 - Fix Chat Message Ordering for ask_user Tool

**Problem:** When Claude called the `ask_user` tool, messages appeared in wrong order: CHOICE buttons first, then TOOL_CALL, then TEXT. The model's past-tense response ("I've asked...") appeared before the actual question.

**Root cause:** Messages ordered by `created_at`. The `ask_user` tool created CHOICE messages during tool execution (early timestamp), while TEXT was created after stream completed (late timestamp).

**Solution:**
- Moved CHOICE creation out of `ask_user` tool — now returns `deferred_choice` data
- `agent_service` collects all tool messages during stream, creates them in correct order after stream ends
- Final order: TOOL_CALL → CHOICE → TEXT (model commentary last)

**Refactoring:**
- Extracted `_extract_deferred_choice()` helper for parsing tool results
- Renamed `deferred_messages` → `tool_messages`
- Reduced indentation via early `continue` statements

---

## 2026-01-11 - Display Tool Calls in Conversation UI

Added visibility into agent tool calls. Previously, users only saw final text responses with no indication of what tools were called or what they returned.

**Implementation:**
- Added `TOOL_CALL` content type to Message model
- Capture `ToolUseBlock` and `ToolResultBlock` from Claude SDK response stream
- Match tool invocations to results by `tool_use_id`, calculate duration
- New `_message_tool_call.html` template with expanded display (tool name, params, result, duration)
- `json_pretty` filter that extracts JSON from MCP content blocks `[{"type": "text", "text": "..."}]`

**Bug fixes:**
- `select_related('organization')` on conversation fetch to avoid lazy load in async context
- Replace `.alist()` with `async for` iteration — `alist()` doesn't exist in Django 6.0 (confirmed via docs)

---

## 2026-01-11 - Phase 3: Deployment Flow Agent Tools

Implemented the deployment flow tools for the AI agent (OpenSpec add-deployment-agent Phase 3).

**New tools (5):**
- `create_workspace` — Creates workspaces with AWS account/region config
- `create_app` — Creates app configs with container/build settings
- `create_datastore` — Creates Aurora Serverless v2 database configs
- `deploy_app` — Creates deployment records (stubbed in v1, no real infra)
- `get_deployment_status` — Returns deployment status, phase, and logs

**Progress indicator enhanced:**
- Phase step visualization: Init → Build → Push → Deploy → Health → Done
- Green checkmark and styling when complete
- Started timestamp display

**Process note:** Initially wrote tools as sync functions using `sync_to_async` wrapper, but AGENTS.md specifies using Django's native async ORM methods (`aget`, `acreate`, `aexists`, etc.). Fixed CLAUDE.md by symlinking it to AGENTS.md so patterns are always in context.

---

## 2026-01-10 - Fix Newlines in Chat Messages

**Problem:** Newlines in agent responses weren't displaying - everything appeared on one line.

**Root causes:**
1. SSE was collapsing newlines with `.replace("\n", "")` for single-line format
2. Agent messages used MARKDOWN content type, which expected pre-rendered HTML
3. User messages lacked `whitespace-pre-wrap`

**Fixes:**
- SSE now uses proper multi-line format: each line prefixed with `data:`
- Agent messages use TEXT content type (has `whitespace-pre-wrap`)
- Added `whitespace-pre-wrap` to user messages and fallback
- Increased font size (removed `text-sm`)

---

## 2026-01-10 - Chat UI Polish: Scrollbar & Layout

Refined the chat interface to match ChatGPT/Claude patterns.

**Scrollbar Styling:**
- Dark scrollbar (`gray-700`) with transparent track
- 12px width for visibility
- Uses both `scrollbar-color` (Firefox) and `::-webkit-scrollbar` (Chrome/Safari)

**Layout - Full-width scroll, centered content:**
- Outer container extends full width (negative margins) so scrollbar is at viewport edge
- Inner content constrained to `max-w-4xl` (896px) and centered
- Header, messages, and input all follow same pattern

**Auto-scroll:**
- Scrolls to bottom on page load
- Scrolls on `htmx:afterSwap` (form submission) and `htmx:sseMessage` (SSE)

**Model Change:**
- Switched from Opus 4.5 to Sonnet 4 for faster responses

---

## 2026-01-10 - SSE Chat Streaming Implementation

Implemented real-time chat message streaming using HTMX SSE extension.

### Key Changes

**SSE Extension Setup (`base.html`):**
- Added `htmx-ext-sse@2.2.4` from CDN with `defer` attribute (must load after HTMX)
- Initial bug: extension loaded before HTMX causing "htmx is not defined" error

**SSE Event Format (`chat.py`):**
- Event name: `new-chat-message` (more descriptive than generic "message")
- HTML must be single-line for SSE: `html.replace("\n", "").strip()`
- Only stream agent/system messages; user messages handled by form submission

**Typing Indicator (`_typing_indicator.html`, `view.html`):**
- Uses OOB swap with `hx-swap-oob="outerHTML"` to replace a placeholder div
- On agent response, placeholder is restored (not deleted) for reuse: `<div id="typing-indicator" hx-swap-oob="outerHTML"></div>`
- Positioned outside `#messages` div but inside `#messages-container` so it always appears at bottom

**Empty Chat Placeholder:**
- Given ID `empty-chat-placeholder`
- Deleted via OOB swap on first message sent

**Layout (`view.html`):**
- ChatGPT-style layout: `h-[calc(100vh-9rem)]` fills viewport minus header/padding
- Messages container uses `flex-1` with `overflow-y-auto`
- Input fixed at bottom

### Gotchas

- **Script load order:** SSE extension needs `defer` to load after HTMX
- **Multi-line SSE data:** Each line needs `data:` prefix, or collapse to single line
- **OOB delete vs clear:** Using `delete` removes element entirely; subsequent OOB swaps fail. Use `outerHTML` with empty div to preserve placeholder.
- **Duplicate messages:** Form submission + SSE both showed user messages. Fixed by skipping USER role in SSE stream.

---

## 2026-01-10 - HTMX Sidebar Optimization & History Navigation Fix

Fixed two issues with the HTMX-based SPA navigation.

### Problem 1: Wasteful Sidebar OOB Updates

Every page navigation was sending the **entire sidebar HTML twice** (mobile + desktop) via HTMX out-of-band swaps — roughly 2-3KB per click — just to update which nav item has the "active" highlight class.

**Before:** Each page template included `_sidebar_oob.html`:
```html
<div id="sidebar-nav-desktop" hx-swap-oob="true">{% include "_sidebar_nav.html" %}</div>
<div id="sidebar-nav-mobile" hx-swap-oob="true">{% include "_sidebar_nav.html" %}</div>
```

**After:** Removed all OOB includes. Added 10 lines of client-side JS that updates nav highlighting based on `location.pathname`:
```javascript
function updateNavHighlight() {
    document.querySelectorAll('.nav-link').forEach(link => {
        const isActive = location.pathname.startsWith(link.dataset.navUrl);
        link.classList.toggle('bg-white/5', isActive);
        link.classList.toggle('text-white', isActive);
        link.classList.toggle('text-gray-400', !isActive);
    });
}
document.body.addEventListener('htmx:pushedIntoHistory', updateNavHighlight);
window.addEventListener('popstate', updateNavHighlight);
```

The server-side `is_active` logic remains for the initial page render; JS only handles subsequent HTMX navigations.

### Problem 2: Browser Back/Forward Didn't Work

Clicking links worked, but the browser back/forward buttons did nothing — content didn't restore and nav highlighting didn't update.

**Root cause:** This is an SPA-style app where only `#main-content` changes. By default, HTMX tries to snapshot/restore the entire `<body>` for history navigation, which doesn't work well for shell-based layouts.

**Fix:** Added `hx-history-elt` to the main content div:
```html
<div id="main-content"
     hx-history-elt
     hx-get="{{ content_url }}"
     hx-trigger="load"
     hx-swap="innerHTML">
```

This tells HTMX: "This element is the page content. Snapshot and restore just this element for history navigation."

Also added `popstate` listener (see JS above) so nav highlighting updates on back/forward.

### Key Insight

HTMX history has two parts:
- **`hx-push-url`** — pushes URL to browser history (we had this)
- **`hx-history-elt`** — tells HTMX which element to snapshot/restore (we were missing this)

Without `hx-history-elt`, HTMX doesn't know what content represents the "page" in a shell-based SPA.

**Files changed:**
- `app_shell.html` — added `hx-history-elt` to `#main-content`
- `partials/_sidebar_nav.html` — added client-side nav highlighting JS
- Removed `partials/_sidebar_oob.html`
- Removed `{% include "_sidebar_oob.html" %}` from 8 page templates

---

## 2026-01-10 - Claude Agent Backend Configuration

The deployment agent supports two Claude backends with automatic selection:

**Priority:** `ANTHROPIC_API_KEY` > `AWS_BEDROCK_REGION`

- If `ANTHROPIC_API_KEY` is set → direct Anthropic API (model: `claude-opus-4-5-20251101`)
- Else if `AWS_BEDROCK_REGION` is set → AWS Bedrock (model: `anthropic.claude-opus-4-5-20251101-v1:0`)
- Else → agent unavailable (`is_available()` returns `False`)

Bedrock auto-discovers AWS credentials from CLI/environment. No default region—must be explicitly configured.

**Key files:** `devopshero_app/services/agent/client.py`

---

## 2026-01-10 - Phase 1: Deployment Agent Foundation

Implemented the foundation layer for the AI deployment agent—a conversational interface that will guide users through deploying their applications.

### What We Built

**Django Models:**
- **Workspace, App, Datastore** — Core entities representing deployed resources
- **Deployment, DeploymentLog** — Track deployment attempts and their progress
- **Conversation, Message** — Chat history between user and agent

**Chat Interface:**
- `chat_list`, `chat_view`, `chat_new` — Navigation and conversation management
- `chat_send` — POST endpoint for user messages
- `chat_stream` — SSE endpoint for real-time agent responses
- `chat_messages`, `chat_close` — Pagination and conversation lifecycle

**Message Partials:**
- 10 templates for different content types: text, markdown, code, progress bars, choice buttons, deployment logs, errors
- Typing indicator for agent "thinking" state

### Why

The deployment agent will be a chat-based interface where users describe what they want to deploy, and the agent analyzes their repository, suggests configurations, and orchestrates the deployment. Phase 1 provides:

1. **Persistence** — Conversations survive page refreshes and server restarts
2. **Real-time updates** — SSE allows the agent to stream responses without polling
3. **Rich content** — Different message types enable progress indicators, code blocks, and interactive choices

### What's Next

- Phase 2: Agent core (system prompt, LLM integration, tool definitions)
- Phase 3: Stubbed tool implementations (analyze repos, create deployments)
- Phase 4: UI integration (dashboard button, full chat layout)

---

## 2026-01-08 22:14 - Aurora DatabaseConfig Implementation

- **Scope:** Implemented the Aurora-only `DatabaseConfig` spec in `infra_customer/`.
- **Key changes:** Added nested config dataclasses, engine/version helpers, and per-app Aurora stack/secret naming.
- **Secrets:** Added derived connection secret with `DATABASE_URL` plus individual fields, injected via ECS Secrets Manager.
- **Deployment:** Supports serverless v2 and provisioned modes with orthogonal config validation.

## 2026-01-08 - db-portal Deployment Fix: Migrations & Monitoring

### The Problem

db-portal deployment started failing with tasks crashing immediately:

```
Tasks: 1/1 running, 0 pending
⏳ Confirming stability (2 more checks)...
Tasks: 0/1 running, 0 pending
⚠️  Tasks are failing:
   ❌ Essential container in task exited
```

### Diagnosis

Used AWS CLI to investigate:

```bash
# Check service status
aws ecs describe-services --cluster devopshero-cluster --services db-portal

# List stopped tasks
aws ecs list-tasks --cluster devopshero-cluster --service-name db-portal --desired-status STOPPED

# Describe task to get stop reason
aws ecs describe-tasks --cluster devopshero-cluster --tasks <task-id>

# Get CloudWatch logs
aws logs get-log-events --log-group-name /devopshero/ecs --log-stream-name db-portal/db-portal/<task-id>
```

**Root cause from logs:**

```
** (MyXQL.Error) (1146) (ER_NO_SUCH_TABLE) Table 'db_portal_prod.digests' doesn't exist
```

The Aurora database was freshly provisioned but **no migrations had ever been run**. The app crashed on startup trying to query the `digests` table.

### Fix 1: Run Migrations on Container Start

Modified `Dockerfile` to run migrations before starting the app:

```dockerfile
# Before
CMD ["bin/db_portal", "start"]

# After
CMD ["sh", "-c", "bin/db_portal eval 'DbPortal.Release.migrate()' && bin/db_portal start"]
```

This uses Elixir's release eval to run the `DbPortal.Release.migrate/0` function (which calls `Ecto.Migrator.run/3`) before starting the Phoenix server.

### Fix 2: Monitoring Was Reporting Old Failures

After fixing migrations, the deployment still reported failures—but the service was actually running fine! The monitoring code had a bug.

**Problem:** `check_stopped_tasks()` in `ecs_utils.py` was querying ALL stopped tasks for the service, including old failed tasks from previous deployment attempts. With 15+ old crashed tasks sitting around, it kept reporting them as current failures.

**Fix:** Added `deployment_start_time` parameter to filter out old tasks:

```python
def check_stopped_tasks(ecs_client, cluster, service, deployment_start_time):
    # ...
    for task in details["tasks"]:
        # Skip tasks that started before our deployment (old failures)
        task_started_at = task.get("startedAt")
        if task_started_at and task_started_at < deployment_start_time:
            continue
        # ... rest of failure checking
```

The deployment start time is recorded in `start_ecs_service()` before triggering the deployment, and passed through to the monitoring functions.

### Resetting the Database for Testing

Needed to drop all tables to test migrations from scratch. Multiple approaches failed before finding what works.

**Success: DROP DATABASE + CREATE DATABASE**
```bash
aws ecs run-task --cluster devopshero-cluster --task-definition devopshero-db-portal \
  --launch-type FARGATE \
  --network-configuration 'awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=DISABLED}' \
  --overrides '{
    "containerOverrides": [{
      "name": "db-portal",
      "command": ["sh", "-c", "bin/db_portal eval '\''Application.load(:db_portal); {:ok, _, _} = Ecto.Migrator.with_repo(DbPortal.Repo, fn repo -> repo.query!(\"DROP DATABASE db_portal_prod\"); repo.query!(\"CREATE DATABASE db_portal_prod\"); IO.puts(\"Database dropped and recreated\") end)'\''"]
    }]
  }'
```

This worked because:
- `Ecto.Migrator.with_repo/2` properly starts the Repo with all config
- `DROP DATABASE` + `CREATE DATABASE` is atomic and bypasses FK issues
- The brief connection error mid-execution (when DB is dropped) is harmless


---

## 2026-01-07 - CDK Cleanup: Deprecation Warning and Notices

Fixed two CDK CLI annoyances:
- **Deprecation warning:** Replaced `container_insights=True` with `container_insights_v2=ecs.ContainerInsights.ENABLED` in `deploy_base.py`
- **Telemetry notice:** Added `--no-notices` flag to CDK deploy command in `cdk_utils.py`

---

## 2026-01-07 - Unified Deployment Entry Point

Refactored deployment to separate base layer from app deployment, with a single entry point.

**Key changes:**
- Created `deploy.py` — unified entry point for all deployments
- Created `app_configs.py` — registry of all app configurations
- Split deployment into separate modules: `deploy_base.py`, `deploy_app.py`, `cdk_utils.py`
- Deleted `deploy_app_simple_dashboard.py` and `deploy_app_db_portal.py`
- Base layer (VPC + ECS cluster) is now deployed explicitly, not as part of app deployment

**New file structure:**
- `deploy.py` — CLI entry point
- `app_configs.py` — app configuration registry
- `deploy_base.py` — VPC/ECS cluster stacks and deployment
- `deploy_app.py` — app stacks (ECR, ALB, ECS service, Aurora)
- `cdk_utils.py` — shared CDK deployment utilities

**New usage:**
```bash
# Base layer
uv run python deploy.py --base                       # Deploy VPC + ECS cluster
uv run python deploy.py --base --teardown            # Teardown base layer

# Apps
uv run python deploy.py --app simple-dashboard       # Deploy app
uv run python deploy.py --app db-portal              # Deploy another app
uv run python deploy.py --app simple-dashboard --image-tag v1.2.3  # Specific tag
uv run python deploy.py --app simple-dashboard --teardown          # Teardown app only
```

---

## 2026-01-07 - Deprecated CloudFormation Deployment Engine

Moved all CloudFormation-based deployment code to `infra_customer/_old_cf/`:
- `deploy_app_cf.py` — CloudFormation deployment module
- `cf_templates/` — All CloudFormation JSON templates

**Changes:**
- CDK is now the only deployment engine
- `cloudformation_utils.py` stays in place (still used by CDK for stack operations)

---

## 2026-01-08 - Aurora Credentials via ECS Secret Injection

All 5 database fields now come from Aurora's managed secret via ECS secret injection:

```python
secrets["DATABASE_HOST"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="host")
secrets["DATABASE_PORT"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="port")
secrets["DATABASE_NAME"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="dbname")
secrets["DATABASE_USERNAME"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="username")
secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(aurora_cluster.secret, field="password")
```

Previously `host`, `port`, `dbname` were plain env vars. Now all DB credentials stay in Secrets Manager.

---

## 2026-01-07 - App Secrets Architecture: Per-App Isolation with boto3

Implemented a secrets management system that provides per-app isolation and flexible secret structures.

### Why Not CloudFormation for Secrets?

CloudFormation's `GenerateSecretString` can only auto-generate **ONE** random field per secret (the parameter is `generate_string_key: str`, not a list). This was too limiting for apps like db_portal that need multiple generated fields:

- `secret_key_base` — Phoenix secret key base (needs to be random)
- `signing_salt` — Phoenix signing salt (needs to be random)
- `slack_token` — Slack API token (literal value)

### Solution: boto3 Secret Creation Before CDK

Secrets are created via boto3 **before** CDK runs, allowing:
- Multiple randomly-generated fields per secret
- Custom JSON structure per app
- Secrets persist across stack deletions (feature, not bug)

**Flow:**
1. `deploy()` calls `secrets_utils.ensure_app_secrets_exist()`
2. If `devopshero/{app_name}/secrets` doesn't exist → generate values for `None` fields → create via boto3
3. If it exists → leave it alone (values are stable)
4. CDK only grants IAM permissions to read the secret (no secret creation in CloudFormation)

### Per-App Task Role Isolation

Each app gets its own ECS task role with permissions scoped to only its secrets:

```python
# AppWithAlbStack creates per-app task role
task_role = iam.Role(self, "TaskRole", role_name=f"devopshero-{app_name}-task-role", ...)
task_role.add_to_policy(iam.PolicyStatement(
    actions=["secretsmanager:GetSecretValue"],
    resources=[f"arn:aws:secretsmanager:{region}:{account}:secret:devopshero/{app_name}/*"],
))
```

This ensures `db-portal` cannot read `simple-dashboard` secrets, and vice versa.

### AppConfig Secret Definition

Apps define their secret structure in `AppConfig`:

```python
app_secrets={
    "slack_token": "disabled",   # Literal value
    "secret_key_base": None,     # Generate random 64-char
    "signing_salt": None,        # Generate random 64-char
}
```

- `str` value → use literally
- `None` → generate random 64-char alphanumeric string

### App-Side: SecretsManagerConfigProvider

The Elixir app reads secrets from `devopshero/{app_name}/secrets`:

```elixir
# lib/db_portal/secrets_manager_config_provider.ex
defp load_secrets(secret_name) do
  with {:ok, %{"SecretString" => json}, _} <- Aws.get_secret_value(secret_name),
       {:ok, secrets} <- Jason.decode(json) do
    {:ok, %{
      "signing_salt" => Map.get(secrets, "signing_salt", ""),
      "secret_key_base" => Map.get(secrets, "secret_key_base", ""),
      :slack_token => Map.get(secrets, "slack_token", "disabled")
    }}
  end
end
```

### Dev Mode Bypass

In dev, set `:no_secrets_mgr` in `config/dev.exs` to skip AWS:

```elixir
config :db_portal, :no_secrets_mgr,
  slack_token: "dev-slack-token",
  signing_salt: "dev-signing-salt",
  secret_key_base: "dev-secret-key-base-64-chars..."
```

### Key Files

- `infra_customer/secrets_utils.py` — `ensure_app_secrets_exist()` function
- `infra_customer/appconfig.py` — `app_secrets` field definition
- `infra_customer/deploy_app_cdk.py` — Per-app task role with scoped IAM
- `db_portal/lib/db_portal/secrets_manager_config_provider.ex` — App-side secret loading
- `db_portal/lib/db_portal/application.ex` — Dev/prod branching via `:no_secrets_mgr`

---

## 2026-01-07 - ARM64 Docker Builds: Avoiding QEMU Emulation Bug with Elixir 1.18

When building Docker images for ECS/Fargate on Apple Silicon Macs, we encountered a critical build failure with Elixir 1.18.

**The Error:**
```
Error while loading project :configparser_ex at /app/deps/configparser_ex
** (ArgumentError) could not call Module.put_attribute/3 because the module DbPortal.MixProject is already compiled
```

**Root Cause:** Building with `--platform linux/amd64` on an ARM Mac forces Docker to use QEMU emulation. QEMU has subtle timing/behavior differences that expose a bug in Elixir 1.18's module loading during `mix deps.compile`. The error occurs because Mix tries to put an attribute on a module that's already been compiled — a race condition that only manifests under emulation.

**Solution:** Build for ARM64 and run on Graviton (ARM) Fargate instances:

```python
# ecr_utils.py - build_and_push_docker_image()
build_result = subprocess.run(
    ["docker", "build", "--platform", "linux/arm64", "-t", image_uri, "."],
    ...
)

# deploy_app_cdk.py - FargateTaskDefinition
runtime_platform=ecs.RuntimePlatform(
    cpu_architecture=ecs.CpuArchitecture.ARM64,
    operating_system_family=ecs.OperatingSystemFamily.LINUX,
),
```

**Why ARM64 is Better:**
- **Native builds on Apple Silicon** — no QEMU emulation, no timing bugs
- **20% cheaper** — Graviton instances cost less than x86
- **Better performance** — Graviton2/3 processors are fast

If amd64 builds are ever needed (e.g., for x86 Fargate), consider:
- Downgrading to Elixir 1.17.x or earlier
- Using a CI/CD system with native x86 runners (GitHub Actions, etc.)
- Building on an x86 machine or EC2 instance

---

## 2026-01-07 - db_portal Changes for ECS/Fargate Deployment

Made several modifications to `db_portal` (Elixir/Phoenix app) to run in ECS/Fargate behind an ALB. The app was originally designed to run on EC2 with direct HTTPS and Okta SSO.

### 1. Health Check Endpoint

**Files:** `lib/db_portal_web/router.ex`, `lib/db_portal_web/controllers/health_controller.ex`

Added a dedicated `/health` endpoint for ALB health checks that bypasses authentication:

```elixir
# router.ex - Add before authenticated routes
scope "/health", DbPortalWeb do
  get "/", HealthController, :index
end

# health_controller.ex - New file
defmodule DbPortalWeb.HealthController do
  use DbPortalWeb, :controller
  def index(conn, _params) do
    send_resp(conn, 200, "OK")
  end
end
```

### 2. HTTP-Only Mode (DISABLE_HTTPS)

**File:** `config/runtime.exs`

The app originally ran its own HTTPS server with SiteEncrypt/ACME. Behind ALB (which terminates TLS), we need HTTP-only mode:

```elixir
if System.get_env("DISABLE_HTTPS") == "true" do
  http_port = String.to_integer(System.get_env("PORT", "4000"))
  host = System.get_env("PHX_HOST", "localhost")

  config :db_portal, DbPortalWeb.Endpoint,
    url: [host: host, port: 443],  # External URL (ALB terminates TLS)
    http: [port: http_port],        # Internal port for ALB health checks
    server: true,
    check_origin: false
end
```

### 3. Authentication Bypass (DISABLE_AUTH)

**File:** `config/runtime.exs`

The app uses Okta SAML for production auth. For ECS deployment without Okta configured, we can bypass:

```elixir
use_okta_auth = cond do
  System.get_env("DISABLE_AUTH") == "true" -> false  # NEW: Explicit bypass
  Application.get_env(:db_portal, :env) == :prod -> true
  System.get_env("MY_OKTA") != nil -> true
  true -> false
end
```

When disabled, the app uses `do_fake_verify` which creates a session for `dev@example.com`.

### 4. Secrets Manager Bypass (NO_SECRETS_MGR)

**Files:** `config/runtime.exs`, `lib/db_portal/application.ex`

The app reads secrets (Slack token, signing salt, secret key base) from AWS Secrets Manager. For ECS, we inject these via environment variables:

```elixir
# runtime.exs
if System.get_env("NO_SECRETS_MGR") == "true" do
  config :db_portal, :no_secrets_mgr,
    slack_token: System.get_env("SLACK_TOKEN", "disabled"),
    signing_salt: System.get_env("SIGNING_SALT", "default-signing-salt-change-me"),
    secret_key_base: System.get_env("SECRET_KEY_BASE", "...")
end

# application.ex - configuration/1 function
case Application.get_env(:db_portal, :no_secrets_mgr) do
  nil -> # Use Secrets Manager (original behavior)
  conf -> # Use env vars from :no_secrets_mgr config
end
```

### 5. Flexible Database Configuration

**File:** `config/runtime.exs`

Support both `DATABASE_URL` (traditional) and individual components (for ECS secrets injection):

```elixir
cond do
  database_url = System.get_env("DATABASE_URL") ->
    config :db_portal, DbPortal.Repo, url: database_url, pool_size: ...

  System.get_env("DATABASE_HOST") ->
    config :db_portal, DbPortal.Repo,
      hostname: System.get_env("DATABASE_HOST"),
      port: String.to_integer(System.get_env("DATABASE_PORT", "3306")),
      database: System.get_env("DATABASE_NAME", "db_portal_prod"),
      username: System.get_env("DATABASE_USERNAME", "dbadmin"),
      password: System.get_env("DATABASE_PASSWORD", ""),
      pool_size: ...

  true -> :ok  # Use defaults from dev.exs/prod.exs
end
```

### Environment Variables Summary

| Variable | Purpose | Example Value |
|----------|---------|---------------|
| `DISABLE_HTTPS` | Run HTTP-only (ALB terminates TLS) | `true` |
| `DISABLE_AUTH` | Bypass Okta SSO | `true` |
| `NO_SECRETS_MGR` | Use env vars instead of Secrets Manager | `true` |
| `PORT` | HTTP listen port | `4000` |
| `PHX_HOST` | External hostname | `dataengr.chsandbox.com` |
| `DATABASE_HOST` | Aurora endpoint | `devopshero-aurora.cluster-xxx.rds.amazonaws.com` |
| `DATABASE_PORT` | MySQL port | `3306` |
| `DATABASE_NAME` | Database name | `db_portal_prod` |
| `DATABASE_USERNAME` | DB user (from Secrets Manager) | Injected by ECS |
| `DATABASE_PASSWORD` | DB password (from Secrets Manager) | Injected by ECS |
| `SECRET_KEY_BASE` | Phoenix secret key | 64+ char string |
| `SIGNING_SALT` | Cookie signing salt | Random string |

---

## 2026-01-06 - Fixed False Failures in ECS Service Stability Check

The `wait_for_service_stable` function was incorrectly reporting task failures during successful deployments.

**Symptom:** Service would reach stable state (1/1 running, 0 pending) but then fail with:
```
⚠️  Tasks are failing:
   ❌ Scaling activity initiated by (deployment ecs-svc/...)
❌ Too many task failures, aborting
```

**Root Cause:** The `check_stopped_tasks` function was treating **all** stopped tasks as failures. During normal deployments, ECS stops old tasks with `stopCode: ServiceSchedulerInitiated` — this is expected behavior (old tasks being rotated out), not a failure.

**Fix:** Updated `check_stopped_tasks` to check the `stopCode` field and ignore intentional stops:

```python
INTENTIONAL_STOP_CODES = {
    "ServiceSchedulerInitiated",  # Normal deployment/scaling rotation
    "UserInitiated",              # User manually stopped the task
    "SpotInterruption",           # Spot instance interrupted (not app's fault)
}

# Skip tasks that were intentionally stopped (not failures)
if stop_code in INTENTIONAL_STOP_CODES:
    continue
```

Now only genuine failures (`EssentialContainerExited`, `TaskFailedToStart`, etc.) are reported.

---

## 2026-01-06 - Fixed CDK Subnet Route Table Warnings

CDK emits warnings when importing a VPC without route table IDs:

```
[Warning at .../ImportedVpc/PublicSubnet1] No routeTableId was provided to the subnet...
```

**Attempted workarounds (rejected):**
- `@aws-cdk/aws-ec2:noSubnetRouteTableId` context flag — only *acknowledges* the warning, doesn't suppress output
- Filtering stderr — works but feels hacky

**Fix:** Export route table IDs from `VpcStack` and import them in `AppWithAlbStack`. With `nat_gateways=1`, all public subnets share one route table and all private subnets share one route table, so we only need two exports:

```python
# VpcStack exports
CfnOutput(self, "PublicRouteTableId", value=self.vpc.public_subnets[0].route_table.route_table_id, ...)
CfnOutput(self, "PrivateRouteTableId", value=self.vpc.private_subnets[0].route_table.route_table_id, ...)

# AppWithAlbStack imports (same RT repeated for each subnet)
vpc = ec2.Vpc.from_vpc_attributes(
    ...,
    public_subnet_route_table_ids=[public_rt, public_rt],
    private_subnet_route_table_ids=[private_rt, private_rt],
)
```

---

## 2026-01-06 - CDK Deployment Fixes (AZs, CIDR Selection, Output Directory)

Fixed several issues preventing CDK deployments from working correctly.

### Issue 1: Dummy Availability Zones

**Problem:** CDK was using `dummy1a` and `dummy1b` instead of real availability zones like `us-east-1a`. This caused CloudFormation to fail with "Value (dummy1a) for parameter availabilityZone is invalid."

**Root Cause:** When CDK synthesizes stacks for cross-account deployment, it doesn't have context about the target account's AZs and falls back to dummy values.

**Fix:** Added explicit `availability_zones` parameter to `VpcStack`:

```python
class VpcStack(Stack):
    def __init__(self, ..., availability_zones: list[str], ...):
        self.vpc = ec2.Vpc(
            self, "Vpc",
            availability_zones=availability_zones,  # Explicit AZs
            ...
        )

# In deploy():
availability_zones = [f"{region}a", f"{region}b"]
vpc_stack = VpcStack(..., availability_zones=availability_zones, ...)
```

### Issue 2: EcsClusterStack Creating Its Own VPC

**Problem:** `ecs.Cluster()` creates a default VPC if none is provided, which also had the dummy AZ problem.

**Fix:** Modified `EcsClusterStack` to accept a VPC parameter:

```python
class EcsClusterStack(Stack):
    def __init__(self, ..., vpc: ec2.IVpc, ...):
        self.cluster = ecs.Cluster(
            self, "EcsCluster",
            vpc=vpc,  # Use provided VPC instead of creating one
            ...
        )

# In deploy():
ecs_cluster_stack = EcsClusterStack(..., vpc=vpc_stack.vpc, ...)
ecs_cluster_stack.add_dependency(vpc_stack)
```

### Issue 3: Hardcoded VPC CIDR

**Problem:** VPC CIDR was hardcoded as `172.21.0.0/20`, which could conflict with existing VPCs.

**Fix:** Reused the CloudFormation approach — check if VPC stack exists, otherwise find available CIDR:

```python
vpc_stack_exists = cloudformation_utils.stack_exists(cf_client, vpc_stack_name)

if vpc_stack_exists:
    vpc_cidr = cloudformation_utils.get_stack_output(cf_client, stack_name=vpc_stack_name, output_key="VpcCidr")
else:
    cidr_config = vpc_utils.find_available_vpc_cidr(ec2_client)
    vpc_cidr = cidr_config["VpcCidr"]
```

### Issue 4: CDK Output Directory

**Problem:** `app.synth()` was writing to `cdk.out/` in whatever directory the script ran from.

**Fix:** Configured explicit output directory in `infra_customer/cdk.out/`:

```python
CDK_OUT_DIR = Path(__file__).parent / "cdk.out"
cdk_app = App(outdir=str(CDK_OUT_DIR))
```

Added `infra_customer/cdk.out/` to `.gitignore`.

### CDK Context Cache (`cdk.context.json`)

CDK creates `cdk.context.json` to cache AWS lookups (like availability zones) so subsequent synths don't need API calls. Example content:

```json
{
  "availability-zones:account=266117665083:region=us-east-1": [
    "us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1e", "us-east-1f"
  ]
}
```

Since we pass explicit AZs rather than using CDK lookups, this file isn't required. Added to `.gitignore` along with `cdk.out/`.

### Result

All 4 CDK stacks now deploy successfully:
- `devopshero-vpc-cdk` — VPC with proper AZs and auto-selected CIDR
- `devopshero-ecs-cluster-cdk` — ECS cluster using the VPC
- `devopshero-ecr-simple-dashboard` — ECR repository
- `devopshero-app-with-alb-simple-dashboard` — ALB, ECS service, ACM cert, Route53

---

## 2026-01-06 - CDK Bootstrap & cdk.out Exploration

Explored the CDK output folder structure and bootstrapped the customer AWS account for CDK deployments.

### cdk.out Folder Structure

Each CDK stack generates two files:

| File | Purpose |
|------|---------|
| `*.template.json` | The CloudFormation template to deploy |
| `*.assets.json` | Manifest of assets (files, Docker images) to publish before deployment |

Plus shared files: `manifest.json` (app manifest), `tree.json` (construct tree), `cdk.out` (version marker).

The `.assets.json` files reference IAM roles created by CDK bootstrap (e.g., `cdk-hnb659fds-file-publishing-role-...`).

### CDK Bootstrap

Ran `npx cdk bootstrap aws://266117665083/us-east-1` to create the **CDKToolkit** CloudFormation stack. This provisions:
- S3 bucket for file assets
- ECR repository for Docker images
- IAM roles (FilePublishing, ImagePublishing, CloudFormationExecution, Deployment, Lookup)
- SSM parameter storing bootstrap version

**Note:** DevOps Hero's existing customer bootstrap (CloudFormation template creating cross-account IAM role) is separate from CDK bootstrap. CDK bootstrap is specifically for CDK's asset publishing pipeline.

### What `cdk deploy` Actually Does

```
1. SYNTH → Runs CDK app, generates *.template.json + *.assets.json to cdk.out/

2. PUBLISH ASSETS (reads *.assets.json)
   → File assets: zip & upload to S3 bootstrap bucket (uses FilePublishingRole)
   → Docker assets: build & push to ECR bootstrap repo (uses ImagePublishingRole)

3. DEPLOY (for each stack, in dependency order from manifest.json)
   → Upload template to S3 (if >51KB)
   → Call CloudFormation CreateStack/UpdateStack
   → Uses DeploymentActionRole to call CF
   → CF uses CloudFormationExecutionRole to create resources
```

### Bootstrap Roles Explained

- **FilePublishingRole** — Upload file assets (Lambda code, etc.) to S3
- **ImagePublishingRole** — Push Docker images to ECR
- **DeploymentActionRole** — Call CloudFormation APIs
- **CloudFormationExecutionRole** — Used by CF to create/modify AWS resources
- **LookupRole** — Read-only queries during synth (e.g., `Vpc.from_lookup()`)

### Attempted: Bypassing CDK Bootstrap

Explored deploying CDK-generated templates directly via boto3 CloudFormation to avoid the bootstrap requirement:

```python
# Instead of: npx cdk deploy
# We tried: cloudformation_utils.deploy_cloudformation_stack(template_path=...)
```

**Pros:** No bootstrap needed, simpler for asset-free stacks.

**Cons:**
- Must manually maintain stack deployment order (CDK reads this from `manifest.json`)
- No asset support (Lambda code, Docker images via `from_asset()`)
- Reinventing what CDK CLI already does well
- 51KB template limit without S3 upload

**Decision:** Reverted to using `npx cdk deploy`. The bootstrap overhead is worth the reliability.

### Future: Customer Onboarding Options

When a customer connects their AWS account, we need CDK bootstrap in their account. Options:

1. **Run `cdk bootstrap` programmatically** — After customer creates our cross-account role, we assume it and run bootstrap via CLI or SDK.

2. **Include bootstrap in customer's CloudFormation** — The bootstrap template is available:
   ```bash
   npx cdk bootstrap --show-template > bootstrap-template.yaml
   ```
   Could merge with or deploy alongside `cf_install_template.json`.

3. **Two-stack customer setup** — Customer clicks "Connect AWS Account" and we deploy:
   - Stack 1: DevOps Hero cross-account role (existing)
   - Stack 2: CDKToolkit bootstrap stack

**TODO:** Decide which approach is cleanest for customers. For now, manually ran bootstrap on test account.

### Project CDK Setup

This project doesn't use `cdk.json`. Instead, CDK is used programmatically:
- Python `aws-cdk-lib` defines stacks in `deploy_app_cdk.py`
- `App().synth()` generates templates to `cdk.out/`
- `npx cdk` fetches the CLI on-demand (not installed as a project dependency)

---

## 2026-01-05 - Deployment Script Refactoring & CDK Alternative

Refactored the deployment codebase for better modularity and added AWS CDK as an alternative to CloudFormation templates.

### New CDK Deployment Option

Created `deploy_app_cdk.py` — a CDK-based equivalent of the CloudFormation deployment. Same infrastructure (VPC, ECS cluster, ECR, ALB, ECS service), but defined in Python using CDK constructs instead of JSON templates.

**Why CDK?** Exploring whether CDK's type safety and IDE support improve maintainability over Jinja2-templated JSON. Both approaches coexist for comparison.

### Unified Entry Point

Created `deploy_app_simple_dashboard.py` as the single entry point for deployments:

```bash
uv run python deploy_app_simple_dashboard.py                     # Deploy with CF (default)
uv run python deploy_app_simple_dashboard.py --engine cdk        # Deploy with CDK
uv run python deploy_app_simple_dashboard.py --image-tag v1.2.3  # Specific tag
uv run python deploy_app_simple_dashboard.py --engine cdk --synth-only  # CDK synth only
```

The entry point handles:
- App configuration (`AppConfig` for simple-dashboard)
- Credential loading from `.env`
- Cross-account role assumption
- Dispatching to CF or CDK engine

### Extracted Utility Modules

Split common functionality into focused modules:

- `appconfig.py` — `AppConfig` dataclass (unified, `cpu`/`memory` as `int`)
- `ecr_utils.py` — `build_and_push_docker_image()`
- `route53_utils.py` — `get_hosted_zone_id()`
- `iam_utils.py` — `load_credentials_from_env()`, `get_assumed_role_session()`
- `cloudformation_utils.py` — `get_stack_output()`, `get_app_urls()`, stack operations

### File Renames

- `deploy_app.py` → `deploy_app_cf.py` (CloudFormation engine)
- `docker_utils.py` → `ecr_utils.py`

### Teardown Support

Added `--teardown` flag to delete all stacks in reverse dependency order:

```bash
uv run python deploy_app_simple_dashboard.py --teardown
```

**Gotcha: ECR repositories must be empty before deletion.** CloudFormation can't delete an ECR repo containing images. CDK has `empty_on_delete=True`, but raw CloudFormation doesn't. Solution: `ecr_utils.delete_all_ecr_images()` empties the repo before stack deletion.

### Code Style Changes

- `load_env()` renamed to `load_credentials_from_env()` and now raises `RuntimeError` instead of `sys.exit(1)`
- `AppConfig.to_template_vars()` converts `int` fields to `str` for CloudFormation compatibility
- Removed `--infra-only` and `--app-only` flags (always deploy everything)

---

## 2026-01-05 - HTTPS & Custom Domain Support (Milestone M5)

Added HTTPS support with custom domains via ACM and Route53.

### Changes

**`AppConfig` dataclass** — Added two new fields:
- `domain_name`: Full domain (e.g., `"simple-dashboard.chsandbox.com"`)
- `hosted_zone_name`: Route53 zone (e.g., `"chsandbox.com"`)

**`cf_app_with_alb.json`** — When domain is configured, creates:
- ACM certificate with DNS validation (auto-validated via Route53)
- HTTPS listener on port 443 with TLS 1.3 policy
- HTTP→HTTPS redirect (301) on port 80
- Route53 A record (alias to ALB)
- Security group rule for port 443

**`deploy_app.py`** — Added `get_hosted_zone_id()` to look up existing Route53 zone at deploy time, passing the zone ID as a CloudFormation parameter.

### Gotcha: Security Group Names

Removed explicit `GroupName` from the security group. CloudFormation can't replace resources with explicit names (name collision during create-before-delete). Let CF generate names like `{StackName}-{LogicalId}-{Random}`.

### Result

App now accessible at `https://simple-dashboard.chsandbox.com` with valid SSL.

---

## 2026-01-03 - ECS Health Checks: Two Different Mechanisms

There are **two separate health check systems** in an ECS/ALB setup:

| Health Check | Who Runs It | On Failure |
|--------------|-------------|------------|
| **Target Group** (ALB) | Load balancer pings HTTP endpoint | Stops routing traffic to task (task keeps running) |
| **Container** (ECS) | ECS agent runs shell command inside container | Kills and replaces the entire task |

**For deployments**, only the Target Group health check matters. ECS considers a task ready for traffic when the ALB marks it healthy. The container health check is optional—useful as a "liveness probe" to catch deadlocked processes, but not involved in deployment rollouts.

**Rolling deployment behavior:** With `MinimumHealthyPercent: 100` and `MaximumPercent: 200`, ECS spins up a new task first, waits for ALB health checks to pass, then drains the old task. This causes 2 tasks to run temporarily—expected behavior for zero-downtime deploys.

---

## 2026-01-03 - Parameterized App Deployment with Jinja2 Templates

Refactored the deployment script to support deploying any app, not just `simple-dashboard`.


### Hybrid Templating Strategy

The key distinction is **when** values get resolved:

**Jinja2 (render time)** — values baked into JSON before CloudFormation sees it:
- `app_name` in resource names and export names (CF can't parameterize these)
- `environment_variables` as a proper JSON array (CF can't loop)
- Conditional sections like health checks
- Anything structural that doesn't change between deployments of the same app

**CloudFormation Parameters (deploy time)** — resolved by CloudFormation:
- `ImageTag` — changes frequently, visible in AWS Console, can redeploy same template with new value
- Simple string/number substitutions where you want AWS Console visibility

Example: `ImageTag` is a CF Parameter because you deploy the same app repeatedly with different tags. You want to see "what tag is deployed?" in the Console, and CF can detect "no changes needed" if you redeploy with the same tag.


### Code Organization

Split `infra_customer/` into focused modules:

```
infra_customer/
├── deploy_app.py           # Main script + AppConfig dataclass
├── vpc_utils.py            # CIDR overlap detection, available range finder
├── ecs_service_stable.py   # Service stabilization with failure diagnostics
├── cf_ecr.json             # Jinja2: ECR repository
└── cf_app_with_alb.json    # Jinja2: Task Definition + ALB + ECS Service
```

### AppConfig Dataclass

All app-specific settings in one place, passed down through functions:

```python
AppConfig(
    app_name="simple-dashboard",
    ecr_repo_name="devopshero/simple-dashboard",
    container_port=8501,
    health_check_path="/_stcore/health",
    health_check_command="...",
    environment_variables=[...],
    app_source_path=Path(...),
)
```

### Simplifications

- Removed no-ALB deployment path (all apps get ALB)
- Removed default function parameters per project style guide

---

## 2025-12-29 - Decoupled ECS Cluster Stack from VPC Stack

Moved the default security group from `cf_ecs_cluster.json` to `cf_vpc.json`. The ECS cluster stack now has zero VPC dependencies, avoiding CloudFormation's "export in use" lock when updating the VPC stack.

## 2025-12-29 - VPC Architecture Change: NAT Gateway for ECS Tasks

Changed the customer VPC from public-subnet-with-public-IP to private-subnet-behind-NAT-Gateway.

**Before:** Fargate tasks ran in public subnets and acquired public IPs to reach ECR/internet.  
**After:** Fargate tasks run in private subnets; outbound traffic goes through a NAT Gateway.

**Rationale:** Cleaner security posture—tasks have no public IPs. The ~$32/month NAT cost is acceptable.

**Future:** Plan to support multiple networking models based on customer preference (e.g., NAT Gateway, public IP, VPC endpoints only).

---

## 2025-12-29 - Customer Account Infrastructure Templates (VPC + ECS Cluster)

### Summary

Created CloudFormation templates and a Python deployment script to initialize customer AWS accounts with the infrastructure needed to run Fargate apps. Successfully deployed to test account `266117665083`.

### Files Created

```
infra_customer/
├── cf_vpc.json              # VPC with 2 public subnets
├── cf_ecs_cluster.json      # ECS cluster, security group, IAM roles
└── test_deploy_infra.py     # Python script to deploy via cross-account role
```

### Architecture Decision: Private Apps with Public Subnets

Apps are private (accessible only from VPC via VPN), but Fargate tasks run in **public subnets with public IPs**. This avoids NAT Gateway costs (~$32/month) while still allowing tasks to pull images from ECR.

```
┌─────────────────────────────────────────┐
│              VPC (172.20.0.0/20)        │
│  ┌───────────────────────────────────┐  │
│  │  Public Subnet 1 (172.20.0.0/24)  │  │
│  │  Public Subnet 2 (172.20.1.0/24)  │  │
│  │  └── Fargate Tasks (public IP)    │◄── VPN access only
│  └───────────────────────────────────┘  │
│  └── Internet Gateway                   │
└─────────────────────────────────────────┘
```

Security group restricts inbound to VPC CIDR only.

### CIDR Range Selection: 172.x.x.x with Auto-Conflict Avoidance

**Why 172.16-31.x.x instead of 10.x.x.x or 192.168.x.x:**
- `10.x.x.x` — Most commonly used by enterprises, higher conflict risk
- `192.168.x.x` — Used by home networks, causes VPN routing issues for developers
- `172.16-31.x.x` — Rarely used, VPN-friendly, good middle ground

**Automatic CIDR selection:** The Python script scans existing VPCs in the customer account and picks the first available `/20` block in `172.20-31.x.x` that doesn't overlap.

```python
def find_available_vpc_cidr(ec2_client) -> dict:
    # Gets all existing VPC CIDRs
    # Tries 172.20.0.0/20, 172.20.16.0/20, etc.
    # Returns first non-conflicting CIDR with subnet allocations
```

### CloudFormation Exports & Cross-Stack Dependencies

The VPC stack exports values that the ECS cluster stack imports:
- `devopshero-vpc-id`
- `devopshero-vpc-cidr`
- `devopshero-public-subnet-1`, `devopshero-public-subnet-2`

**Lesson learned:** CloudFormation prevents modifying exported values if another stack imports them. When we tried to change the VPC CIDR after ECS cluster was deployed:

```
Cannot update export devopshero-vpc-cidr as it is in use by devopshero-ecs-cluster
```

**Solution:** Delete stacks in reverse dependency order, then recreate. This is fine because VPC CIDRs are effectively immutable anyway.

**Considered nested stacks** but decided against for now — adds complexity (S3 hosting, harder debugging) for minimal benefit with just 2 stacks.

### Cross-Account Deployment via AssumeRole

The Python script:
1. Loads DOH control plane credentials from `.env`
2. Assumes the `devopshero-{external_id}` role in the target account
3. Deploys CloudFormation stacks with the assumed credentials

```python
session = get_assumed_role_session(
    access_key=os.getenv("DOH_AWS_ACCESS_KEY"),
    secret_key=os.getenv("DOH_AWS_SECRET_KEY"),
    account_id="266117665083",
    external_id="9e62c988-09dd-4f96-b5a7-a67646dd285b",
    region="us-east-1",
)
```

### What Got Deployed

| Stack | Resources |
|-------|-----------|
| `devopshero-vpc` | VPC, 2 public subnets, Internet Gateway, route table |
| `devopshero-ecs-cluster` | ECS cluster, security group, Task Execution Role, Task Role, CloudWatch log group |

### Next Steps

1. Create per-app CloudFormation template (ECR repo + Task Definition + ECS Service)
2. Add Docker build & push to ECR in the Python script
3. Deploy `simple_dashboard` end-to-end
4. Get a working URL accessible via VPN

---

## 2025-12-28 - Created Simple Dashboard (Track 1 MVP App)

### Summary

Created `deployable_repos/simple_dashboard/` — a minimal Streamlit app to test the deployment pipeline.

### Files Created

```
simple_dashboard/
├── app.py                      # Streamlit dashboard with fake metrics
├── pyproject.toml              # For local dev with uv
├── requirements.txt            # For Dockerfile
├── Dockerfile                  # Uses uv for fast installs
├── README.md
└── .streamlit/
    ├── credentials.toml        # Skips email prompt
    └── config.toml             # Disables telemetry
```

### Streamlit First-Run Prompt Skip

Streamlit shows an email collection prompt on first run. To skip it, create `.streamlit/credentials.toml`:

```toml
[general]
email = ""
```

And `.streamlit/config.toml` to disable telemetry:

```toml
[browser]
gatherUsageStats = false

[server]
headless = true
```

### Local Development

```bash
cd deployable_repos/simple_dashboard
uv run streamlit run app.py
```

`uv run` automatically creates an isolated `.venv`, installs deps from `pyproject.toml`, and runs the app.

---

## 2025-12-28 - Strategic Direction: Dual-Track MVP Approach

### Summary

Defined the strategic approach for finding product market fit: build a simple deployment pipeline first, then incrementally add features toward deploying complex enterprise apps.

### The Problem

We had built solid infrastructure (auth, org management, AWS account connection) but zero core product functionality. The gap between "connected AWS account" and "deployed app" was undefined.

### The Decision: Dual-Track Approach

Rather than attempting to deploy a complex app immediately, we'll pursue two parallel tracks:

**Track 1: Simple Dashboard → Working Deployment**
- Create a minimal Streamlit app with no dependencies
- Build the core deployment pipeline: Build → ECR → Fargate → URL
- Prove the loop closes end-to-end
- Target: days, not weeks

**Track 2: Feature Roadmap → db_portal**
- Use `db_portal` (existing Phoenix/Elixir internal tool) as the north star
- Each milestone adds one capability that enterprise apps need
- Eventually deploy db_portal as proof of enterprise readiness

### Feature Milestones (Track 2)

| Milestone    | Feature                    | db_portal Requirement                            |
|--------------|----------------------------|--------------------------------------------------|
| **M1**       | Basic deploy (Streamlit)   | N/A (foundation)                                 |
| **M2**       | Environment variables      | `RUN_SAMPLER`, `SSLCERT_MODE`, `LE_MODE`         |
| **M3**       | Secrets injection          | `secret_key_base`, `signing_salt`, `db_password` |
| **M4**       | Managed RDS/Aurora         | MySQL database dependency                        |
| **M5**       | Custom domain + TLS        | `dataengr.humanityrules.io` with certs              |
| **M6**       | SSO integration            | Okta SAML                                        |
| **M7**       | Private VPC networking     | Aurora connectivity, no public internet          |
| **M8**       | Background workers         | Sampler scheduler process                        |

### Why This Approach

1. **Faster learning** — Get a working deployment in days, not weeks
2. **Avoid scope creep** — Don't get lost in db_portal-specific issues
3. **Incremental value** — Each milestone is independently demoable
4. **Clear north star** — db_portal keeps us honest about enterprise requirements

### Target Persona

Data Scientists / ML Engineers who can build apps but struggle with deployment. They represent:
- Maximum pain (deployment is mystical to them)
- Growing market (vibe coding trend)
- Simpler initial scope (stateless web UIs)
- Clear success metric ("I have a URL")

### Folder Structure

```
deployable_repos/
├── db_portal/           # Track 2 goal (complex Phoenix app)
└── simple_dashboard/    # Track 1 MVP (minimal Streamlit app)
```

### Next Steps

1. Create `simple_dashboard/` with Streamlit app + Dockerfile
2. Manually deploy to Fargate to understand the AWS plumbing
3. Automate the pipeline in DevOps Hero
4. Wire to UI: App model + "Deploy" button + status page

---

## 2025-12-28 - Fixed Dropdown Popover Width Issue

### Summary

Fixed a visual bug where the organization dropdown's options list was rendering full-width instead of matching the button width.

### The Problem

The `el-options` popover element was using `w-(--button-width)` to match the button width, but the `--button-width` CSS variable was never being set by the Tailwind Plus Elements library. Since popover elements render in the browser's "top layer" (outside normal document flow), they don't inherit width from parent containers.

### The Fix

Added CSS Anchor Positioning rules in `styles.css`:

```css
el-select {
  anchor-name: --select-anchor;
}

el-options[popover] {
  position-anchor: --select-anchor;
  width: 15rem;                /* fallback for older browsers */
  width: anchor-size(width);   /* uses anchor's width in modern browsers */
}
```

Also removed the broken `w-(--button-width)` class from `_dropdown_select.html` since the width is now handled via CSS.

### Why This Approach

- **CSS Anchor Positioning** is the modern way to link a popover's dimensions to its anchor element
- The **fallback width** (`15rem`) ensures reasonable behavior in browsers without full anchor positioning support
- By moving this to CSS rather than relying on the Tailwind Plus Elements library to set a CSS variable, we have direct control over the behavior

---

## 2025-12-27 (evening) - Real User/Org Context & Organization Switcher

### Summary

Replaced all hardcoded fake data in the app shell with real user and organization data from the database. Added working organization switcher.

### Changes

- **`get_app_shell_context()`**: Now takes `request` param and pulls real data (user name, email, initials, organizations)
- **Organization switcher**: Dropdown in sidebar now actually switches organizations via `/switch-organization/` endpoint
- **`current_organization` on User model**: Moved from session storage to a FK on User. Made it NOT NULL with `on_delete=PROTECT`
- **Removed avatar**: Replaced profile image with user initials in colored circle
- **Removed `get_current_organization()`**: Was just `return request.user.current_organization`

### Migrations

- `0005_add_current_organization_to_user.py`
- `0006_make_current_organization_required.py` (data migration + NOT NULL)

---

## 2025-12-27 - Backend Callback Endpoint & Infrastructure Refinements

### Summary

Completed the AWS account connection flow by implementing the backend API endpoint that receives callbacks from the Lambda. Also standardized naming conventions and improved Lambda logging.

### What We Built

#### Backend Callback Endpoint (`/api/aws/install-account-callback`)

Created `devopshero_app/views/api.py` with the endpoint that:
- Validates Bearer token authentication
- Validates `external_id` is a proper UUID (prevents Django 500 errors)
- Finds the `AWSAccount` record by `external_id`
- Updates status to `CONNECTED` on Create, handles Update/Delete appropriately
- Returns clean JSON responses for all error cases

#### Lambda Logging Fix

Replaced `print()` statements with Python's `logging` module. `print()` in Lambda can have buffering issues and doesn't reliably appear in CloudWatch. The logging module is the recommended approach.

### ngrok for Local Testing

Enabled ngrok tunneling (`https://devopshero.ngrok.io`) so the Lambda can call our local Django server:
- Added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
- Made OAuth redirect URI dynamic (builds from request host)

### Issues Encountered

- **UUID validation**: Passing invalid UUIDs to the endpoint caused Django 500 errors. Added explicit UUID validation before the database query.
- **WorkOS trailing slash**: WorkOS dashboard rejects redirect URIs with trailing slashes.

---

## 2025-12-26 - AWS Infrastructure Setup for Cross-Account Access

### Summary

Today we built the AWS infrastructure that allows DevOpsHero to connect to customer AWS accounts. The system uses CloudFormation to create IAM roles in customer accounts, with a callback mechanism to automatically notify DevOpsHero when a customer completes the setup.

### What We Built

#### 1. Install Callback Lambda (`cf_install_callback_lambda.json` + `install_callback_lambda.py`)

We created a Lambda function that acts as a CloudFormation Custom Resource handler. When a customer deploys our CloudFormation template in their AWS account, this Lambda is automatically invoked to notify the DevOpsHero backend.

**Why:** Without this callback, customers would have to manually provide their AWS Account ID after deploying the stack, and we'd have no confirmation the deployment actually succeeded. The callback automates this—CloudFormation itself tells us the deployment completed and provides the account ID and Role ARN directly.

#### 2. S3 Buckets (`cf_public_bucket.json` + `cf_private_bucket.json`)

We created two S3 buckets:

- **devopshero-public**: Hosts the customer-facing CloudFormation template (`cf_install_template.json`). Must be public so AWS Console can fetch it via the quick-create URL.
- **devopshero-private**: Stores the Lambda code zip file. Private because it contains internal implementation details.

Both buckets have versioning enabled for rollback capability.

#### 3. Customer Install Template (`cf_install_template.json`)

The CloudFormation template that customers deploy in their AWS accounts. It creates:
- An IAM role with `AdministratorAccess` that DevOpsHero can assume
- A custom resource that calls our callback Lambda

**Security:** Uses an `ExternalId` parameter to prevent confused deputy attacks. Each customer gets a unique ExternalId stored in our database.

#### 4. Deployment Scripts

- `run_devops_deployment.sh`: Master script that deploys all infrastructure in the correct order
- `upload_s3_files.sh`: Uploads Lambda code and install template to S3
- `update_install_callback_lambda.sh`: Quick script to update just the Lambda code

### Technical Decisions

**Why separate the Lambda code into a .py file?**
Originally the Python code was embedded in the CloudFormation template using `ZipFile`. Extracting it to `install_callback_lambda.py` makes the code easier to read, edit, and test. The tradeoff is we now need S3 to host the zip file.

**Why two buckets instead of one?**
Security principle of least privilege. The public bucket only contains the install template (which customers need to see anyway). The Lambda code stays private.

**Chicken-and-egg problem:**
The Lambda needs its code in S3, but S3 must exist first. We solved this by ordering the deployment script:
1. Create buckets
2. Upload files to S3
3. Deploy Lambda

### Issues We Encountered

1. **Invalid RetentionInDays**: CloudWatch Logs only accepts specific values (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, etc.). We tried 768, had to change to 731.

2. **ROLLBACK_COMPLETE state**: When a CloudFormation stack fails during creation, it enters this state and cannot be updated—only deleted. Added delete-and-wait logic to the deployment script.


### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    Customer's AWS Account                       │
│                                                                 │
│  CloudFormation Stack                                           │
│  ├── IAM Role (devopshero-{external_id})                        │
│  │   └── Allows DevOpsHero account to AssumeRole                │
│  └── Custom Resource ──────────────────────────────────────┐    │
│                                                            │    │
└────────────────────────────────────────────────────────────│────┘
                                                             │
                                                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   DevOpsHero AWS Account (555553041615)         │
│                                                                 │
│  ┌─────────────────┐    ┌──────────────────────────────────┐    │
│  │ S3 (public)     │    │ Lambda: devopshero-install-callback│  │
│  │ - install tpl   │    │                                    │  │
│  └─────────────────┘    │ Receives: AccountId, RoleArn,      │  │
│                         │           ExternalId, Region       │  │
│  ┌─────────────────┐    │                                    │  │
│  │ S3 (private)    │    │ Calls: DevOpsHero Backend API      │  │
│  │ - lambda code   │    └───────────────────────────────────┘   │
│  └─────────────────┘                                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### TODO

- ~~Implement the backend API endpoint `/api/aws/account-callback` to receive Lambda callbacks~~ ✅ Done (2025-12-27)
- Add error handling in the callback Lambda for network failures (retries?)
- Consider adding SNS notifications for failed stack deployments
- Test the full flow end-to-end with a real CloudFormation deployment
- Add CloudWatch alarms for Lambda errors (WE ARE MISSING CUSTOMERS!!!!)
- Document the customer onboarding flow
- Reduce IAM permissions from AdministratorAccess to least-privilege (later, once we know exactly what's needed)
- Rearchitecture DOH infra stack to use nested stacks, while solving the chicken and egg problem between S3 and 
  lambda code by keeping the private bucket in its own independent stack.
