import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.db import connection, connections
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from ..models import Conversation, Message
from ..services.agent import agent_client
from ..services.agent import agent_runner
from ..services.agent.agent_service import AgentStreamEvent
from ..services.agent.mcp_tools import get_tool_display_name, get_tool_main_param
from ..templatetags.chat_filters import extract_mcp_text_content
from .base import get_app_shell_context

logger = logging.getLogger(__name__)


def _get_db_connection_count() -> int | None:
    """Query PostgreSQL for connection count excluding this session."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid()")
            return cursor.fetchone()[0]
    except Exception:
        return None


async def _log_connections(label: str) -> None:
    """Log current DB connection count with a label."""
    count = await sync_to_async(_get_db_connection_count, thread_sensitive=True)()
    logger.info(f"[CONN] {label}: db_connections={count}")


def _get_conversations(user):
    """Get all conversations for a user in their current organization."""
    return Conversation.objects.filter(
        user=user,
        organization=user.current_organization,
    ).select_related("context_workspace", "context_repository").order_by("-updated_at")


@login_required
def chat_list(request):
    """Show unified chat interface with no conversation selected."""
    conversations = _get_conversations(user=request.user)

    context = get_app_shell_context(request=request, current_page="chat")
    context["conversations"] = conversations
    context["conversation"] = None
    context["messages"] = []

    if request.htmx:
        return render(request, "devopshero_app/chat/chat.html", context=context)

    context["content_url"] = "/chat/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
def chat_new(request):
    """Create a new conversation and redirect to it."""
    workspace_id = request.GET.get("workspace")
    repo_id = request.GET.get("repo")
    
    conversation = Conversation.objects.create(
        user=request.user,
        organization=request.user.current_organization,
        context_workspace_id=workspace_id if workspace_id else None,
        context_repository_id=repo_id if repo_id else None,
        status=Conversation.Status.ACTIVE,
    )
    return redirect("chat_view", conversation_id=conversation.id)


@login_required
def chat_view(request, conversation_id):
    """View a specific conversation in the unified chat interface."""
    conversation = get_object_or_404(
        Conversation.objects.select_related("context_workspace", "context_repository"),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    messages = conversation.messages.all().order_by("created_at")

    context = get_app_shell_context(request=request, current_page="chat")
    context["conversation"] = conversation
    context["messages"] = messages

    # HTMX request targeting the chat panel - return just the panel content
    if request.htmx and request.htmx.target == "chat-panel":
        return render(request, "devopshero_app/chat/_chat_panel.html", context=context)

    # Full HTMX navigation or direct page load - need full unified template
    context["conversations"] = _get_conversations(user=request.user)

    if request.htmx:
        return render(request, "devopshero_app/chat/chat.html", context=context)

    context["content_url"] = f"/chat/{conversation_id}/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
@require_POST
def chat_send(request, conversation_id):
    """Send a message in a conversation and trigger agent response."""
    conversation = Conversation.objects.select_related(
        'organization', 'user'
    ).get(
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    message_text = request.POST.get("message", "").strip()
    choice_id = request.POST.get("choice_id")

    if not message_text:
        return HttpResponse(status=400)

    # Create user message
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content_type=Message.ContentType.TEXT,
        content=message_text,
        metadata={"choice_id": choice_id} if choice_id else {},
    )

    # Update conversation timestamp
    conversation.save()

    # Render the user message
    context = {"message": user_message, "conversation_id": conversation_id}
    user_html = render_to_string(
        "devopshero_app/chat/_message.html",
        context=context,
        request=request,
    )

    # OOB delete the 'empty chat' placeholder (if present)
    remove_placeholder = '<div id="empty-chat-placeholder" hx-swap-oob="delete"></div>'

    # The SSE connection (chat_stream) will run the agent and show the thinking indicator
    if agent_client.is_available():
        return HttpResponse(user_html + remove_placeholder)

    # Agent unavailable - log error and inform user
    logger.error("Agent client unavailable")
    unavailable_html = render_to_string("devopshero_app/chat/_streaming_unavailable.html")
    return HttpResponse(user_html + unavailable_html + remove_placeholder)


@login_required
async def chat_stream(request, conversation_id):
    """SSE endpoint for streaming agent responses."""
    # Check agent availability before starting stream
    if not agent_client.is_available():
        logger.error("Agent client unavailable")

        async def unavailable_generator():
            yield _format_sse(event_name="sse-error", data=_render_streaming_error(error_msg="AI assistant is currently unavailable"))

        response = StreamingHttpResponse(unavailable_generator(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    user = await request.auser()

    try:
        conversation = await Conversation.objects.select_related(
            'organization', 'user'
        ).aget(
            id=conversation_id,
            user=user,
            organization_id=user.current_organization_id,
        )
    except Conversation.DoesNotExist:
        return HttpResponse(status=404)

    async def event_generator():
        """Subscribe to agent runner's event queue and yield SSE events."""
        logger.info(f"SSE event_generator started for conversation {conversation_id}")

        try:
            # Get or spawn agent runner
            runner = await agent_runner.ensure_agent_running(conversation=conversation)
            agent_runner.mark_client_connected(conversation_id=conversation_id)

            # Consume events from runner's queue with keepalive
            # Keepalive interval must be shorter than CloudFront origin timeout (30s default)
            keepalive_interval = 15.0
            while True:
                try:
                    event = await asyncio.wait_for(runner.event_queue.get(), timeout=keepalive_interval)
                    if event is None:
                        # Sentinel: runner finished, exit loop
                        logger.info(f"Agent runner completed for conversation {conversation_id}")
                        break
                    yield _format_sse_event(event=event)
                except asyncio.TimeoutError:
                    # No event within timeout - send SSE comment to keep connection alive
                    yield ": keepalive\n\n"

        except asyncio.CancelledError:
            # Uvicorn cancels async tasks when the client disconnects (e.g., page reload).
            # Mark disconnected so runner can exit when done with current work.
            agent_runner.mark_client_disconnected(conversation_id=conversation_id)
            logger.info(f"SSE client disconnected for conversation {conversation_id}")
            raise

        finally:
            # Close DB connections opened while iterating this generator. Streaming responses
            # are consumed after the view returns, so request_finished cleanup (the normal Django 
            # cleanup tied to the request lifecycle) won't see them and close them.
            # Explicit close_all() is required here to clean up connections created in this context.
            conn_before = await sync_to_async(_get_db_connection_count, thread_sensitive=True)()
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            logger.info(f"[CONN] event_generator CLEANUP {conversation_id}: db_connections {conn_before}")

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _format_sse(event_name: str, data: str) -> str:
    """Format data as SSE event."""
    sse_data = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event_name}\n{sse_data}\n\n"


