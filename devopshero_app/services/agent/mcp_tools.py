"""
MCP tools for the Claude Agent SDK.

This module wraps our domain-specific tools as MCP tools using the
@tool decorator from claude-agent-sdk. Tools access Django context
via contextvars to maintain request isolation.
"""

import asyncio
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server
from django.conf import settings

from devopshero_app.models import Conversation, Repository, Workspace
from devopshero_app.services.github import repo_service

from .tools import (
    create_app as _create_app,
    create_datastore as _create_datastore,
    create_environment as _create_environment,
    deploy_app as _deploy_app,
    get_deployment_status as _get_deployment_status,
    get_environment_status as _get_environment_status,
    initiate_aws_connection as _initiate_aws_connection,
    list_apps as _list_apps,
    list_aws_accounts as _list_aws_accounts,
    list_environments as _list_environments,
    list_hosted_zones as _list_hosted_zones,
    list_repositories as _list_repositories,
    scan_repository as _scan_repository,
    teardown_deployment as _teardown_deployment,
)


# Context variable for the current conversation
# Set before processing, accessed by tools
conversation_context: ContextVar[Conversation | None] = ContextVar(
    "conversation_context", default=None
)


def _get_conversation() -> Conversation:
    """Get the current conversation from context."""
    conversation = conversation_context.get()
    if conversation is None:
        raise RuntimeError(
            "No conversation context set. "
            "Ensure conversation_context.set() is called before processing."
        )
    return conversation


async def _require_workspace(conversation: Conversation) -> Workspace:
    """Get workspace from conversation or raise helpful error."""
    # Check context_workspace_id (just an integer field, no DB query) to see if set
    if conversation.context_workspace_id is None:
        raise ValueError(
            "No workspace context for this conversation. "
            "Start a new conversation from a workspace page to set the context."
        )
    # Use async ORM to fetch the related workspace (with organization for create_app)
    return await Workspace.objects.select_related("organization").aget(id=conversation.context_workspace_id)


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
    "mcp__devopshero__create_environment": "Create Environment",
    "mcp__devopshero__get_environment_status": "Get Environment Status",
    "mcp__devopshero__list_repositories": "List Repositories",
    "mcp__devopshero__scan_repository": "Scan Repository",
    # Workspace tools
    "mcp__devopshero__list_apps": "List Apps",
    "mcp__devopshero__create_app": "Create App",
    "mcp__devopshero__create_datastore": "Create Datastore",
    "mcp__devopshero__deploy_app": "Deploy App",
    "mcp__devopshero__get_deployment_status": "Get Deployment Status",
    "mcp__devopshero__teardown_deployment": "Teardown Deployment",
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
TOOL_MAIN_PARAMS = {
    "mcp__devopshero__initiate_aws_connection": "account_name",
    "mcp__devopshero__list_hosted_zones": "aws_account_uuid",
    "mcp__devopshero__list_environments": "aws_account_uuid",
    "mcp__devopshero__create_environment": "environment_name",
    "mcp__devopshero__get_environment_status": "environment_id",
    "mcp__devopshero__scan_repository": "repository_id",
    "mcp__devopshero__create_app": "name",
    "mcp__devopshero__create_datastore": "name",
    "mcp__devopshero__deploy_app": "app_name",
    "mcp__devopshero__get_deployment_status": "deployment_id",
    "mcp__devopshero__teardown_deployment": "app_id",
    "mcp__devopshero__wait": "seconds",
    # External/Claude Agent SDK tools
    "Read": "file_path",
    "Shell": "description",
    "Bash": "description",
    # Glob and Grep have special handling in get_tool_main_param
}


def get_tool_main_param(full_name: str, parameters: dict) -> str | None:
    """Extract and format the main parameter value for display."""
    if not parameters:
        return None

    # Special handling for search tools: show pattern and optionally path/directory
    if full_name == "Grep":
        return _format_grep_params(parameters)
    if full_name == "Glob":
        return _format_glob_params(parameters)

    main_param = TOOL_MAIN_PARAMS.get(full_name)
    if not main_param or main_param not in parameters:
        return None

    value = parameters[main_param]
    return _format_param_value(full_name, value)


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


