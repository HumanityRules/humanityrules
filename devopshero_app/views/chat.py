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

    # OOB delete the 'empty chat' placeholder (if present)
    remove_placeholder = '<div id="empty-chat-placeholder" hx-swap-oob="delete"></div>'

    # The SSE connection (chat_stream) will run the agent and show the thinking indicator
    if agent_client.is_available():
        return HttpResponse(user_html + remove_placeholder)

    # Agent unavailable - log error and inform user
    logger.error("Agent client unavailable")
    unavailable_html = '''<div class="flex items-start space-x-3 max-w-[80%] mb-4">
        <div class="flex-shrink-0 w-8 h-8 bg-amber-100 dark:bg-amber-900 rounded-full flex items-center justify-center">
            <svg class="w-5 h-5 text-amber-600 dark:text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
            </svg>
        </div>
        <div class="bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800 rounded-2xl rounded-tl-md px-4 py-3">
            <p class="text-amber-800 dark:text-amber-200">AI assistant is currently unavailable. Please contact your administrator.</p>
        </div>
    </div>'''
    return HttpResponse(user_html + unavailable_html + remove_placeholder)


@login_required
async def chat_stream(request, conversation_id):
    """SSE endpoint for streaming agent responses."""
    # Check agent availability before starting stream
    if not agent_client.is_available():
        logger.error("Agent client unavailable")

        async def unavailable_generator():
            yield _format_sse(
                event_name="sse-error",
                data=_render_streaming_error(error_msg="AI assistant is currently unavailable"),
            )

        response = StreamingHttpResponse(
            unavailable_generator(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

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
    # Reset thinking indicator to empty placeholder via OOB (so it can be reused)
    oob_reset = '<div id="thinking-indicator" hx-swap-oob="outerHTML"></div>'
    return f'''<div id="tool-{tool_use_id}">
<div class="flex items-start space-x-3 max-w-[95%] mb-4">
    <div class="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center">
        <svg class="w-5 h-5 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
        </svg>
    </div>
    <div class="min-w-0 flex-1">
        <div class="border border-gray-200 dark:border-gray-700 overflow-hidden rounded-lg w-full min-w-0">
            <div class="flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-700">
                <div class="flex items-center space-x-2">
                    <svg class="w-4 h-4 text-blue-500 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
                    </svg>
                    <span class="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">{tool_name}</span>
                </div>
            </div>
        </div>
    </div>
</div>
</div>
{oob_reset}'''


def _extract_mcp_text_content(value):
    """Extract text content from MCP content block structure.

    MCP tool results come as: [{"type": "text", "text": "..."}]
    This extracts the text and tries to parse it as JSON.
    """
    if not isinstance(value, list) or len(value) == 0:
        return value

    # Extract text from all text blocks
    texts = []
    for block in value:
        if isinstance(block, dict) and block.get("type") == "text" and "text" in block:
            texts.append(block["text"])

    if not texts:
        return value

    combined_text = "\n".join(texts)

    # Try to parse as JSON
    try:
        return json.loads(combined_text)
    except json.JSONDecodeError:
        return combined_text


def _render_tool_result(data: dict) -> str:
    """Render HTML for tool execution result (OOB swap)."""
    tool_name = data.get("name", "unknown")
    tool_use_id = data.get("tool_use_id", "")
    status = data.get("status", "success")
    duration_ms = data.get("duration_ms", 0)
    parameters = data.get("input", {})
    result = data.get("result", "")

    # Format JSON for display
    params_json = json.dumps(parameters, indent=2) if parameters else "{}"

    # Parse result, extract MCP text content, and pretty-print
    try:
        result_parsed = json.loads(result) if isinstance(result, str) else result
        result_parsed = _extract_mcp_text_content(result_parsed)
        if isinstance(result_parsed, str):
            result_json = result_parsed
        else:
            result_json = json.dumps(result_parsed, indent=2)
    except (json.JSONDecodeError, TypeError):
        result_json = str(result)

    status_icon = (
        '<svg class="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20">'
        '<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/>'
        '</svg>'
        if status == "success"
        else '<svg class="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 20 20">'
        '<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/>'
        '</svg>'
    )

    return f'''<div id="tool-{tool_use_id}" hx-swap-oob="outerHTML">
<div class="flex items-start space-x-3 max-w-[95%] mb-4">
    <div class="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center">
        <svg class="w-5 h-5 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
        </svg>
    </div>
    <div class="min-w-0 flex-1">
        <div class="border border-gray-200 dark:border-gray-700 overflow-hidden rounded-lg w-full min-w-0">
            <div class="flex items-center justify-between px-3 py-2 bg-gray-50 dark:bg-gray-700">
                <div class="flex items-center space-x-2">
                    {status_icon}
                    <span class="font-mono text-sm font-medium text-gray-900 dark:text-gray-100">{tool_name}</span>
                </div>
                <span class="text-xs text-gray-500">{duration_ms}ms</span>
            </div>
            <div class="p-3 text-sm space-y-3 min-w-0">
                <div class="min-w-0">
                    <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Parameters</div>
                    <pre class="bg-gray-100 dark:bg-gray-900 p-2 rounded text-xs overflow-x-auto font-mono text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-all">{params_json}</pre>
                </div>
                <div class="min-w-0">
                    <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Result</div>
                    <pre class="bg-gray-100 dark:bg-gray-900 p-2 rounded text-xs overflow-x-auto max-h-64 overflow-y-auto font-mono text-gray-800 dark:text-gray-200 whitespace-pre-wrap break-all">{result_json}</pre>
                </div>
            </div>
        </div>
    </div>
</div>
</div>'''


def _render_thinking() -> str:
    """Render HTML for 'agent is thinking' indicator (OOB swap into placeholder)."""
    # Use OOB swap to replace the thinking-indicator placeholder (avoids duplicates)
    return '''<div id="thinking-indicator" hx-swap-oob="outerHTML" class="flex items-start space-x-3 max-w-[80%] mb-4">
<div class="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center">
    <svg class="w-5 h-5 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
    </svg>
</div>
<div class="bg-gray-100 dark:bg-gray-800 rounded-2xl rounded-tl-md px-4 py-3">
    <div class="flex items-center space-x-2">
        <div class="flex space-x-1">
            <span class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style="animation-delay: 0ms;"></span>
            <span class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style="animation-delay: 150ms;"></span>
            <span class="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full animate-bounce" style="animation-delay: 300ms;"></span>
        </div>
        <span class="text-sm text-gray-500 dark:text-gray-400">Thinking...</span>
    </div>
</div>
</div>
'''


def _render_streaming_start() -> str:
    """Render HTML for streaming message container."""
    # Styling matches _message.html agent message structure
    html = '<div id="streaming-message" class="flex items-start space-x-3 max-w-[80%] mb-4 streaming-active"><div class="flex-shrink-0 w-8 h-8 bg-indigo-100 dark:bg-indigo-900 rounded-full flex items-center justify-center"><svg class="w-5 h-5 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg></div><div class="bg-gray-100 dark:bg-gray-800 rounded-2xl rounded-tl-md px-4 py-3 min-w-0 flex-1"><p id="streaming-text" class="text-gray-900 dark:text-gray-100 whitespace-pre-wrap"><span class="streaming-cursor"></span></p></div></div>'
    # Reset thinking indicator to empty placeholder via OOB (so it can be reused)
    html += '<div id="thinking-indicator" hx-swap-oob="outerHTML"></div>'
    return html


def _render_streaming_error(error_msg: str) -> str:
    """Render HTML for streaming error message."""
    return f'<div id="streaming-message" hx-swap-oob="outerHTML"><div class="text-red-600 p-3 bg-red-50 rounded-lg">Error: {error_msg}</div></div>'


def _format_sse_event(event: StreamEvent) -> str:
    """Convert StreamEvent to SSE format."""
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
