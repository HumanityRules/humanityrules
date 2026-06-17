"""
MCP tools for the Claude Agent SDK.

This module wraps our domain-specific tools as MCP tools using the
@tool decorator from claude-agent-sdk. Tools receive the conversation
via closure from create_devopshero_mcp_server().
"""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server
from django.conf import settings

from devopshero_app.models import AWSAccount, AppPermissionRequest, Conversation, Repository, Workspace
from devopshero_app.services import permissions_service
from devopshero_app.services.gitproviders import repo_service

from .tools import (
    create_datastore as _create_datastore,
    provision_environment as _provision_environment,
    save_environment as _save_environment,
    save_app as _save_app,
    save_blueprint as _save_blueprint,
    deploy_blueprint as _deploy_blueprint,
    get_deployment_status as _get_deployment_status,
    get_environment_status as _get_environment_status,
    initiate_aws_connection as _initiate_aws_connection,
    list_apps as _list_apps,
    list_aws_accounts as _list_aws_accounts,
    list_environments as _list_environments,
    list_hosted_zones as _list_hosted_zones,
    list_repositories as _list_repositories,
    run_git_operation as _run_git_operation,
    scan_repository as _scan_repository,
    teardown_deployment as _teardown_deployment,
    test_docker_build as _test_docker_build,
    query_app_logs as _query_app_logs,
    lookup_access_denied_events as _lookup_access_denied_events,
)


async def _require_workspace(conversation: Conversation) -> Workspace:
    """Get workspace from conversation or raise helpful error."""
    # Check context_workspace_id (just an integer field, no DB query) to see if set
    if conversation.context_workspace_id is None:
        raise ValueError(
            "No workspace context for this conversation. "
            "Start a new conversation from a workspace page to set the context."
        )
    return await Workspace.objects.select_related("organization").aget(
        id=conversation.context_workspace_id,
        organization=conversation.organization,
    )


async def _require_aws_account(conversation: Conversation) -> AWSAccount:
    """Get AWS account from conversation or raise helpful error."""
    if conversation.context_aws_account_id is None:
        raise ValueError(
            "No AWS account context for this conversation. "
            "Start a new conversation from the environment setup flow to set the context."
        )
    return await AWSAccount.objects.aget(id=conversation.context_aws_account_id, organization=conversation.organization)


async def _require_app_permission_request(conversation: Conversation) -> AppPermissionRequest:
    """Get AppPermissionRequest from conversation or raise helpful error."""
    if conversation.context_app_permission_request_id is None:
        raise ValueError(
            "No permission request context for this conversation. "
            "Start a new conversation from the permissions editor to set the context."
        )
    return await AppPermissionRequest.objects.select_related(
        "app", "environment", "environment__aws_account",
    ).aget(
        id=conversation.context_app_permission_request_id,
        app__organization=conversation.organization,
    )


def _mcp_response(data: Any) -> dict[str, Any]:
    """Format data as MCP tool response."""
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    elif isinstance(data, list):
        data = [item.to_dict() if hasattr(item, "to_dict") else item for item in data]
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


# Mapping from full MCP tool names to human-friendly display names.
# MCP tools are namespaced (e.g., 'mcp__devopshero__list_aws_accounts') to avoid
# collisions between servers, but we want clean names for UI display.
TOOL_DISPLAY_NAMES = {
    # Platform tools
    "mcp__devopshero__list_aws_accounts": "List AWS Accounts",
    "mcp__devopshero__list_hosted_zones": "List Hosted Zones",
    "mcp__devopshero__list_environments": "List Environments",
    "mcp__devopshero__initiate_aws_connection": "Initiate AWS Connection",
    "mcp__devopshero__save_environment": "Save Environment",
    "mcp__devopshero__provision_environment": "Provision Environment",
    "mcp__devopshero__get_environment_status": "Get Environment Status",
    "mcp__devopshero__list_repositories": "List Repositories",
    "mcp__devopshero__scan_repository": "Scan Repository",
    # Workspace tools
    "mcp__devopshero__list_apps": "List Apps",
    "mcp__devopshero__create_datastore": "Create Datastore",
    "mcp__devopshero__save_app": "Save App",
    "mcp__devopshero__save_blueprint": "Save Blueprint",
    "mcp__devopshero__deploy_blueprint": "Deploy Blueprint",
    "mcp__devopshero__get_deployment_status": "Get Deployment Status",
    "mcp__devopshero__teardown_deployment": "Teardown Deployment",
    "mcp__devopshero__test_docker_build": "Test Docker Build",
    "mcp__devopshero__git_ops": "Git Ops",
    # Permissions
    "mcp__devopshero__query_app_logs": "Query App Logs",
    "mcp__devopshero__lookup_access_denied_events": "Lookup Access Denied Events",
    "mcp__devopshero__update_permission_draft": "Update Permission Draft",
    # Utility
    "mcp__devopshero__wait": "Wait",
}
def get_tool_display_name(full_name: str, parameters: dict | None) -> str:
    """Get the human-friendly display name for an MCP tool."""
    # Special handling for Task tool - derive display name from sub-agent type
    if full_name == "Task" and parameters:
        subagent_type = parameters.get("subagent_type", "Task")
        return subagent_type.replace("-", " ").title()

    return TOOL_DISPLAY_NAMES.get(full_name, full_name)


