"""
Agent service for managing deployment conversations.

This module provides the core agent service that:
- Loads conversation context from the database
- Sends messages to Claude using the Claude Agent SDK
- Handles tool calls automatically via MCP tools
- Saves agent responses back to the database
- Records tool calls as visible messages in the conversation

Built on the Claude Agent SDK for robust agent orchestration with
structured tool calling, conversation memory, and streaming responses.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SandboxSettings,
    UserMessage,
)
from claude_agent_sdk.types import (
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    SystemMessage,
    StreamEvent as SDKStreamEvent,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from django.conf import settings
from django.db.models import Sum

from devopshero_app.models import AWSAccount, Conversation, Environment, LLMUsageLog, Message, Repository, Workspace
from devopshero_app.services.gitproviders import repo_service
from devopshero_app.services.llm import llm_client, title_generator

from .agent_client import get_claude_env
from .mcp_tools import (
    conversation_context,
    devopshero_mcp_server,
    TOOL_NAMES,
)
from .repo_analysis.repo_analyzer_config import get_analyze_repository_agent

logger = logging.getLogger(__name__)


AgentEventType = Literal[
    "thinking",     # Agent is thinking (before text or between tools)
    "start",        # Streaming started, create message container
    "text_delta",   # Text chunk to append
    "text_flush",   # Finalize current streaming text (before tool call)
    "tool_start",   # Tool execution starting
    "tool_result",  # Tool execution completed
    "complete",     # Streaming finished (may include title for OOB update)
    "error",        # Error occurred
]


@dataclass
class AgentStreamEvent:
    """Event yielded during agent response streaming."""

    type: AgentEventType
    data: Any = None


@dataclass
class StreamingContext:
    """Mutable state for streaming response processing."""

    conversation: Conversation
    pending_tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    accumulated_content: str = ""
    has_started_streaming: bool = False


@dataclass
class SandboxPaths:
    """Sandbox directory structure for a conversation."""
    root_path: Path   # sandbox/conv-{id}/ — conversation isolation folder
    src_path: Path    # sandbox/conv-{id}/src/ — cloned repository
    tmp_path: Path    # sandbox/conv-{id}/tmp/ — temp files for this conversation


def _get_sandbox_paths(conversation_id) -> SandboxPaths:
    """Single source of truth for conversation sandbox paths."""
    root_path = settings.CLAUDE_SANDBOX_DIR / f"conv-{conversation_id}"
    return SandboxPaths(
        root_path=root_path,
        src_path=root_path / "src",
        tmp_path=root_path / "tmp",
    )


def _load_prompt_file(filename: str) -> str:
    """Load a system prompt from the given filename."""
    prompt_path = Path(__file__).parent / filename
    return prompt_path.read_text()


def _get_llm_model_for_conversation_mode(mode: str) -> str:
    """Return the Claude model alias for a conversation mode."""
    mode_to_setting = {
        Conversation.Mode.GENERAL: settings.CLAUDE_MODEL_GENERAL,
        Conversation.Mode.ENVIRONMENT_SETUP: settings.CLAUDE_MODEL_ENVIRONMENT,
        Conversation.Mode.APP_DEPLOYMENT: settings.CLAUDE_MODEL_APP_DEPLOYMENT,
    }
    return mode_to_setting.get(mode, settings.CLAUDE_MODEL_GENERAL)


async def _build_system_prompt(conversation: Conversation) -> str:
    """Build system prompt based on conversation mode."""
    if conversation.mode == Conversation.Mode.ENVIRONMENT_SETUP:
        return await _build_environment_prompt(conversation)
    elif conversation.mode == Conversation.Mode.APP_DEPLOYMENT:
        return await _build_app_deployment_prompt(conversation)
    else:
        return await _build_general_prompt(conversation)


async def _build_environment_prompt(conversation: Conversation) -> str:
    """Build system prompt for environment setup conversations."""
    base_prompt = _load_prompt_file("system_prompt_environment.md")
    sections = []

    # Conversation context - AWS account
    account = await AWSAccount.objects.aget(id=conversation.context_aws_account_id)
    context_lines = [
        f"Target AWS Account: {account.name} (id: {account.id}, aws: {account.aws_account_id or 'pending'}, status: {account.status})"
    ]
    sections.append("## Conversation Context\n\n" + "\n".join(context_lines))

    # Existing environments and naming guidance
    existing_envs = [
        env async for env in Environment.objects.filter(aws_account_id=account.id).values("name", "aws_region", "status")
    ]
    existing_names = {env["name"] for env in existing_envs}

    env_lines = []
    if existing_envs:
        env_lines.append("This AWS account already has these environments:")
        for env in existing_envs:
            env_lines.append(f"- {env['name']} ({env['aws_region']}, {env['status']})")
    else:
        env_lines.append("This AWS account has no environments yet.")

    # Naming priority: suggest first available name
    naming_priority = ["default", "dev", "staging", "prod"]
    suggested_name = next((name for name in naming_priority if name not in existing_names), None)

    env_lines.append("")
    env_lines.append("Naming priority (use first available): default, dev, staging, prod")
    if suggested_name:
        env_lines.append(f"Suggested name: **{suggested_name}**")
    else:
        env_lines.append("All standard names taken — ask the user for a custom name.")

    sections.append("## Existing Environments\n\n" + "\n".join(env_lines))

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_app_deployment_prompt(conversation: Conversation) -> str:
    """Build system prompt for app deployment conversations."""
    base_prompt = _load_prompt_file("system_prompt_app_deployment.md")
    sections = []

    # Conversation context section
    context_lines = []
    if conversation.context_workspace_id:
        ws = await Workspace.objects.aget(id=conversation.context_workspace_id)
        context_lines.append(f"Current workspace: {ws.name} (id: {ws.id})")
    if conversation.context_repository_id:
        repo = await Repository.objects.aget(id=conversation.context_repository_id)
        context_lines.append(f"Current repository: {repo.full_name} (id: {repo.id})")

    if context_lines:
        sections.append("## Conversation Context\n\n" + "\n".join(context_lines))

    # AWS infrastructure section
    infra_section = await _build_aws_infrastructure_section(conversation.organization_id)
    if infra_section:
        sections.append(infra_section)

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_general_prompt(conversation: Conversation) -> str:
    """Build system prompt for general conversations."""
    base_prompt = _load_prompt_file("system_prompt_general.md")
    sections = []

    # Conversation context section (workspace only for general mode)
    context_lines = []
    if conversation.context_workspace_id:
        ws = await Workspace.objects.aget(id=conversation.context_workspace_id)
        context_lines.append(f"Current workspace: {ws.name} (id: {ws.id})")

    if context_lines:
        sections.append("## Conversation Context\n\n" + "\n".join(context_lines))

    # AWS infrastructure section
    infra_section = await _build_aws_infrastructure_section(conversation.organization_id)
    if infra_section:
        sections.append(infra_section)

    if sections:
        return base_prompt + "\n\n" + "\n\n".join(sections)
    return base_prompt


async def _build_aws_infrastructure_section(organization_id) -> str:
    """Build the AWS infrastructure section listing accounts and environments."""
    accounts = AWSAccount.objects.filter(organization_id=organization_id).prefetch_related("environments")

    lines = ["## AWS Infrastructure", ""]
    account_count = 0

    async for account in accounts:
        account_count += 1
        lines.append(f"Account: **{account.name}** (id: {account.id}, aws: {account.aws_account_id or 'pending'}, status: {account.status})")

        environments = [env async for env in account.environments.all()]
        if environments:
            for env in environments:
                domain_info = f", domain: *.{env.shared_alb_hosted_zone}" if env.shared_alb_hosted_zone else ""
                lines.append(f"  - Environment: **{env.name}** (id: {env.id}, slug: {env.slug}, region: {env.aws_region}, status: {env.status}{domain_info})")
        else:
            lines.append("  - No environments yet")
        lines.append("")

    if account_count == 0:
        return "## AWS Infrastructure\n\nNo AWS accounts connected. Guide the user to connect one using initiate_aws_connection."

    return "\n".join(lines)


async def _aget_last_user_message(conversation: Conversation) -> str:
    """Get the content of the most recent user message."""
    last_message = await conversation.messages.filter(role="user").order_by("-created_at").afirst()

    if not last_message:
        raise ValueError("Conversation has no user messages to process")

    return last_message.content


async def _persist_text_message(conversation: Conversation, content: str) -> None:
    """Persist an agent text message to the database as markdown."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.MARKDOWN,
        content=content,
    )


