import asyncio
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from ..models import Conversation, Message, User
from ..services.agent import agent_client
from ..services.agent import agent_service
from ..services.agent.agent_service import AgentStreamEvent
from ..services.agent.mcp_tools import get_tool_display_name, get_tool_main_param
from ..templatetags.chat_filters import extract_mcp_text_content
from .base import get_app_shell_context

logger = logging.getLogger(__name__)


def _get_conversations(user):
    """Get all conversations for a user in their current organization."""
    return Conversation.objects.filter(
        user=user,
        organization=user.current_organization,
    ).select_related("workspace").order_by("-updated_at")


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
    conversation = Conversation.objects.create(
        user=request.user,
        organization=request.user.current_organization,
        status=Conversation.Status.ACTIVE,
    )
    return redirect("chat_view", conversation_id=conversation.id)


@login_required
def chat_view(request, conversation_id):
    """View a specific conversation in the unified chat interface."""
    conversation = get_object_or_404(
        Conversation,
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

    # Verify conversation access upfront (before returning streaming response)
    user_pk = request.session.get('_auth_user_id')
    user = await User.objects.select_related('current_organization').aget(pk=user_pk)
    current_org = user.current_organization

    conversation_exists = await Conversation.objects.filter(
        id=conversation_id,
        user=user,
        organization=current_org,
    ).aexists()
    if not conversation_exists:
        return HttpResponse(status=404)

    async def event_generator():
        """Generate SSE events by running agent directly when needed."""
        logger.info(f"SSE event_generator started for conversation {conversation_id}")

        while True:
            # Load conversation fresh each iteration
            conversation = await Conversation.objects.select_related(
                'organization', 'user'
            ).aget(
                id=conversation_id,
                user=user,
                organization=current_org,
            )

            # Check if response needed: last message is from user
            if await _needs_response(conversation=conversation):
                logger.info(f"Running agent for conversation {conversation_id}")
                async for event in agent_service.stream_response(
                    conversation=conversation,
                    fork_session=False,
                ):
                    yield _format_sse_event(event=event)
                logger.info(f"Agent completed for conversation {conversation_id}")
                continue

            # No pending message - wait before checking again
            await asyncio.sleep(0.5)
            # Send keepalive to prevent connection timeout
            yield ": keepalive\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


async def _needs_response(conversation: Conversation) -> bool:
    """Check if conversation has an unanswered user message."""
    last_message = await conversation.messages.order_by("-created_at").afirst()
    return last_message is not None and last_message.role == Message.Role.USER


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

    # Add OOB swap for conversation title when workspace is selected
    if tool_full_name == "mcp__devopshero__select_workspace" and data.get("status") == "success":
        html += _render_title_oob_swap(result=result)

    return html


def _render_title_oob_swap(result: str) -> str:
    """Render OOB swap HTML to update conversation title after workspace selection."""
    try:
        result_data = json.loads(result) if isinstance(result, str) else result
        # Handle MCP content wrapper format: [{"type": "text", "text": "..."}]
        if isinstance(result_data, list) and result_data and "text" in result_data[0]:
            result_data = json.loads(result_data[0]["text"])
        workspace_name = result_data.get("name", "")
        if workspace_name:
            title = f"Working on {workspace_name}"
            return (
                f'<h1 id="conversation-title" hx-swap-oob="true" '
                f'class="text-lg font-semibold text-gray-900 dark:text-white">{title}</h1>'
            )
    except (json.JSONDecodeError, TypeError, KeyError, IndexError):
        pass
    return ""


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
        return _format_sse(event_name="sse-complete", data="{}")
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