# Mapping from tool names to their "main" parameter for display in titles.
TOOL_INPUT_PARAMS_FOR_TITLE = {
    "mcp__devopshero__initiate_aws_connection": "account_name",
    "mcp__devopshero__list_hosted_zones": "aws_account_uuid",
    "mcp__devopshero__list_environments": "aws_account_uuid",
    "mcp__devopshero__save_environment": "environment_name",
    "mcp__devopshero__get_environment_status": "environment_id",
    "mcp__devopshero__scan_repository": "repository_id",
    "mcp__devopshero__create_datastore": "name",
    "mcp__devopshero__save_app": "name",
    "mcp__devopshero__save_blueprint": "environment_slug",
    "mcp__devopshero__get_deployment_status": "deployment_id",
    "mcp__devopshero__teardown_deployment": "app_id",
    "mcp__devopshero__git_ops": "action",
    "mcp__devopshero__query_app_logs": "time_window_hours",
    "mcp__devopshero__lookup_access_denied_events": "time_window_hours",
    "mcp__devopshero__update_permission_draft": "service",
    "mcp__devopshero__wait": "seconds",
    # External/Claude Agent SDK tools
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "Shell": "description",
    "Bash": "description",
    # Glob and Grep have special handling in get_tool_input_param_for_title
}

def get_tool_input_param_for_title(full_name: str, parameters: dict) -> str | None:
    """Extract and format the main parameter value for display."""
    if not parameters:
        return None

    # Special handling for search tools: show pattern and optionally path/directory
    if full_name == "Grep":
        return _format_grep_params(parameters)
    if full_name == "Glob":
        return _format_glob_params(parameters)

    param_name = TOOL_INPUT_PARAMS_FOR_TITLE.get(full_name)
    if not param_name or param_name not in parameters:
        return None

    return _format_input_param_title(tool_name=full_name, param_value=parameters[param_name])


def _format_input_param_title(tool_name: str, param_value: Any) -> str:
    """Format parameter value for display (sanitize paths, truncate UUIDs)."""
    value_str = sanitize_paths_for_display(str(param_value))

    if len(value_str) == 36 and value_str.count("-") == 4:
        return value_str[:8]

    if "wait" in tool_name:
        return f"{param_value}s"

    if tool_name in ("mcp__devopshero__query_app_logs", "mcp__devopshero__lookup_access_denied_events"):
        return f"Last {param_value} hours"

    return value_str


def _format_grep_params(parameters: dict) -> str | None:
    """Format Grep tool parameters for display."""
    pattern = parameters.get("pattern")
    if not pattern:
        return None

    # Truncate long patterns
    if len(pattern) > 30:
        pattern = pattern[:27] + "..."

    path = parameters.get("path")
    if path:
        # Extract just the filename or last path component
        path_display = path.rstrip("/").split("/")[-1]
        return f"{pattern} in {path_display}"

    return pattern


def _format_glob_params(parameters: dict) -> str | None:
    """Format Glob tool parameters for display."""
    pattern = parameters.get("pattern")
    if not pattern:
        return None

    # Truncate long patterns
    if len(pattern) > 30:
        pattern = pattern[:27] + "..."

    directory = parameters.get("path")
    if directory:
        # Extract just the last path component
        dir_display = directory.rstrip("/").split("/")[-1]
        return f"{pattern} in {dir_display}"

    return pattern


def _get_sandbox_prefix() -> str:
    """Build the sandbox path prefix including the context subdirectory glob.

    Sandbox paths look like: /sandbox-dir/conv-xxx/file.py
    The prefix pattern matches '/sandbox-dir/' plus the context subdirectory and its slash,
    so replacing it leaves just the relative path (e.g. 'file.py').
    """
    return str(settings.CLAUDE_SANDBOX_DIR).rstrip("/") + "/"


