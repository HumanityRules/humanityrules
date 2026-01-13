import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from ..models import Conversation, Message
from ..services.agent import agent_client
from ..services.agent import agent_service
from ..services.streaming_service import StreamEvent
from .base import get_app_shell_context

logger = logging.getLogger(__name__)


@login_required
def chat_list(request):
    """List all conversations for the current user."""
    conversations = Conversation.objects.filter(
        user=request.user,
        organization=request.user.current_organization,
    ).select_related("workspace").order_by("-updated_at")

    context = get_app_shell_context(request=request, current_page="chat")
    context["conversations"] = conversations

    if request.htmx:
        return render(request, "devopshero_app/chat/chat_list.html", context=context)

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
    """View a specific conversation."""
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

    if request.htmx:
        return render(request, "devopshero_app/chat/chat_view.html", context=context)

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

    # OOB delete the empty chat placeholder (if present)
    remove_placeholder = '<div id="empty-chat-placeholder" hx-swap-oob="delete"></div>'

    # Include typing indicator if agent is available
    # The SSE connection (chat_stream) will run the agent when it detects the new message
    if agent_client.is_available():
        typing_html = render_to_string(
            "devopshero_app/chat/_typing_indicator.html",
            context={"conversation_id": conversation_id},
            request=request,
        )
        return HttpResponse(user_html + typing_html + remove_placeholder)

    # No API key configured - just return user message
    return HttpResponse(user_html + remove_placeholder)


@login_required
async def chat_stream(request, conversation_id):
    """SSE endpoint for streaming agent responses."""
    # Verify conversation access (raises DoesNotExist if unauthorized)
    current_org = await sync_to_async(lambda: request.user.current_organization)()
    user = request.user

    async def event_generator():
        """Generate SSE events by running agent directly when needed."""
        logger.info("SSE event_generator started for conversation %s", conversation_id)

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
                async for event in agent_service.stream_response(conversation=conversation):
                    yield _format_sse_event(event=event)
                logger.info(f"Agent completed for conversation {conversation_id}")
                continue

            # No pending message - wait before checking again
            await asyncio.sleep(1)
            # Send keepalive to prevent connection timeout
            yield ": keepalive\n\n"

    response = StreamingHttpResponse(
        event_generator(),
        content_type="text/event-stream",
    )
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
    tool_name = data.get("name", "unknown")
    tool_use_id = data.get("tool_use_id", "")
    return f'''<div id="tool-{tool_use_id}" class="my-2 border border-gray-200 rounded-lg p-3 bg-gray-50">
        <div class="flex items-center gap-2">
            <svg class="w-4 h-4 text-blue-500 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
            <span class="text-sm font-medium text-gray-700">Running {tool_name}...</span>
        </div>
    </div>'''


def _render_tool_result(data: dict) -> str:
    """Render HTML for tool execution result (OOB swap)."""
    tool_name = data.get("name", "unknown")
    tool_use_id = data.get("tool_use_id", "")
    status = data.get("status", "success")
    duration_ms = data.get("duration_ms", 0)

    icon = "✓" if status == "success" else "✗"
    color = "text-green-600" if status == "success" else "text-red-600"

    return f'''<div id="tool-{tool_use_id}" hx-swap-oob="outerHTML">
        <div class="my-2 border border-gray-200 rounded-lg p-3 bg-gray-50">
            <div class="flex items-center gap-2">
                <span class="{color}">{icon}</span>
                <span class="text-sm font-medium text-gray-700">{tool_name}</span>
                <span class="text-xs text-gray-500">{duration_ms}ms</span>
            </div>
        </div>
    </div>'''


def _render_streaming_start() -> str:
    """Render HTML for streaming message container."""
    # Styling matches _message.html agent message structure
    html = '<div id="streaming-message" class="flex items-start space-x-3 max-w-[80%] mb-4 streaming-active"><div class="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center"><svg class="w-5 h-5 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg></div><div class="bg-gray-100 dark:bg-gray-800 rounded-2xl rounded-tl-md px-4 py-3 min-w-0 flex-1"><p id="streaming-text" class="text-gray-900 dark:text-gray-100 whitespace-pre-wrap"></p><span class="streaming-cursor"></span></div></div>'
    # Remove typing indicator
    html += '<div id="typing-indicator" hx-swap-oob="outerHTML"></div>'
    return html


def _render_streaming_error(error_msg: str) -> str:
    """Render HTML for streaming error message."""
    return f'<div id="streaming-message" hx-swap-oob="outerHTML"><div class="text-red-600 p-3 bg-red-50 rounded-lg">Error: {error_msg}</div></div>'


def _format_sse_event(event: StreamEvent) -> str:
    """Convert StreamEvent to SSE format."""
    if event.type == "start":
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