async def _persist_tool_call(conversation: Conversation, tool_name: str, parameters: Any, result: str, status: str, duration_ms: int) -> None:
    """Persist a tool call message to the database."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.AGENT,
        content_type=Message.ContentType.TOOL_CALL,
        content=tool_name,
        metadata={
            "tool_name": tool_name,
            "parameters": parameters,
            "result": result,
            "status": status,
            "duration_ms": duration_ms,
        },
    )


async def _persist_error(conversation: Conversation, error_type: str, error_description: str) -> None:
    """Persist an error message to the database."""
    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.SYSTEM,
        content_type=Message.ContentType.ERROR,
        content=error_description,
        metadata={"error_type": error_type},
    )


async def _maybe_generate_title(conversation: Conversation, user_message: str, agent_response: str) -> None:
    """Generate and set conversation title using LLM if not already set."""
    try:
        # Get context names for title generation
        workspace_name = None
        repo_name = None
        aws_account_name = None

        if conversation.context_workspace_id:
            ws = await Workspace.objects.only("name").aget(id=conversation.context_workspace_id)
            workspace_name = ws.name

        if conversation.context_repository_id:
            repo = await Repository.objects.only("full_name").aget(id=conversation.context_repository_id)
            repo_name = repo.full_name

        if conversation.context_aws_account_id:
            account = await AWSAccount.objects.only("name").aget(id=conversation.context_aws_account_id)
            aws_account_name = account.name

        # Generate title in thread (anthropic client is sync)
        result = await asyncio.to_thread(
            title_generator.generate_title,
            user_message,
            agent_response,
            workspace_name,
            repo_name,
            aws_account_name,
        )

        conversation.title = result.title
        logger.info(f"Generated title for conversation {conversation.id}: {result.title}")

        # Log title generation LLM usage
        await LLMUsageLog.objects.acreate(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            conversation=conversation,
            source=LLMUsageLog.Source.TITLE_GENERATION,
            model_alias=title_generator.MODEL_ALIAS,
            model_id=llm_client.get_model_id(alias=title_generator.MODEL_ALIAS),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=None,
            duration_ms=None,
            num_turns=None,
        )

    except Exception as e:
        # Don't fail the conversation if title generation fails
        logger.error(f"Failed to generate title for conversation {conversation.id}: {e}")


async def _handle_sdk_stream_event(message: SDKStreamEvent, ctx: StreamingContext) -> AsyncGenerator[AgentStreamEvent, None]:
    """Handle token-level streaming events."""
    event_type = message.event.get("type")

    if event_type == "content_block_delta":
        delta = message.event.get("delta", {})
        if delta.get("type") == "text_delta":
            text_chunk = delta.get("text", "")
            if text_chunk:
                # Create streaming container on first text (replaces thinking indicator)
                if not ctx.has_started_streaming:
                    yield AgentStreamEvent(type="start")
                    ctx.has_started_streaming = True
                ctx.accumulated_content += text_chunk
                yield AgentStreamEvent(type="text_delta", data={"text": text_chunk})


async def _enrich_tool_input(tool_name: str, tool_input: dict) -> dict:
    """
    Enrich tool input with display-friendly data looked up from the database.

    Originally added (2026-01-15) to resolve UUIDs to friendly names for UI display.
    For example, when deploy_app was called with app_id, we'd look up the app name
    so the UI could show "Deploy App: my-cool-app" instead of a UUID.

    As of 2026-01-27, the two original use cases are obsolete:
    - deploy_app now takes 'name' directly (upsert by name, not app_id)
    - select_workspace tool was removed

    Kept as a hook for future enrichment needs.
    """
    return tool_input


async def _handle_assistant_message(message: AssistantMessage, ctx: StreamingContext) -> AsyncGenerator[AgentStreamEvent, None]:
    """Handle assistant messages containing tool use blocks."""

    # Handle API-level errors (rate_limit, invalid_request, server_error, etc.)
    # When message.error is set, the TextBlock content was NOT streamed — it contains the error description.
    if message.error:
        error_text = " ".join(block.text for block in message.content if isinstance(block, TextBlock))
        error_description = error_text or message.error
        logger.error(f"[SDK] AssistantMessage error ({message.error}): {error_description}")
        await _persist_error(conversation=ctx.conversation, error_type=message.error, error_description=error_description)
        yield AgentStreamEvent(type="error", data={"error": error_description})
        return
    
    # Persist any accumulated text before tool calls
    if ctx.accumulated_content:
        await _persist_text_message(conversation=ctx.conversation, content=ctx.accumulated_content)
        ctx.accumulated_content = ""

    # Flush to release streaming element IDs (only if we were streaming text)
    if ctx.has_started_streaming:
        yield AgentStreamEvent(type="text_flush")
        ctx.has_started_streaming = False

    for block in message.content:
        if not isinstance(block, ToolUseBlock):
            # TextBlocks have already been streamed via SDKStreamEvent, so we skip them.
            # ThinkingBlocks are intentionally not surfaced to the user.
            continue

        # Enrich input with display-friendly data (e.g., app_name from app_id)
        enriched_input = await _enrich_tool_input(block.name, block.input)

        # Record pending tool call
        ctx.pending_tool_calls[block.id] = {
            "name": block.name,
            "input": enriched_input,
            "start_time": time.time(),
        }

        yield AgentStreamEvent(
            type="tool_start",
            data={
                "tool_use_id": block.id,
                "name": block.name,
                "input": enriched_input,
            },
        )
        

async def _handle_tool_results(message: UserMessage, ctx: StreamingContext) -> AsyncGenerator[AgentStreamEvent, None]:
    """Handle tool results from synthetic user messages."""
    if not isinstance(message.content, list):
        return

    for block in message.content:
        if not isinstance(block, ToolResultBlock):
            # Non-ToolResultBlock content (e.g., TextBlock) can appear in synthetic
            # UserMessages from the SDK. These are informational and can be skipped.
            continue

        call_info = ctx.pending_tool_calls.pop(block.tool_use_id, None)
        if not call_info:
            logger.error(f"No call info found for tool use ID: {block.tool_use_id}")
            continue

        tool_name = call_info["name"]
        duration_ms = int((time.time() - call_info["start_time"]) * 1000)
        status = "error" if block.is_error else "success"

        await _persist_tool_call(
            conversation=ctx.conversation,
            tool_name=tool_name,
            parameters=call_info["input"],
            result=block.content,
            status=status,
            duration_ms=duration_ms,
        )

        yield AgentStreamEvent(
            type="tool_result",
            data={
                "tool_use_id": block.tool_use_id,
                "name": tool_name,
                "input": call_info["input"],
                "result": block.content,
                "status": status,
                "duration_ms": duration_ms,
            },
        )

    # Show thinking indicator while waiting for next response (text or another tool)
    ctx.has_started_streaming = False
    yield AgentStreamEvent(type="thinking")


def _create_agent_options(system_prompt: str, resume_session_id: str | None, fork_session: bool, sandbox_paths: SandboxPaths, model_alias: str) -> ClaudeAgentOptions:
    """Create SDK client options with standard configuration."""

    # The sandbox settings are used to restrict the agent's filesystem access but only for Bash commands.
    sandbox_settings = SandboxSettings(
        enabled=True,
        autoAllowBashIfSandboxed=False,
        allowUnsandboxedCommands=False
    )

    # TMPDIR points to the per-conversation tmp folder for isolation
    env = {
        **get_claude_env(),
        "TMPDIR": str(sandbox_paths.tmp_path),
    }

    # Excluded built-in tools: NotebookEdit, WebFetch, KillShell, AskUserQuestion, Skill
    builtin_tools = [
        "Read", "Write", "Edit", "Glob", "Grep", "Bash",
        "Task", "TaskOutput", "TodoWrite", "EnterPlanMode", "ExitPlanMode",
    ]

    blocked_agents = ["Task(Bash)", "Task(statusline-setup)"]
    
    return ClaudeAgentOptions(
        model=llm_client.get_model_id(alias=model_alias),
        system_prompt=system_prompt,
        resume=resume_session_id,
        fork_session=fork_session,
        permission_mode="default",
        cwd=str(sandbox_paths.src_path),
        sandbox=sandbox_settings,
        agents={"analyze-repository": get_analyze_repository_agent()},
        mcp_servers={"devopshero": devopshero_mcp_server},
        tools=builtin_tools,
        allowed_tools=TOOL_NAMES,
        disallowed_tools=blocked_agents,
        env=env,
        include_partial_messages=True,
    )


async def stream_response(conversation: Conversation, fork_session: bool) -> AsyncGenerator[AgentStreamEvent, None]:
    """
    Stream agent response for a conversation.

    This is the main entry point for streaming agent processing. It:
    1. Clones the repository if one is set in conversation context
    2. Sets up the conversation context for MCP tools
    3. Sends the latest user message to Claude via the Agent SDK
    4. Yields text deltas for real-time frontend updates
    5. Handles tool calls and yields tool events
    6. Persists final messages to the database on completion
    7. Cleans up cloned repository

    Args:
        conversation: The Conversation to process. Its session_id is used to resume or fork.
        fork_session: Whether to fork a resumed session into a new session ID.

    Yields:
        AgentStreamEvent objects for each streaming event.

    Raises:
        ValueError: If the conversation has no user messages.
    """
    conversation_context.set(conversation)
    user_message = await _aget_last_user_message(conversation)
    system_prompt = await _build_system_prompt(conversation)

    logger.info(f"System prompt for conversation {conversation.id}:\n{system_prompt}")

    # Set up per-conversation sandbox directory structure. Do not create the src path here, it will be created by the repository clone.
    sandbox_paths = _get_sandbox_paths(conversation.id)
    sandbox_paths.root_path.mkdir(parents=True, exist_ok=True)
    sandbox_paths.tmp_path.mkdir(parents=True, exist_ok=True)

    # Clone repository if one is set in conversation context
    if conversation.context_repository_id:
        repository = await Repository.objects.select_related("integration").aget(
            id=conversation.context_repository_id,
        )
        await asyncio.to_thread(
            repo_service.clone_repository,
            repository,
            repository.default_branch,
            sandbox_paths.src_path,
        )

    model_alias = _get_llm_model_for_conversation_mode(conversation.mode)
    logger.info(f"Using model {model_alias} for conversation {conversation.id} (mode={conversation.mode})")

    options = _create_agent_options(
        system_prompt=system_prompt,
        resume_session_id=conversation.session_id,
        fork_session=fork_session,
        sandbox_paths=sandbox_paths,
        model_alias=model_alias,
    )

    # The streaming context is used to store the accumulated content and the pending tool calls, and is passed 
    # around and mutated by the event handlers
    ctx = StreamingContext(conversation=conversation)

    # Start with thinking indicator (will be replaced by streaming container on first text)
    yield AgentStreamEvent(type="thinking")

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_message)

            async for message in client.receive_response():
                if isinstance(message, SDKStreamEvent):
                    async for event in _handle_sdk_stream_event(message, ctx):
                        yield event

                elif isinstance(message, AssistantMessage):
                    async for event in _handle_assistant_message(message, ctx):
                        yield event

                elif isinstance(message, UserMessage):
                    async for event in _handle_tool_results(message, ctx):
                        yield event

                elif isinstance(message, ResultMessage):
                    logger.info(f"[SDK] ResultMessage: turns={message.num_turns}, cost=${message.total_cost_usd or 0:.4f}")
                    # Capture session_id for conversation continuity
                    if message.session_id and not conversation.session_id:
                        conversation.session_id = message.session_id

                    # Log LLM usage to database
                    usage = message.usage or {}
                    await LLMUsageLog.objects.acreate(
                        organization_id=conversation.organization_id,
                        user_id=conversation.user_id,
                        conversation=conversation,
                        source=LLMUsageLog.Source.AGENT_TURN,
                        model_alias=model_alias,
                        model_id=llm_client.get_model_id(alias=model_alias),
                        input_tokens=usage.get("input_tokens"),
                        output_tokens=usage.get("output_tokens"),
                        cost_usd=message.total_cost_usd,
                        duration_ms=message.duration_ms,
                        num_turns=message.num_turns,
                    )

                elif isinstance(message, SystemMessage):
                    logger.info(f"[SDK] SystemMessage: subtype={message.subtype}, data=\n{json.dumps(message.data, indent=2)}")

        if ctx.accumulated_content:
            await _persist_text_message(conversation=conversation, content=ctx.accumulated_content)

        # Generate title for new conversations (first message)
        generated_title = None
        if not conversation.title:
            await _maybe_generate_title(
                conversation=conversation,
                user_message=user_message,
                agent_response=ctx.accumulated_content or "",
            )
            if conversation.title:
                generated_title = conversation.title

        await conversation.asave()

        # Query total cost for this conversation (includes the turn we just logged + title gen)
        total_cost = await LLMUsageLog.objects.filter(
            conversation=conversation,
        ).aaggregate(total=Sum("cost_usd"))
        total_cost_value = total_cost["total"]

        # Include title and cost in complete event (for OOB UI updates)
        complete_data = {"conversation_id": str(conversation.id)}
        if generated_title:
            complete_data["title"] = generated_title
        if total_cost_value is not None:
            complete_data["total_cost"] = f"{total_cost_value:.4f}"
        yield AgentStreamEvent(type="complete", data=complete_data)

    except Exception as e:
        logger.exception("Error during streaming conversation processing")
        try:
            await _persist_error(conversation=conversation, error_type=type(e).__name__, error_description=f"Agent error: {e}")
        except Exception:
            logger.exception("Failed to save error message to database")
        yield AgentStreamEvent(type="error", data={"error": str(e)})

    # NOTE: Cloned repos are NOT cleaned up per-message.
    # They persist for the conversation lifetime to allow agent work to accumulate.
    # TODO: Implement cleanup via chat_close or periodic cleanup job.
