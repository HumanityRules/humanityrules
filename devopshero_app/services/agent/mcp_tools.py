"""
MCP tools for the Claude Agent SDK.

This module wraps our domain-specific tools as MCP tools using the
@tool decorator from claude-agent-sdk. Tools access Django context
via contextvars to maintain request isolation.
"""

import asyncio
import json
from contextvars import ContextVar
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from devopshero_app.models import Conversation, Workspace

from .tools import (
    create_app as _create_app,
    create_datastore as _create_datastore,
    create_workspace as _create_workspace,
    deploy_app as _deploy_app,
    get_deployment_status as _get_deployment_status,
    initiate_aws_connection as _initiate_aws_connection,
    list_aws_accounts as _list_aws_accounts,
    list_deployable_repos as _list_deployable_repos,
    list_workspaces as _list_workspaces,
    scan_repository as _scan_repository,
    select_workspace as _select_workspace,
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


def _require_workspace(conversation: Conversation) -> Workspace:
    """Get workspace from conversation or raise helpful error."""
    if conversation.workspace is None:
        raise ValueError(
            "No workspace selected for this conversation. "
            "Use select_workspace to choose a workspace first, "
            "or create_workspace if you don't have one yet."
        )
    return conversation.workspace


def _mcp_response(data: Any) -> dict[str, Any]:
    """Format data as MCP tool response."""
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    elif isinstance(data, list):
        data = [item.to_dict() if hasattr(item, "to_dict") else item for item in data]
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


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
    "list_workspaces",
    (
        "List all workspaces in the user's organization. "
        "Returns workspace names, repository URLs, and AWS configuration. "
        "Use this to find a workspace to select before creating apps."
    ),
    {},
)
async def list_workspaces(args: dict[str, Any]) -> dict[str, Any]:
    """List all workspaces in the organization."""
    conversation = _get_conversation()
    workspaces = await _list_workspaces(organization=conversation.organization)
    return _mcp_response(workspaces)


@tool(
    "select_workspace",
    (
        "Select a workspace for this conversation. "
        "Once selected, the workspace is pinned and cannot be changed. "
        "All subsequent app and deployment operations will use this workspace."
    ),
    {
        "workspace_id": str,
    },
)
async def select_workspace(args: dict[str, Any]) -> dict[str, Any]:
    """Pin a workspace to this conversation."""
    conversation = _get_conversation()
    result = await _select_workspace(
        workspace_id=args["workspace_id"],
        conversation=conversation,
        organization=conversation.organization,
    )
    return _mcp_response(result)


@tool(
    "create_workspace",
    (
        "Create a new workspace for organizing applications and deployments. "
        "A workspace binds a repository to an AWS account and region. "
        "You must have at least one connected AWS account to create a workspace. "
        "The primary_repo_url is required and must be a file:// URL."
    ),
    {
        "name": str,
        "aws_account_id": str,
        "aws_region": str,
        "primary_repo_url": str,
        "description": str,
    },
)
async def create_workspace(args: dict[str, Any]) -> dict[str, Any]:
    """Create a new workspace in the organization."""
    conversation = _get_conversation()
    result = await _create_workspace(
        name=args["name"],
        aws_account_id=args["aws_account_id"],
        aws_region=args["aws_region"],
        organization=conversation.organization,
        user=conversation.user,
        description=args.get("description", ""),
        primary_repo_url=args["primary_repo_url"],
    )
    return _mcp_response(result)


@tool(
    "list_deployable_repos",
    (
        "List available repositories for deployment. "
        "Returns repository names and file:// URLs. "
        "Use this to discover what apps are available to deploy."
    ),
    {},
)
async def list_deployable_repos(args: dict[str, Any]) -> dict[str, Any]:
    """List available repositories for deployment."""
    repos = _list_deployable_repos()
    return _mcp_response(repos)


@tool(
    "scan_repository",
    (
        "Quick scan of a repository to detect basic characteristics. "
        "Returns framework, language, Dockerfile info, suggested port, health path, "
        "detected database, and required environment variables. "
        "For deep analysis, use the analyze-repository sub-agent instead."
    ),
    {
        "repo_url": str,
        "branch": str,
    },
)
async def scan_repository(args: dict[str, Any]) -> dict[str, Any]:
    """Quick scan of a repository's contents."""
    result = _scan_repository(
        repo_url=args["repo_url"],
        branch=args["branch"],
    )
    return _mcp_response(result)


# =============================================================================
# Workspace Tools (require a pinned workspace)
# =============================================================================


@tool(
    "create_app",
    (
        "Create an application configuration in the selected workspace. "
        "This defines how an app will be built and deployed. "
        "Requires a workspace to be selected first with select_workspace. "
        "The repository URL is inherited from the workspace."
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
        "domain_name": str,
        "datastore_id": str,
        "dockerfile_path": str,
    },
)
async def create_app(args: dict[str, Any]) -> dict[str, Any]:
    """Create an application configuration in the selected workspace."""
    conversation = _get_conversation()
    workspace = _require_workspace(conversation)

    result = await _create_app(
        workspace=workspace,
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
        domain_name=args.get("domain_name"),
        datastore_id=args.get("datastore_id"),
        dockerfile_path=args.get("dockerfile_path", ""),
    )
    return _mcp_response(result)


@tool(
    "create_datastore",
    (
        "Create a managed database (datastore) in the selected workspace. "
        "Supports Aurora PostgreSQL and Aurora MySQL with Serverless v2 scaling. "
        "Requires a workspace to be selected first with select_workspace."
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
    workspace = _require_workspace(conversation)

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
        "Creates a deployment record and initiates the deployment process. "
        "In v1, this creates records but does not trigger real infrastructure. "
        "Use get_deployment_status to check progress."
    ),
    {
        "app_id": str,
        "git_ref": str,
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
        conversation=conversation,
    )
    return _mcp_response({
        **result.to_dict(),
        "note": (
            "Deployment created. In v1, this is stubbed and does not "
            "trigger real infrastructure. Use get_deployment_status "
            "to check progress."
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
        initiate_aws_connection,
        list_workspaces,
        select_workspace,
        create_workspace,
        list_deployable_repos,
        scan_repository,
        # Workspace tools
        create_app,
        create_datastore,
        deploy_app,
        get_deployment_status,
        # Utility
        wait,
    ],
)

# Tool names for use in allowed_tools configuration
TOOL_NAMES = [
    # Platform tools
    "mcp__devopshero__list_aws_accounts",
    "mcp__devopshero__initiate_aws_connection",
    "mcp__devopshero__list_workspaces",
    "mcp__devopshero__select_workspace",
    "mcp__devopshero__create_workspace",
    "mcp__devopshero__list_deployable_repos",
    "mcp__devopshero__scan_repository",
    # Workspace tools
    "mcp__devopshero__create_app",
    "mcp__devopshero__create_datastore",
    "mcp__devopshero__deploy_app",
    "mcp__devopshero__get_deployment_status",
    # Utility
    "mcp__devopshero__wait",
]
