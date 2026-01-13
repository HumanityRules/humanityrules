"""
MCP tools for the Claude Agent SDK.

This module wraps our domain-specific tools as MCP tools using the
@tool decorator from claude-agent-sdk. Tools access Django context
via contextvars to maintain request isolation.
"""

import json
from contextvars import ContextVar
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

from devopshero_app.models import Conversation

from .tools import (
    create_app as _create_app,
    create_datastore as _create_datastore,
    create_workspace as _create_workspace,
    deploy_app as _deploy_app,
    get_deployment_status as _get_deployment_status,
    inspect_repository as _inspect_repository,
    list_aws_accounts as _list_aws_accounts,
    list_deployable_repos as _list_deployable_repos,
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


def _mcp_response(data: Any) -> dict[str, Any]:
    """Format data as MCP tool response."""
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    elif isinstance(data, list):
        data = [item.to_dict() if hasattr(item, "to_dict") else item for item in data]
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


@tool(
    "inspect_repository",
    (
        "Analyze a repository's contents to detect application characteristics. "
        "Only file:// URLs are supported (e.g., file:///path/to/repo). "
        "Returns framework, language, Dockerfile info, suggested port, health path, "
        "detected database, and required environment variables."
    ),
    {
        "repo_url": str,
        "branch": str,
    },
)
async def inspect_repository(args: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze a repository's contents.
    """
    result = _inspect_repository(
        repo_url=args["repo_url"],
        branch=args["branch"],
    )
    return _mcp_response(result)


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
    """
    List AWS accounts connected to the organization.
    """
    conversation = _get_conversation()

    accounts = await _list_aws_accounts(organization=conversation.organization)
    return _mcp_response(accounts)


@tool(
    "list_deployable_repos",
    (
        "List available repositories for deployment. "
        "Returns repository names and file:// URLs that can be used with inspect_repository. "
        "Use this to discover what apps are available to deploy."
    ),
    {},
)
async def list_deployable_repos(args: dict[str, Any]) -> dict[str, Any]:
    """List available repositories for deployment."""
    repos = _list_deployable_repos()
    return _mcp_response(repos)


@tool(
    "create_workspace",
    (
        "Create a new workspace for organizing applications and deployments. "
        "A workspace groups related apps together and defines the target AWS account "
        "and region for deployments. You must have at least one connected AWS account "
        "to create a workspace."
    ),
    {
        "name": str,
        "aws_account_id": str,
        "aws_region": str,
        "description": str,
        "primary_repo_url": str,
    },
)
async def create_workspace(args: dict[str, Any]) -> dict[str, Any]:
    """
    Create a new workspace in the organization.

    This tool creates a Workspace record that groups apps together
    and defines deployment targets.
    """
    conversation = _get_conversation()

    result = await _create_workspace(
        name=args["name"],
        aws_account_id=args["aws_account_id"],
        aws_region=args["aws_region"],
        organization=conversation.organization,
        user=conversation.user,
        description=args.get("description", ""),
        primary_repo_url=args.get("primary_repo_url", ""),
    )
    return _mcp_response(result)


@tool(
    "create_app",
    (
        "Create an application configuration within a workspace. "
        "This defines how an app will be built and deployed, including "
        "container settings, build strategy, and environment configuration. "
        "The workspace must exist and have an AWS account connected."
    ),
    {
        "workspace_id": str,
        "name": str,
        "repo_url": str,
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
    },
)
async def create_app(args: dict[str, Any]) -> dict[str, Any]:
    """
    Create an application configuration in a workspace.

    This tool creates an App record with build and deployment settings.
    """
    conversation = _get_conversation()

    result = await _create_app(
        workspace_id=args["workspace_id"],
        name=args["name"],
        repo_url=args["repo_url"],
        branch=args["branch"],
        app_type=args["app_type"],
        build_strategy=args["build_strategy"],
        container_port=args["container_port"],
        cpu=args["cpu"],
        memory=args["memory"],
        health_check_path=args["health_check_path"],
        organization=conversation.organization,
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
        "Create a managed database (datastore) in a workspace. "
        "Supports Aurora PostgreSQL and Aurora MySQL with Serverless v2 scaling. "
        "The database will be automatically configured with secure credentials."
    ),
    {
        "workspace_id": str,
        "name": str,
        "engine": str,
        "database_name": str,
        "deployment_mode": str,
        "serverless_min_acu": float,
        "serverless_max_acu": float,
    },
)
async def create_datastore(args: dict[str, Any]) -> dict[str, Any]:
    """
    Create a managed database in a workspace.

    This tool creates a Datastore record for an Aurora database.
    """
    conversation = _get_conversation()

    result = await _create_datastore(
        workspace_id=args["workspace_id"],
        name=args["name"],
        engine=args["engine"],
        database_name=args["database_name"],
        organization=conversation.organization,
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
    """
    Create a deployment for an application.

    This tool creates a Deployment record. In v1, it's stubbed
    and does not trigger real infrastructure deployment.
    """
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
    """
    Get current deployment status and recent logs.

    This tool retrieves deployment status including phase,
    progress percentage, and log entries.
    """
    conversation = _get_conversation()

    result = await _get_deployment_status(
        deployment_id=args["deployment_id"],
        organization=conversation.organization,
        log_limit=10,
    )
    return _mcp_response(result)


# Create the MCP server with all tools
devopshero_mcp_server = create_sdk_mcp_server(
    name="devopshero",
    version="1.0.0",
    tools=[
        create_app,
        create_datastore,
        create_workspace,
        deploy_app,
        get_deployment_status,
        inspect_repository,
        list_aws_accounts,
        list_deployable_repos,
    ],
)

# Tool names for use in allowed_tools configuration
TOOL_NAMES = [
    "mcp__devopshero__create_app",
    "mcp__devopshero__create_datastore",
    "mcp__devopshero__create_workspace",
    "mcp__devopshero__deploy_app",
    "mcp__devopshero__get_deployment_status",
    "mcp__devopshero__inspect_repository",
    "mcp__devopshero__list_aws_accounts",
    "mcp__devopshero__list_deployable_repos",
]