def sanitize_paths_for_display(obj: Any) -> Any:
    """Recursively strip sandbox path prefixes from strings in a data structure.

    Matches any occurrence of CLAUDE_SANDBOX_DIR within strings (not just at
    the start), and strips both the sandbox prefix and the context subdirectory
    that follows it (conv-xxx/, scan-xxx/, deployment-id/, etc.).
    Only active when SANITIZE_SANDBOX_PATHS is True.
    """
    if not settings.SANITIZE_SANDBOX_PATHS:
        return obj

    if isinstance(obj, str):
        prefix = _get_sandbox_prefix()
        if prefix not in obj:
            return obj
        return re.sub(re.escape(prefix) + r"[^/]+/", "", obj)

    if isinstance(obj, dict):
        return {k: sanitize_paths_for_display(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [sanitize_paths_for_display(item) for item in obj]

    return obj




# =============================================================================
# MCP Server Factory
# =============================================================================


def create_devopshero_mcp_server(conversation: Conversation):
    """Create an MCP server with tools scoped to a conversation via closures."""

    # =========================================================================
    # Platform Tools (always available, no workspace required)
    # =========================================================================

    @tool(
        "list_aws_accounts",
        (
            "List AWS accounts connected to the user's organization. "
            "Use this to find available deployment targets. "
            "Returns account ID, name, status, and region for each account."
        ),
        {},
    )
    async def list_aws_accounts(args: dict[str, Any]) -> dict[str, Any]:
        """List AWS accounts connected to the organization."""
        accounts = await _list_aws_accounts(organization=conversation.organization)
        return _mcp_response(accounts)

    @tool(
        "list_hosted_zones",
        (
            "List Route53 hosted zones (domains) in a connected AWS account. "
            "Use this to discover available domains for app configuration. "
            "Returns zone ID, domain name, and record count for each public hosted zone. "
            "The aws_account_uuid parameter is the internal UUID from list_aws_accounts (the 'id' field), "
            "not the 12-digit AWS account number."
        ),
        {
            "aws_account_uuid": str,
        },
    )
    async def list_hosted_zones(args: dict[str, Any]) -> dict[str, Any]:
        """List Route53 hosted zones in an AWS account."""
        zones = await _list_hosted_zones(
            aws_account_uuid=args["aws_account_uuid"],
            organization=conversation.organization,
        )
        return _mcp_response(zones)

    @tool(
        "list_environments",
        (
            "List environments in a connected AWS account. "
            "Environments contain the base infrastructure (VPC, ECS cluster, shared ALB) for deployments. "
            "Returns environment ID, name, slug, status, and shared ALB configuration. "
            "Use this to discover existing environments before deploying. "
            "Decision logic: if 'default' exists and is READY, use it; if none exist, create one; "
            "if multiple exist, ask the user which one to use."
        ),
        {
            "aws_account_uuid": str,
        },
    )
    async def list_environments(args: dict[str, Any]) -> dict[str, Any]:
        """List environments in an AWS account."""
        environments = await _list_environments(
            aws_account_uuid=args["aws_account_uuid"],
            organization=conversation.organization,
        )
        return _mcp_response(environments)

    @tool(
        "save_environment",
        (
            "Create or update the environment setup draft for the current AWS account. "
            "On first call, creates the Environment draft and pins it to the conversation. "
            "On subsequent calls, updates the existing draft or failed environment. "
            "Use this as soon as you know the name, region, and domain choice so the draft is visible "
            "before asking for final provisioning approval. "
            "Pass an empty hosted_zone_name for HTTP-only environments."
        ),
        {
            "type": "object",
            "properties": {
                "environment_name": {"type": "string", "description": "Human-readable name for the environment"},
                "aws_region": {"type": "string", "description": "AWS region for this environment, e.g. us-east-1"},
                "hosted_zone_name": {
                    "type": "string",
                    "description": "Route53 hosted zone for HTTPS. Use an empty string for HTTP-only.",
                },
            },
            "required": ["environment_name", "aws_region", "hosted_zone_name"],
        },
    )
    async def save_environment(args: dict[str, Any]) -> dict[str, Any]:
        """Create or update the environment setup draft."""
        aws_account = await _require_aws_account(conversation=conversation)
        result = await _save_environment(
            conversation=conversation,
            aws_account=aws_account,
            environment_name=args["environment_name"],
            aws_region=args["aws_region"],
            hosted_zone_name=args["hosted_zone_name"],
        )
        action = "created" if result.created else "updated"
        return _mcp_response({
            **result.to_dict(),
            "note": (
                f"Environment draft {action}. Review the saved draft with the user and wait for explicit "
                "confirmation before calling provision_environment."
            ),
        })

    @tool(
        "provision_environment",
        (
            "Queue provisioning for the conversation's saved environment draft. "
            "Provisioning creates the VPC, ECS cluster, and shared ALB infrastructure for that draft. "
            "IMPORTANT: Before calling this tool, you MUST review the saved draft with the user and wait "
            "for explicit confirmation. "
            "Returns immediately with PENDING status - use get_environment_status to poll for progress. "
            "Provisioning typically takes 5-10 minutes. "
            "Retry semantics: if the environment previously failed (ERROR status), update the draft with "
            "save_environment and then call this tool again."
        ),
        {},
    )
    async def provision_environment(args: dict[str, Any]) -> dict[str, Any]:
        """Provision an environment (queues provisioning)."""
        result = await _provision_environment(
            conversation=conversation,
            organization=conversation.organization,
        )
        return _mcp_response({
            **result.to_dict(),
            "note": (
                "Environment queued with PENDING status. The job worker will provision "
                "the infrastructure (VPC, ECS cluster, shared ALB). "
                "Use get_environment_status to check progress."
            ),
        })

    @tool(
        "initiate_aws_connection",
        (
            "Start the process of connecting a new AWS account. "
            "Creates a pending account record and returns a CloudFormation URL. "
            "The user must click the URL to deploy the stack in their AWS account."
        ),
        {
            "account_name": str,
        },
    )
    async def initiate_aws_connection(args: dict[str, Any]) -> dict[str, Any]:
        """Create a pending AWS account and return the CloudFormation URL."""
        result = await _initiate_aws_connection(
            account_name=args["account_name"],
            organization=conversation.organization,
            user=conversation.user,
        )
        return _mcp_response(result)

    @tool(
        "list_repositories",
        (
            "List repositories connected to the organization via GitHub integration. "
            "Returns repository names, clone URLs, default branches, and providers. "
            "Use this to discover available repositories for app deployment. "
            "If no repositories are found, the user may need to connect GitHub first "
            "via Settings > Git Integrations."
        ),
        {},
    )
    async def list_repositories(args: dict[str, Any]) -> dict[str, Any]:
        """List repositories connected to the organization."""
        repos = await _list_repositories(organization=conversation.organization)
        return _mcp_response(repos)

    @tool(
        "scan_repository",
        (
            "Quick scan of a repository to detect basic characteristics. "
            "Returns framework, language, Dockerfile info, suggested port, health path, "
            "detected database, and required environment variables. "
            "Use list_repositories first to get available repository IDs. "
            "For deep analysis, use the analyze-repository sub-agent instead."
        ),
        {
            "type": "object",
            "properties": {
                "repository_id": {
                    "type": "string",
                    "description": "Repository ID from list_repositories",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to scan. If not specified, uses the repository's default branch.",
                },
            },
            "required": ["repository_id"],
        },
    )
    async def scan_repository(args: dict[str, Any]) -> dict[str, Any]:
        """Quick scan of a repository's contents."""
        import uuid

        # Look up repository
        try:
            repository = await Repository.objects.select_related("integration").aget(
                id=args["repository_id"],
                organization=conversation.organization,
            )
        except Repository.DoesNotExist:
            raise ValueError(
                f"Repository {args['repository_id']} not found or doesn't belong to your organization."
            )

        # Use specified branch or fall back to repository's default
        branch = args.get("branch") or repository.default_branch

        # Clone the repository (run sync I/O in thread pool)
        cloned_repo_path = settings.CLAUDE_SANDBOX_DIR / f"scan-{uuid.uuid4().hex[:8]}"
        await asyncio.to_thread(
            repo_service.clone_repository,
            repository,
            branch,
            cloned_repo_path,
        )

        try:
            # Scan the cloned repository
            result = _scan_repository(repo_path=cloned_repo_path)
            return _mcp_response(result)
        finally:
            # Always clean up
            await asyncio.to_thread(repo_service.cleanup_repository, cloned_repo_path)

    # =========================================================================
    # Workspace Tools (require a pinned workspace)
    # =========================================================================

    @tool(
        "list_apps",
        (
            "List applications in the current workspace with their active deployments. "
            "Returns app ID, name, slug, type, branch, repository, and a list of deployments. "
            "Each deployment includes: environment name/slug, subdomain, hosted zone, status, and URL. "
            "Use this to see which environments an app is deployed to and check for potential domain conflicts."
        ),
        {},
    )
    async def list_apps(args: dict[str, Any]) -> dict[str, Any]:
        """List applications in the current workspace."""
        workspace = await _require_workspace(conversation)
        apps = await _list_apps(workspace=workspace)
        return _mcp_response(apps)

    @tool(
        "create_datastore",
        (
            "Create a managed database (datastore) in the selected workspace. "
            "Requires a workspace in the conversation context."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable name for the datastore"},
                "engine": {
                    "type": "string",
                    "enum": ["aurora-postgresql", "aurora-mysql"],
                    "description": "Database engine: aurora-postgresql or aurora-mysql",
                },
                "database_name": {"type": "string", "description": "Name of the database to create within the cluster"},
                "deployment_mode": {
                    "type": "string",
                    "enum": ["aurora_serverless_v2", "aurora_provisioned"],
                    "description": "aurora_serverless_v2 (recommended, auto-scales) or aurora_provisioned (fixed capacity)",
                },
                "serverless_min_acu": {
                    "type": "number",
                    "description": "Minimum ACU for serverless scaling (0.5-128). Only used with aurora_serverless_v2.",
                },
                "serverless_max_acu": {
                    "type": "number",
                    "description": "Maximum ACU for serverless scaling (0.5-128). Only used with aurora_serverless_v2.",
                },
            },
            "required": ["name", "engine", "database_name", "deployment_mode"],
        },
    )
    async def create_datastore(args: dict[str, Any]) -> dict[str, Any]:
        """Create a managed database in the selected workspace."""
        workspace = await _require_workspace(conversation)

        result = await _create_datastore(
            workspace=workspace,
            name=args["name"],
            engine=args["engine"],
            database_name=args["database_name"],
            user=conversation.user,
            deployment_mode=args.get("deployment_mode", "aurora_serverless_v2"),
            serverless_min_acu=args.get("serverless_min_acu", 0.5),
            serverless_max_acu=args.get("serverless_max_acu", 2.0),
        )
        return _mcp_response(result)

    @tool(
        "save_app",
        (
            "Create or update the application definition. "
            "On first call, creates the App and pins it to the conversation. "
            "On subsequent calls, updates the existing App. "
            "Use this as soon as repository analysis and clarifications give you enough information "
            "to save the app draft. Do not wait until the final deployment step."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable name for the app"},
                "app_type": {"type": "string", "enum": ["web", "worker", "scheduled"], "description": "Type of app"},
                "build_strategy": {"type": "string", "enum": ["dockerfile", "nixpacks", "buildpack"], "description": "How to build the container image"},
                "container_port": {"type": "integer", "description": "Port the container listens on (e.g., 8000)"},
                "health_check_path": {"type": "string", "description": "HTTP path for health checks (e.g., /health)"},
                "dockerfile_path": {"type": "string", "description": "Path to Dockerfile relative to repo root"},
                "health_check_command": {"type": "string", "description": "Health check command for non-HTTP health checks"},
                "repo_subpath": {"type": "string", "description": "Subdirectory within repository for monorepos"},
            },
            "required": ["name", "app_type", "build_strategy", "container_port", "health_check_path"],
        },
    )
    async def save_app(args: dict[str, Any]) -> dict[str, Any]:
        """Create or update the app definition."""
        workspace = await _require_workspace(conversation)

        repository_id = conversation.context_repository_id
        if not repository_id:
            raise ValueError(
                "No repository selected. Start the conversation from a workspace with a selected repository."
            )
        repository = await Repository.objects.aget(
            id=repository_id,
            organization=conversation.organization,
        )

        result = await _save_app(
            conversation=conversation,
            workspace=workspace,
            repository=repository,
            user=conversation.user,
            name=args["name"],
            app_type=args["app_type"],
            build_strategy=args["build_strategy"],
            container_port=args["container_port"],
            health_check_path=args["health_check_path"],
            dockerfile_path=args.get("dockerfile_path"),
            health_check_command=args.get("health_check_command"),
            repo_subpath=args.get("repo_subpath"),
        )

        action = "created" if result.created else "updated"
        return _mcp_response({
            **result.to_dict(),
            "note": f"App '{result.name}' {action}. Use save_blueprint next to configure deployment.",
        })

    @tool(
        "save_blueprint",
        (
            "Create or update a deployment blueprint for the current app. "
            "On first call, creates a draft blueprint for the specified environment. "
            "On subsequent calls, updates the existing blueprint. "
            "Requires save_app to have been called first. "
            "Use this as soon as you know the environment and core deployment settings so the draft is saved "
            "before asking for final deployment approval. "
            "For cpu: ECS CPU units (256=0.25vCPU, 512=0.5vCPU, 1024=1vCPU, 2048=2vCPU). "
            "For memory: MiB (512, 1024, 2048, 4096). "
            "For environment_variables: omit to keep existing, pass [] to clear, or [{\"name\": \"FOO\", \"value\": \"bar\"}] to replace. "
            "For app_secrets: omit to keep existing, pass {} to clear, or {\"key\": \"value\"} to replace. "
            "Use null values for auto-generated secrets (e.g., {\"SECRET_KEY\": null})."
        ),
        {
            "type": "object",
            "properties": {
                "environment_slug": {"type": "string", "description": "Target environment slug (required on first call)"},
                "branch": {"type": "string", "description": "Git branch override. Omit to use repository default."},
                "cpu": {"type": "integer", "description": "Fargate CPU units (256, 512, 1024, 2048)"},
                "memory": {"type": "integer", "description": "Fargate memory in MiB (512, 1024, 2048, 4096)"},
                "environment_variables": {"type": "array", "description": "List of {name, value} dicts. Omit to keep existing, [] to clear."},
                "app_secrets": {"type": "object", "description": "Dict of secret names to values. Omit to keep existing, {} to clear."},
                "datastore_id": {"type": "string", "description": "UUID of datastore to bind. Omit if app doesn't need a database."},
                "subdomain": {"type": "string", "description": "Route53 subdomain override. Defaults to app slug."},
            },
            "required": [],
        },
    )
    async def save_blueprint(args: dict[str, Any]) -> dict[str, Any]:
        """Create or update a deployment blueprint."""
        workspace = await _require_workspace(conversation)

        try:
            result = await _save_blueprint(
                conversation=conversation,
                workspace=workspace,
                user=conversation.user,
                environment_slug=args.get("environment_slug"),
                branch=args.get("branch"),
                cpu=args.get("cpu"),
                memory=args.get("memory"),
                environment_variables=args.get("environment_variables"),
                app_secrets=args.get("app_secrets"),
                datastore_id=args.get("datastore_id"),
                subdomain=args.get("subdomain"),
            )
        except ValueError as e:
            return _mcp_response({
                "error": str(e),
                "app_id": str(conversation.context_app_id) if conversation.context_app_id else None,
            })

        action = "created" if result.created else "updated"
        return _mcp_response({
            **result.to_dict(),
            "note": (
                f"Blueprint {action}. Review the saved draft with the user and wait for explicit "
                "confirmation before calling deploy_blueprint."
            ),
        })

    @tool(
        "deploy_blueprint",
        (
            "Trigger deployment of the current blueprint. "
            "Creates a pending deployment from the blueprint configuration. "
            "The job worker will execute the deployment workflow. "
            "Use get_deployment_status to check progress. "
            "Requires save_app and save_blueprint to have been called first. "
            "CRITICAL: only call this after reviewing the saved draft with the user and receiving "
            "explicit confirmation to deploy."
        ),
        {},
    )
    async def deploy_blueprint_tool(args: dict[str, Any]) -> dict[str, Any]:
        """Trigger deployment from the current blueprint."""
        result = await _deploy_blueprint(conversation=conversation)

        return _mcp_response({
            **result.to_dict(),
            "note": (
                f"Deployment queued for '{result.app_name}' in '{result.environment_name}'. "
                "The job worker will process it. Use get_deployment_status to check progress."
            ),
        })

    @tool(
        "get_deployment_status",
        (
            "Get the current status of a deployment including progress and logs. "
            "Returns the deployment phase, percentage complete, and recent log entries. "
            "Use this to monitor deployment progress and report status to users."
        ),
        {
            "deployment_id": str,
        },
    )
    async def get_deployment_status(args: dict[str, Any]) -> dict[str, Any]:
        """Get current deployment status and recent logs."""
        result = await _get_deployment_status(
            deployment_id=args["deployment_id"],
            organization=conversation.organization,
            log_limit=10,
        )
        return _mcp_response(result)

    @tool(
        "teardown_deployment",
        (
            "Tear down (destroy) a deployed application. "
            "Deletes all app-specific AWS infrastructure: ECS service, ALB listener rules, "
            "Aurora database (if any), and ECR repository. "
            "The app must be in SUCCEEDED or FAILED state. Cannot teardown in-progress deployments. "
            "Use get_deployment_status to monitor teardown progress."
        ),
        {
            "app_id": str,
        },
    )
    async def teardown_deployment(args: dict[str, Any]) -> dict[str, Any]:
        """Queue teardown of an application's deployment."""
        result = await _teardown_deployment(
            app_id=args["app_id"],
            organization=conversation.organization,
            user=conversation.user,
        )

        return _mcp_response({
            **result.to_dict(),
            "note": (
                "Teardown queued. The job worker will delete the app's CDK stacks "
                "(ECS service, ALB rules, ECR repository, and Aurora if applicable). "
                "Use get_deployment_status to check progress."
            ),
        })

    @tool(
        "test_docker_build",
        (
            "Test-build the Dockerfile in the conversation's repository sandbox. "
            "Build-only — does not push to ECR. "
            "Use this after generating a Dockerfile to verify it builds successfully before deploying. "
            "Returns success/failure and the build output (truncated to last 80 lines)."
        ),
        {
            "environment_slug": str,
        },
    )
    async def test_docker_build(args: dict[str, Any]) -> dict[str, Any]:
        """Run a test Docker build for the Dockerfile in the sandbox."""
        result = await _test_docker_build(
            conversation_id=conversation.id,
            organization=conversation.organization,
            environment_slug=args["environment_slug"],
        )
        return _mcp_response({"success": result.success, "build_output": result.build_output})

    @tool(
        "git_ops",
        (
            "Run git operations in the conversation sandbox using DOH-managed repository credentials. "
            "Supports: status, diff, log, create_branch, stage_files, commit, push_branch, "
            "create_pull_request, update_pull_request, get_pull_request, and get_remote_info. "
            "For GitHub push and PR actions, authentication uses the repository's GitHub App "
            "installation token from DOH data, not local machine credentials."
        ),
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "status",
                        "diff",
                        "log",
                        "create_branch",
                        "stage_files",
                        "commit",
                        "push_branch",
                        "create_pull_request",
                        "update_pull_request",
                        "get_pull_request",
                        "get_remote_info",
                    ],
                    "description": "Git action to execute.",
                },
                "include_staged": {
                    "type": "boolean",
                    "description": "For diff action: if true, shows staged diff (git diff --cached).",
                },
                "limit": {
                    "type": "integer",
                    "description": "For log action: number of commits to show (1-50).",
                },
                "branch_name": {
                    "type": "string",
                    "description": "For create_branch and push_branch actions.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "For stage_files action: repository-relative paths to stage.",
                },
                "commit_message": {
                    "type": "string",
                    "description": "For commit action.",
                },
                "title": {
                    "type": "string",
                    "description": "For create_pull_request and update_pull_request actions.",
                },
                "body": {
                    "type": "string",
                    "description": "For create_pull_request and update_pull_request actions.",
                },
                "head_branch": {
                    "type": "string",
                    "description": "For create_pull_request action. Defaults to current branch.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "For create_pull_request action. Defaults to repository default branch.",
                },
                "pull_number": {
                    "type": "integer",
                    "description": "For update_pull_request and get_pull_request actions.",
                },
            },
            "required": ["action"],
        },
    )
    async def git_ops(args: dict[str, Any]) -> dict[str, Any]:
        """Run one git operation for the conversation's selected repository."""
        repository_id = conversation.context_repository_id
        if not repository_id:
            raise ValueError(
                "No repository selected. Start the conversation from a workspace with a selected repository."
            )

        try:
            repository = await Repository.objects.select_related("integration").aget(
                id=repository_id,
                organization=conversation.organization,
            )
        except Repository.DoesNotExist:
            raise ValueError(f"Repository {repository_id} not found in organization.")

        result = await _run_git_operation(
            conversation_id=conversation.id,
            repository=repository,
            action=args["action"],
            parameters=args,
        )
        return _mcp_response(result)

    @tool(
        "get_environment_status",
        (
            "Get the current status of an environment including provisioning progress and logs. "
            "Returns the environment status, configuration, and recent log entries. "
            "Use this to monitor environment provisioning progress."
        ),
        {
            "environment_id": str,
        },
    )
    async def get_environment_status(args: dict[str, Any]) -> dict[str, Any]:
        """Get current environment status and recent logs."""
        result = await _get_environment_status(
            environment_id=args["environment_id"],
            organization=conversation.organization,
            log_limit=10,
        )
        return _mcp_response(result)

    # =========================================================================
    # Permissions Tools (require an AppPermissionRequest context)
    # =========================================================================

    @tool(
        "query_app_logs",
        (
            "Query CloudWatch Logs for permission-related errors in the app's ECS log group. "
            "Searches for patterns like AccessDenied and authorization errors. "
            "Results are compacted: duplicate errors are grouped by signature with a count and time range. "
            "Use this to detect runtime permission denials from application logs."
        ),
        {
            "type": "object",
            "properties": {
                "time_window_hours": {
                    "type": "integer",
                    "description": "How many hours back to search (1-168, default 24).",
                },
            },
            "required": [],
        },
    )
    async def query_app_logs(args: dict[str, Any]) -> dict[str, Any]:
        """Query CloudWatch Logs for permission errors."""
        apr = await _require_app_permission_request(conversation)
        result = await _query_app_logs(
            apr=apr,
            time_window_hours=args.get("time_window_hours", 24),
        )
        return _mcp_response(result)

    @tool(
        "lookup_access_denied_events",
        (
            "Look up CloudTrail AccessDenied management events for the app's ECS task role. "
            "Searches recent CloudTrail events for permission denials attributed to the task role. "
            "Note: only covers management events (e.g., CreateBucket, PutQueuePolicy). "
            "Data events (S3 GetObject, DynamoDB PutItem) require separate CloudTrail data event logging. "
            "CloudTrail events may be delayed by up to 15 minutes."
        ),
        {
            "type": "object",
            "properties": {
                "time_window_hours": {
                    "type": "integer",
                    "description": "How many hours back to search (1-2160 / 90 days, default 24).",
                },
            },
            "required": [],
        },
    )
    async def lookup_access_denied_events(args: dict[str, Any]) -> dict[str, Any]:
        """Look up CloudTrail AccessDenied events for the app's task role."""
        apr = await _require_app_permission_request(conversation)
        result = await _lookup_access_denied_events(
            apr=apr,
            time_window_hours=args.get("time_window_hours", 24),
        )
        return _mcp_response(result)

    @tool(
        "update_permission_draft",
        (
            "Add or update a permission statement in the draft policy. "
            "A statement is identified by its (service, resource ARNs) pair: if a statement for this "
            "service already covers exactly the given resources, the access levels are merged into it; "
            "otherwise a new statement is created. Call this multiple times with different resource sets "
            "to grant distinct scopes for one service (e.g. 'List' on '*' and 'Read' on specific table ARNs). "
            "Optionally provide a description that explains why these permissions are needed — "
            "it will be appended to any existing description the user has already written. "
            "Use this tool proactively when you identify missing permissions from source code analysis, "
            "CloudWatch Logs, or CloudTrail. The draft must be in 'draft' status."
        ),
        {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": "AWS service prefix (e.g., 's3', 'dynamodb', 'sqs').",
                },
                "access_levels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Access levels to grant: 'Read', 'Write', 'List', 'Tagging', 'Permissions management'.",
                },
                "resources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "AWS resource ARNs to grant access to (e.g., 'arn:aws:s3:::my-bucket').",
                },
                "description": {
                    "type": "string",
                    "description": "Rationale for these permissions — appended to the existing description.",
                },
            },
            "required": ["service", "access_levels", "resources"],
        },
    )
    async def update_permission_draft(args: dict[str, Any]) -> dict[str, Any]:
        """Add or update a permission statement in the draft policy."""
        app_permission_request = await _require_app_permission_request(conversation)
        if app_permission_request.status != "draft":
            raise ValueError(f"Cannot modify permission request in '{app_permission_request.status}' status. Only draft requests can be edited.")

        await permissions_service.aupsert_statement(
            app_permission_request, args["service"], args["access_levels"], args["resources"],
        )

        description_text = args.get("description", "").strip()
        if description_text:
            await permissions_service.amerge_description(app_permission_request, description_text)

        return _mcp_response({"success": True, "app_permission_request_id": str(app_permission_request.id), "statements": app_permission_request.statements})

    # =========================================================================
    # Utility Tools
    # =========================================================================

    @tool(
        "wait",
        "Wait for a specified number of seconds. Useful for debugging streaming UI.",
        {"seconds": int},
    )
    async def wait(args: dict[str, Any]) -> dict[str, Any]:
        """Wait for n seconds (default 2)."""
        seconds = args.get("seconds", 2)
        await asyncio.sleep(seconds)
        return _mcp_response({"waited": seconds})

    # =========================================================================
    # MCP Server Configuration
    # =========================================================================

    server = create_sdk_mcp_server(
        name="devopshero",
        version="1.0.0",
        tools=[
            # Platform tools
            list_aws_accounts,
            list_hosted_zones,
            list_environments,
            initiate_aws_connection,
            save_environment,
            provision_environment,
            get_environment_status,
            list_repositories,
            scan_repository,
            # Workspace tools
            list_apps,
            create_datastore,
            save_app,
            save_blueprint,
            deploy_blueprint_tool,
            get_deployment_status,
            teardown_deployment,
            test_docker_build,
            git_ops,
            # Permissions
            query_app_logs,
            lookup_access_denied_events,
            update_permission_draft,
            # Utility
            wait,
        ],
    )
    return server