def _render_tool_start(data: dict) -> str:
    """Render HTML for tool execution start."""
    tool_full_name = data.get("name", "unknown")
    parameters = data.get("input", {})
    tool_name = get_tool_display_name(tool_full_name, parameters)
    tool_main_param = get_tool_main_param(tool_full_name, parameters)
    if tool_main_param:
        tool_name = f"{tool_name}: "
    params_json = json.dumps(parameters, indent=2) if parameters else "{}"
    return render_to_string("devopshero_app/chat/_streaming_tool_start.html", context={
        "tool_name": tool_name,
        "tool_main_param": tool_main_param,
        "tool_use_id": data.get("tool_use_id", ""),
        "params_json": params_json,
    })


def _render_tool_result(data: dict) -> str:
    """Render HTML for tool execution result (OOB swap)."""
    tool_full_name = data.get("name", "unknown")
    parameters = data.get("input", {})
    result = data.get("result", "")

    # Format JSON for display
    params_json = json.dumps(parameters, indent=2) if parameters else "{}"

    # Parse result, extract MCP text content, and pretty-print
    try:
        result_parsed = json.loads(result) if isinstance(result, str) else result
        result_parsed = extract_mcp_text_content(result_parsed)
        if isinstance(result_parsed, str):
            result_json = result_parsed
        else:
            result_json = json.dumps(result_parsed, indent=2)
    except (json.JSONDecodeError, TypeError):
        result_json = str(result)

    tool_name = get_tool_display_name(tool_full_name, parameters)
    tool_main_param = get_tool_main_param(tool_full_name, parameters)
    if tool_main_param:
        tool_name = f"{tool_name}: "

    html = render_to_string("devopshero_app/chat/_streaming_tool_result.html", context={
        "tool_name": tool_name,
        "tool_main_param": tool_main_param,
        "tool_use_id": data.get("tool_use_id", ""),
        "status": data.get("status", "success"),
        "duration_ms": data.get("duration_ms", 0),
        "params_json": params_json,
        "result_json": result_json,
    })

    return html


