import json
import time

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from ..models import Conversation, Message
from .base import get_app_shell_context


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
        return render(request, "devopshero_app/chat_list.html", context=context)

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
        return render(request, "devopshero_app/chat.html", context=context)

    context["content_url"] = f"/chat/{conversation_id}/"
    return render(request, "devopshero_app/app_shell.html", context=context)


@login_required
@require_POST
def chat_send(request, conversation_id):
    """Send a message in a conversation."""
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

    # Render and return the user message partial
    context = {"message": user_message, "conversation_id": conversation_id}
    html = render_to_string(
        "devopshero_app/partials/chat/_message.html",
        context=context,
        request=request,
    )

    return HttpResponse(html)


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
        last_message_id = request.GET.get("last_id")

        # Get initial set of message IDs to track what's new
        seen_ids = set(
            conversation.messages.values_list("id", flat=True)
        )

        while True:
            # Check for new messages
            new_messages = conversation.messages.exclude(
                id__in=seen_ids
            ).order_by("created_at")

            for message in new_messages:
                seen_ids.add(message.id)

                # Render message partial
                context = {"message": message, "conversation_id": conversation_id}
                html = render_to_string(
                    "devopshero_app/partials/chat/_message.html",
                    context=context,
                    request=request,
                )

                # Send SSE event
                yield f"event: message\ndata: {html}\n\n"

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
                "devopshero_app/partials/chat/_message.html",
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