# Tool names for use in allowed_tools configuration
TOOL_NAMES = [
    # Platform tools
    "mcp__devopshero__list_aws_accounts",
    "mcp__devopshero__list_hosted_zones",
    "mcp__devopshero__list_environments",
    "mcp__devopshero__initiate_aws_connection",
    "mcp__devopshero__save_environment",
    "mcp__devopshero__provision_environment",
    "mcp__devopshero__get_environment_status",
    "mcp__devopshero__list_repositories",
    # Workspace tools
    "mcp__devopshero__list_apps",
    "mcp__devopshero__create_datastore",
    "mcp__devopshero__save_app",
    "mcp__devopshero__save_blueprint",
    "mcp__devopshero__deploy_blueprint",
    "mcp__devopshero__get_deployment_status",
    "mcp__devopshero__teardown_deployment",
    "mcp__devopshero__test_docker_build",
    "mcp__devopshero__git_ops",
    "mcp__devopshero__query_app_logs",
    # Utility
    "mcp__devopshero__wait",
]


# Subset of MCP tools allowed in ENVIRONMENT_SETUP mode
ENVIRONMENT_ALLOWED_TOOLS = [
    "mcp__devopshero__list_hosted_zones",
    "mcp__devopshero__list_environments",
    "mcp__devopshero__save_environment",
    "mcp__devopshero__provision_environment",
    "mcp__devopshero__get_environment_status",
    "mcp__devopshero__wait",
]


# Subset of MCP tools allowed in PERMISSIONS mode
PERMISSIONS_ALLOWED_TOOLS = [
    "mcp__devopshero__query_app_logs",
    "mcp__devopshero__lookup_access_denied_events",
    "mcp__devopshero__update_permission_draft",
    "mcp__devopshero__wait",
]