def _render_thinking() -> str:
    """Render HTML for 'agent is thinking' indicator (OOB swap into placeholder)."""
    return render_to_string("devopshero_app/chat/_streaming_thinking.html")


def _render_streaming_start() -> str:
    """Render HTML for streaming message container."""
    return render_to_string("devopshero_app/chat/_streaming_start.html")


def _render_streaming_error(error_msg: str) -> str:
    """Render HTML for streaming error message."""
    return render_to_string("devopshero_app/chat/_streaming_error.html", context={
        "error_msg": error_msg,
    })


def _render_title_update(title: str, conversation_id: str) -> str:
    """Render OOB swap HTML to update conversation title in header and sidebar."""
    # OOB swap for main header title
    header_html = (
        f'<h1 id="conversation-title" hx-swap-oob="true" '
        f'class="text-lg font-semibold text-gray-900 dark:text-white">{title}</h1>'
    )
    # OOB swap for sidebar title
    sidebar_html = (
        f'<h3 id="sidebar-title-{conversation_id}" hx-swap-oob="true" '
        f'class="text-sm font-medium text-gray-900 dark:text-white truncate">{title}</h3>'
    )
    return header_html + sidebar_html


def _format_sse_event(event: AgentStreamEvent) -> str:
    """Convert AgentStreamEvent to SSE format."""
    if event.type == "thinking":
        return _format_sse(event_name="sse-thinking", data=_render_thinking())
    elif event.type == "start":
        return _format_sse(event_name="sse-start", data=_render_streaming_start())
    elif event.type == "text_delta":
        return _format_sse(event_name="sse-text-delta", data=json.dumps(event.data))
    elif event.type == "text_flush":
        return _format_sse(event_name="sse-text-flush", data="{}")
    elif event.type == "tool_start":
        return _format_sse(event_name="sse-tool-start", data=_render_tool_start(event.data))
    elif event.type == "tool_result":
        return _format_sse(event_name="sse-tool-result", data=_render_tool_result(event.data))
    elif event.type == "complete":
        # Include title OOB swap if a title was generated
        data = _render_title_update(title=event.data["title"], conversation_id=event.data["conversation_id"]) if event.data.get("title") else ""
        return _format_sse(event_name="sse-complete", data=data)
    elif event.type == "error":
        error_msg = event.data.get("error", "Unknown error") if event.data else "Unknown error"
        return _format_sse(event_name="sse-error", data=_render_streaming_error(error_msg=error_msg))
    return ""


@login_required
def chat_messages(request, conversation_id):
    """Get messages for a conversation (with pagination support)."""
    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    # Get pagination params
    offset = int(request.GET.get("offset", 0))
    limit = int(request.GET.get("limit", 50))

    messages = conversation.messages.all().order_by("created_at")[offset : offset + limit]

    context = {"messages": messages, "conversation_id": conversation_id}

    # Render all messages
    html_parts = []
    for message in messages:
        context["message"] = message
        html_parts.append(
            render_to_string(
                "devopshero_app/chat/_message.html",
                context=context,
                request=request,
            )
        )

    return HttpResponse("".join(html_parts))


@login_required
@require_POST
def chat_close(request, conversation_id):
    """Mark a conversation as completed."""
    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    conversation.status = Conversation.Status.COMPLETED
    conversation.save()

    return redirect("chat_list")
