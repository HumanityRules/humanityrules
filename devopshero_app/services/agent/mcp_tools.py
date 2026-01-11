"""
MCP tools for the Claude Agent SDK.

This module wraps our domain-specific tools as MCP tools using the
@tool decorator from claude-agent-sdk. Tools access Django context
via contextvars to maintain request isolation.
"""

import json
from contextvars import ContextVar
from typing import Any

from asgiref.sync import sync_to_async
from claude_agent_sdk import tool, create_sdk_mcp_server

from devopshero_app.models import Conversation

from .tools import (
    inspect_repository as _inspect_repository,
    list_aws_accounts as _list_aws_accounts,
    ask_user as _ask_user,
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

    This tool wraps the synchronous inspect_repository function
    and returns the result as MCP tool output.
    """
    # inspect_repository is synchronous and doesn't need Django ORM
    result = _inspect_repository(
        repo_url=args["repo_url"],
        branch=args["branch"],
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result.to_dict(), indent=2),
            }
        ]
    }


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

    This tool accesses the Django ORM via sync_to_async to get
    accounts for the current conversation's organization.
    """
    conversation = _get_conversation()

    # Wrap the synchronous Django ORM call
    accounts = await sync_to_async(_list_aws_accounts)(
        organization=conversation.organization
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps([a.to_dict() for a in accounts], indent=2),
            }
        ]
    }


@tool(
    "ask_user",
    (
        "Ask the user a question with interactive choice buttons. "
        "Use this when you need user input to proceed, such as selecting "
        "an AWS account, confirming a deployment, or choosing between options. "
        "The question will be displayed with clickable buttons for each choice."
    ),
    {
        "question": str,
        "choices": list,
        "allow_text_input": bool,
    },
)
async def ask_user(args: dict[str, Any]) -> dict[str, Any]:
    """
    Ask the user a question with choices.

    This tool creates a CHOICE message in the conversation that
    renders as interactive buttons in the UI. The agent should
    wait for the user's response before proceeding.
    """
    conversation = _get_conversation()

    # Wrap the synchronous Django ORM call
    result = await sync_to_async(_ask_user)(
        question=args["question"],
        choices=args["choices"],
        conversation=conversation,
        allow_text_input=args.get("allow_text_input", True),
    )

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        **result.to_dict(),
                        "note": (
                            "Question has been presented to the user. "
                            "Wait for their response before proceeding."
                        ),
                    },
                    indent=2,
                ),
            }
        ]
    }


# Create the MCP server with all tools
devopshero_mcp_server = create_sdk_mcp_server(
    name="devopshero",
    version="1.0.0",
    tools=[
        inspect_repository,
        list_aws_accounts,
        ask_user,
    ],
)

# Tool names for use in allowed_tools configuration
TOOL_NAMES = [
    "mcp__devopshero__inspect_repository",
    "mcp__devopshero__list_aws_accounts",
    "mcp__devopshero__ask_user",
]