def _relativize_sandbox_path(path_str: str) -> str:
    """Relativize a path within the sandbox directory for display.

    Strips the sandbox directory prefix and the first subdirectory (which is the
    context-specific folder like conv-xxx, scan-xxx, or deployment-id).

    Paths outside the sandbox are returned unchanged (security signal).
    """
    try:
        path = Path(path_str)
        sandbox = settings.CLAUDE_SANDBOX_DIR

        # Check if path is under sandbox
        if not path_str.startswith(str(sandbox)):
            return path_str

        # Get path relative to sandbox
        relative = path.relative_to(sandbox)
        parts = relative.parts

        # If empty or just the context dir, return "."
        if len(parts) == 0:
            return "."
        if len(parts) == 1:
            return "."

        # Strip the first part (conv-xxx, scan-xxx, deployment-id, etc.)
        return str(Path(*parts[1:]))

    except (ValueError, TypeError):
        # relative_to raises ValueError if path is not under sandbox
        return path_str


def sanitize_paths_for_display(obj: Any) -> Any:
    """Recursively sanitize sandbox paths in a data structure for display.

    Walks through dicts, lists, and strings, relativizing any paths that
    are under CLAUDE_SANDBOX_DIR.
    """
    if isinstance(obj, str):
        # Only attempt to relativize if it looks like an absolute path
        if obj.startswith("/"):
            return _relativize_sandbox_path(obj)
        return obj

    if isinstance(obj, dict):
        return {k: sanitize_paths_for_display(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [sanitize_paths_for_display(item) for item in obj]

    return obj


def _format_param_value(tool_name: str, value: Any) -> str:
    """Format parameter value for display (extract repo names, truncate UUIDs)."""
    value_str = str(value)

    # Relativize sandbox paths
    if value_str.startswith("/"):
        value_str = _relativize_sandbox_path(value_str)

    # Truncate UUIDs (36 chars with dashes)
    if len(value_str) == 36 and value_str.count("-") == 4:
        return value_str[:8]

    # Format seconds
    if "wait" in tool_name:
        return f"{value}s"

    return value_str


# =============================================================================
# Platform Tools (always available, no workspace required)
# =============================================================================


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
    conversation = _get_conversation()
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
    conversation = _get_conversation()
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
    conversation = _get_conversation()
    environments = await _list_environments(
        aws_account_uuid=args["aws_account_uuid"],
        organization=conversation.organization,
    )
    return _mcp_response(environments)


@tool(
    "create_environment",
    (
        "Create an environment in a connected AWS account. "
        "Queues provisioning of VPC, ECS cluster, and shared ALB infrastructure. "
        "IMPORTANT: Before calling this tool, confirm the AWS region with the user. "
        "The default is us-east-1, but this cannot be changed after provisioning. "
        "Tell the user: 'I'll create the environment in us-east-1. Let me know if you need a different region.' "
        "If hosted_zone_name is provided, also creates a wildcard SSL certificate for HTTPS. "
        "Returns immediately with PENDING status - use get_environment_status to poll for progress. "
        "Provisioning typically takes 5-10 minutes. "
        "The aws_account_uuid is the internal UUID from list_aws_accounts (the 'id' field), "
        "not the 12-digit AWS account number."
    ),
    {
        "aws_account_uuid": str,
        "environment_name": str,
        "aws_region": str,
        "hosted_zone_name": str,
    },
)
async def create_environment(args: dict[str, Any]) -> dict[str, Any]:
    """Create an environment (queues provisioning)."""
    conversation = _get_conversation()
    result = await _create_environment(
        aws_account_uuid=args["aws_account_uuid"],
        environment_name=args["environment_name"],
        aws_region=args.get("aws_region", "us-east-1"),
        hosted_zone_name=args.get("hosted_zone_name"),
        organization=conversation.organization,
        user=conversation.user,
    )
    return _mcp_response({
        **result.to_dict(),
        "note": (
            "Environment created with PENDING status. The job worker will provision "
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
    conversation = _get_conversation()
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
    conversation = _get_conversation()
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
        "repository_id": str,
        "branch": str,
    },
)
async def scan_repository(args: dict[str, Any]) -> dict[str, Any]:
    """Quick scan of a repository's contents."""
    import uuid

    conversation = _get_conversation()

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

    # Clone the repository (run sync I/O in thread pool)
    clone_id = f"scan-{uuid.uuid4().hex[:8]}"
    repo_path = await asyncio.to_thread(
        repo_service.clone_repository,
        repository,
        args["branch"],
        clone_id,
    )

    try:
        # Scan the cloned repository
        result = _scan_repository(repo_path=repo_path)
        return _mcp_response(result)
    finally:
        # Always clean up
        await asyncio.to_thread(repo_service.cleanup_repository, repo_path)


# =============================================================================
# Workspace Tools (require a pinned workspace)
# =============================================================================


@tool(
    "list_apps",
    (
        "List applications in the current workspace. "
        "Use this to discover existing apps before creating new ones. "
        "If an app with the desired name already exists, use deploy_app with its ID "
        "instead of calling create_app (which would create a duplicate with a numeric suffix). "
        "Returns app ID, name, slug, type, branch, repository, and latest deployment status."
    ),
    {},
)
async def list_apps(args: dict[str, Any]) -> dict[str, Any]:
    """List applications in the current workspace."""
    conversation = _get_conversation()
    workspace = await _require_workspace(conversation)
    apps = await _list_apps(workspace=workspace)
    return _mcp_response(apps)


@tool(
    "create_app",
    (
        "Create an application configuration in the selected workspace. "
        "This defines how an app will be built and deployed. "
        "Requires a workspace and repository in the conversation context. "
        "For environment_variables, pass an array of objects with 'name' and 'value' keys, "
        "e.g., [{\"name\": \"API_KEY\", \"value\": \"secret\"}]. Pass [] if no env vars needed. "
        "For app_secrets, pass a dict mapping secret field names to values. "
        "Use null for auto-generated secrets, e.g., {\"secret_key_base\": null, \"api_token\": \"disabled\"}."
    ),
    {
        "name": str,
        "branch": str,
        "app_type": str,
        "build_strategy": str,
        "container_port": int,
        "cpu": int,
        "memory": int,
        "health_check_path": str,
        "environment_variables": list,
        "datastore_id": str,
        "dockerfile_path": str,
        "app_secrets": dict,
    },
)
async def create_app(args: dict[str, Any]) -> dict[str, Any]:
    """Create an application configuration in the selected workspace."""
    conversation = _get_conversation()
    workspace = await _require_workspace(conversation)

    repository_id = conversation.context_repository_id
    if not repository_id:
        raise ValueError(
            "No repository selected. Start the conversation from a workspace with a selected repository."
        )
    
    try:
        repository = await Repository.objects.aget(
            id=repository_id,
            organization=conversation.organization,
        )
    except Repository.DoesNotExist:
        raise ValueError(f"Repository {repository_id} not found in organization.")

    result = await _create_app(
        workspace=workspace,
        repository=repository,
        name=args["name"],
        branch=args["branch"],
        app_type=args["app_type"],
        build_strategy=args["build_strategy"],
        container_port=args["container_port"],
        cpu=args["cpu"],
        memory=args["memory"],
        health_check_path=args["health_check_path"],
        user=conversation.user,
        environment_variables=args.get("environment_variables"),
        datastore_id=args.get("datastore_id"),
        dockerfile_path=args.get("dockerfile_path", ""),
        app_secrets=args.get("app_secrets"),
    )
    return _mcp_response(result)


@tool(
    "create_datastore",
    (
        "Create a managed database (datastore) in the selected workspace. "
        "Supports Aurora PostgreSQL and Aurora MySQL with Serverless v2 scaling. "
        "Requires a workspace in the conversation context."
    ),
    {
        "name": str,
        "engine": str,
        "database_name": str,
        "deployment_mode": str,
        "serverless_min_acu": float,
        "serverless_max_acu": float,
    },
)
async def create_datastore(args: dict[str, Any]) -> dict[str, Any]:
    """Create a managed database in the selected workspace."""
    conversation = _get_conversation()
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
    "deploy_app",
    (
        "Deploy an application to AWS infrastructure. "
        "Creates a deployment record and triggers the deployment process. "
        "The job worker will build the Docker image, push to ECR, "
        "and deploy via CDK. Use get_deployment_status to check progress. "
        "For environment_slug, always use 'default'."
    ),
    {
        "app_id": str,
        "git_ref": str,
        "environment_slug": str,
    },
)
async def deploy_app(args: dict[str, Any]) -> dict[str, Any]:
    """Create a deployment for an application."""
    conversation = _get_conversation()

    result = await _deploy_app(
        app_id=args["app_id"],
        git_ref=args["git_ref"],
        organization=conversation.organization,
        user=conversation.user,
        environment_slug=args["environment_slug"],
    )

    # Link deployment to conversation via M2M
    from devopshero_app.models import Deployment
    deployment = await Deployment.objects.aget(id=result.id)
    await conversation.deployments.aadd(deployment)

    return _mcp_response({
        **result.to_dict(),
        "note": (
            "Deployment created. The job worker will pick it up "
            "and execute the build/push/deploy pipeline. "
            "Use get_deployment_status to check progress."
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
    conversation = _get_conversation()

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
        "The app must be in RUNNING or FAILED state. Cannot teardown in-progress deployments. "
        "Use get_deployment_status to monitor teardown progress."
    ),
    {
        "app_id": str,
    },
)
async def teardown_deployment(args: dict[str, Any]) -> dict[str, Any]:
    """Queue teardown of an application's deployment."""
    conversation = _get_conversation()

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
    conversation = _get_conversation()

    result = await _get_environment_status(
        environment_id=args["environment_id"],
        organization=conversation.organization,
        log_limit=10,
    )
    return _mcp_response(result)


# =============================================================================
# Utility Tools
# =============================================================================


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


# =============================================================================
# MCP Server Configuration
# =============================================================================


# Create the MCP server with all tools
devopshero_mcp_server = create_sdk_mcp_server(
    name="devopshero",
    version="1.0.0",
    tools=[
        # Platform tools
        list_aws_accounts,
        list_hosted_zones,
        list_environments,
        initiate_aws_connection,
        create_environment,
        get_environment_status,
        list_repositories,
        scan_repository,
        # Workspace tools
        list_apps,
        create_app,
        create_datastore,
        deploy_app,
        get_deployment_status,
        teardown_deployment,
        # Utility
        wait,
    ],
)

# Tool names for use in allowed_tools configuration
TOOL_NAMES = [
    # Platform tools
    "mcp__devopshero__list_aws_accounts",
    "mcp__devopshero__list_hosted_zones",
    "mcp__devopshero__list_environments",
    "mcp__devopshero__initiate_aws_connection",
    "mcp__devopshero__create_environment",
    "mcp__devopshero__get_environment_status",
    "mcp__devopshero__list_repositories",
    "mcp__devopshero__scan_repository",
    # Workspace tools
    "mcp__devopshero__list_apps",
    "mcp__devopshero__create_app",
    "mcp__devopshero__create_datastore",
    "mcp__devopshero__deploy_app",
    "mcp__devopshero__get_deployment_status",
    "mcp__devopshero__teardown_deployment",
    # Utility
    "mcp__devopshero__wait",
]
