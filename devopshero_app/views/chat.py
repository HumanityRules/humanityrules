import logging
import threading
import time

from django.contrib.auth.decorators import login_required
from django.db import close_old_connections
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from ..models import Conversation, Message
from ..services.agent import agent_client
from ..services.agent import process_conversation
from .base import get_app_shell_context

logger = logging.getLogger(__name__)


def _process_agent_in_background(conversation_id: str):
    """
    Process agent response in a background thread.

    This function runs in a separate thread to avoid blocking the request.
    The agent response is saved to the database and picked up by SSE.
    """
    try:
        # Need to close old connections when running in a new thread
        close_old_connections()

        conversation = Conversation.objects.select_related('organization', 'user').get(id=conversation_id)
        process_conversation(conversation)
    except Exception as e:
        logger.exception("Background agent processing failed")
        try:
            # Try to save an error message
            conversation = Conversation.objects.get(id=conversation_id)
            Message.objects.create(
                conversation=conversation,
                role=Message.Role.SYSTEM,
                content_type=Message.ContentType.ERROR,
                content=f"Agent error: {str(e)}",
                metadata={"error_type": type(e).__name__},
            )
        except Exception:
            logger.exception("Failed to save error message")


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
    conversation = get_object_or_404(
        Conversation,
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
    conversation.save()  # Triggers updated_at

    # Render the user message
    context = {"message": user_message, "conversation_id": conversation_id}
    user_html = render_to_string(
        "devopshero_app/chat/_message.html",
        context=context,
        request=request,
    )

    # OOB delete the empty chat placeholder (if present)
    remove_placeholder = '<div id="empty-chat-placeholder" hx-swap-oob="delete"></div>'

    # Start agent processing in background
    if agent_client.is_available():
        thread = threading.Thread(
            target=_process_agent_in_background,
            args=(str(conversation_id),),
            daemon=True,
        )
        thread.start()

        # Include typing indicator that will be shown until agent responds
        typing_html = render_to_string(
            "devopshero_app/chat/_typing_indicator.html",
            context={"conversation_id": conversation_id},
            request=request,
        )
        return HttpResponse(user_html + typing_html + remove_placeholder)

    # No API key configured - just return user message
    return HttpResponse(user_html + remove_placeholder)


@login_required
def chat_stream(request, conversation_id):
    """SSE endpoint for streaming new messages."""
    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    def event_generator():
        """Generate SSE events for new messages."""
        # Get initial set of message IDs to track what's new
        seen_ids = set(
            conversation.messages.values_list("id", flat=True)
        )

        while True:
            # Refresh from database
            conversation.refresh_from_db()

            # Check for new messages
            new_messages = conversation.messages.exclude(
                id__in=seen_ids
            ).order_by("created_at")

            for message in new_messages:
                seen_ids.add(message.id)

                # Skip user messages - they're already added by form submission
                if message.role == Message.Role.USER:
                    continue

                # Render message partial
                context = {"message": message, "conversation_id": conversation_id}
                html = render_to_string(
                    "devopshero_app/chat/_message.html",
                    context=context,
                    request=request,
                )

                # Clear the typing indicator (keep element as empty placeholder for next time)
                remove_typing = '<div id="typing-indicator" hx-swap-oob="outerHTML"></div>'
                full_html = html + remove_typing

                # SSE multi-line format: prefix each line with "data: "
                sse_data = "\n".join(f"data: {line}" for line in full_html.split("\n"))
                yield f"event: new-chat-message\n{sse_data}\n\n"

            # Sleep before checking again
            time.sleep(1)

    response = StreamingHttpResponse(
        event_generator(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


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
