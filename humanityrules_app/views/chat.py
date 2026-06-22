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

from django.db.models import Sum

from ..models import Conversation, Message
from ..services.agent import agent_client
from ..services.agent import agent_runner
from ..services.agent import agent_service
from ..services.agent import mcp_tools
from ..templatetags import chat_filters
from . import base

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


def _get_conversations(user, annotate_costs):
    """Get all conversations for a user in their current organization."""
    qs = Conversation.objects.filter(
        user=user,
        organization=user.current_organization,
    ).select_related(
        "context_workspace", "context_repository", "context_aws_account", "context_environment",
    ).order_by("-updated_at")
    if annotate_costs:
        qs = qs.annotate(total_cost=Sum("llm_usage_logs__cost_usd"))
    return qs


@login_required
def chat_list(request):
    """Show unified chat interface with no conversation selected."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="chat")
        context["content_url"] = "/chat/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    show_costs = request.user.is_staff
    conversations = _get_conversations(user=request.user, annotate_costs=show_costs)

    context = base.get_app_shell_context(request=request, current_page="chat")
    context["conversations"] = conversations
    context["conversation"] = None
    context["messages"] = []
    context["show_costs"] = show_costs

    return render(request, "humanityrules_app/chat/chat.html", context=context)


@login_required
def chat_new(request):
    """Create a new conversation and redirect to it."""
    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=request.GET.get("workspace") or None,
        repo_id=request.GET.get("repo") or None,
        aws_account_id=request.GET.get("aws_account") or None,
        mode=None,
        app_permission_request_id=None,
    )

    return redirect("chat_view", conversation_id=conversation.id)


@login_required
@base.require_agent_deployments
def chat_app_deploy(request, workspace_slug, repo_name, repo_owner=None):
    """Shortcut: create an APP_DEPLOYMENT conversation from human-readable URL segments."""
    from ..models import Repository, Workspace

    org = request.user.current_organization
    workspace = get_object_or_404(Workspace, organization=org, slug=workspace_slug)
    full_name = f"{repo_owner}/{repo_name}" if repo_owner else repo_name
    repo = get_object_or_404(Repository, organization=org, full_name__iexact=full_name)

    conversation = agent_service.create_conversation(
        user=request.user,
        workspace_id=workspace.id,
        repo_id=repo.id,
        aws_account_id=None,
        mode=None,
        app_permission_request_id=None,
    )

    return redirect("chat_view", conversation_id=conversation.id)


@login_required
def chat_view(request, conversation_id):
    """View a specific conversation in the unified chat interface."""
    if not request.htmx:
        context = base.get_app_shell_context(request=request, current_page="chat")
        context["content_url"] = f"/chat/{conversation_id}/"
        return render(request, "humanityrules_app/app_shell.html", context=context)

    conversation = get_object_or_404(
        Conversation.objects.select_related(
            "context_workspace", "context_repository", "context_aws_account", "context_environment",
        ),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )

    messages = conversation.messages.exclude(content_type=Message.ContentType.SYSTEM_TRIGGER).order_by("created_at")

    context = base.get_app_shell_context(request=request, current_page="chat")
    context["conversation"] = conversation
    context["messages"] = messages

    # HTMX request targeting the chat panel - return just the panel content
    if request.htmx.target == "chat-panel":
        return render(request, "humanityrules_app/chat/_chat_panel.html", context=context)

    # Full HTMX navigation - need full unified template
    show_costs = request.user.is_staff
    context["conversations"] = _get_conversations(user=request.user, annotate_costs=show_costs)
    context["show_costs"] = show_costs

    return render(request, "humanityrules_app/chat/chat.html", context=context)


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

    if not message_text:
        return HttpResponse(status=400)

    # If there's a pending AskUserQuestion, signal the answer to the blocked callback
    runner = agent_runner.get_runner(conversation.id)
    agent = runner.agent if runner else None
    pending = agent.pending_question if agent else None
    if pending:
        raw_answers = request.POST.get("question_answers")
        if raw_answers:
            answers = json.loads(raw_answers)
        else:
            first_q = pending.questions[0]["question"] if pending.questions else ""
            answers = {first_q: message_text}
        agent.submit_question_answer(answers=answers)

    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.USER,
        content_type=Message.ContentType.TEXT,
        content=message_text,
    )

    # Update conversation timestamp
    conversation.save()

    # Render the user message
    context = {"message": user_message, "conversation_id": conversation_id}
    user_html = render_to_string(
        "humanityrules_app/chat/_message.html",
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
    unavailable_html = render_to_string("humanityrules_app/chat/_streaming_unavailable.html")
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

    show_costs = user.is_staff

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
                        # Sentinel: runner finished — send sse-close so the HTMX SSE
                        # extension closes the EventSource cleanly (prevents the
                        # browser's automatic reconnection).
                        logger.info(f"Agent runner completed for conversation {conversation_id}")
                        yield _format_sse(event_name="sse-close", data="")
                        break
                    yield _format_sse_event(event=event, show_costs=show_costs)
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
            # Return DB connections to the pool. StreamingHttpResponse generators run after
            # the view returns, so Django's request_finished signal (which normally returns
            # connections to the pool) doesn't cover them. Without this, connections opened
            # during iteration leak from the pool. With psycopg3's pool=True, close_all()
            # returns connections to the pool rather than truly closing them.
            # See also: agent_runner.py's _run_agent_loop() — both cleanups are required.
            conn_before = await sync_to_async(_get_db_connection_count, thread_sensitive=True)()
            await sync_to_async(connections.close_all, thread_sensitive=True)()
            logger.info(f"[CONN] event_generator CLEANUP {conversation_id}: db_connections {conn_before}")

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _render_streaming_tool_start(agent_streaming_event_data: dict) -> str:
    """Render HTML for tool execution start."""
    tool_full_name = agent_streaming_event_data.get("name", "unknown")
    input_params = agent_streaming_event_data.get("input", {})

    tool_name = mcp_tools.get_tool_display_name(tool_full_name, input_params)
    tool_input_param_for_title = mcp_tools.get_tool_input_param_for_title(tool_full_name, input_params)
    if tool_input_param_for_title:
        tool_name = f"{tool_name}: "

    return render_to_string("humanityrules_app/chat/_streaming_tool_start.html", context={
        "tool_name": tool_name,
        "tool_input_param_for_title": tool_input_param_for_title,
        "tool_use_id": agent_streaming_event_data.get("tool_use_id", ""),
        "params": input_params,
    })


def _render_streaming_tool_result(agent_streaming_event_data: dict) -> str:
    """Render HTML for tool execution result."""
    tool_full_name = agent_streaming_event_data.get("name", "unknown")
    input_params = agent_streaming_event_data.get("input", {})
    result = agent_streaming_event_data.get("result", "")
    
    tool_name = mcp_tools.get_tool_display_name(tool_full_name, input_params)
    tool_input_param_for_title = mcp_tools.get_tool_input_param_for_title(tool_full_name, input_params)
    if tool_input_param_for_title:
        tool_name = f"{tool_name}: "

    # Override status when the tool reports business-logic failure via a success field.
    # The MCP-level is_error only covers tool crashes, not domain failures like a failed build.
    status = agent_streaming_event_data.get("status", "success")
    if status == "success" and isinstance(result, dict) and result.get("success") is False:
        status = "error"

    return render_to_string("humanityrules_app/chat/_streaming_tool_result.html", context={
        "tool_name": tool_name,
        "tool_input_param_for_title": tool_input_param_for_title,
        "tool_use_id": agent_streaming_event_data.get("tool_use_id", ""),
        "status": status,
        "duration_ms": agent_streaming_event_data.get("duration_ms", 0),
        "params": input_params,
        "tool_result": result,
        "custom_result_template": chat_filters.TOOL_RESULT_TEMPLATES.get(tool_full_name, ""),
    })


def _render_streaming_question(agent_streaming_event_data: dict) -> str:
    """Render HTML for an AskUserQuestion interactive choice UI."""
    return render_to_string("humanityrules_app/chat/_streaming_question.html", context={
        "questions": agent_streaming_event_data.get("questions", []),
        "conversation_id": agent_streaming_event_data.get("conversation_id", ""),
    })


def _render_streaming_thinking() -> str:
    """Render HTML for 'agent is thinking' indicator (OOB swap into placeholder)."""
    return render_to_string("humanityrules_app/chat/_streaming_thinking.html")


def _render_streaming_start() -> str:
    """Render HTML for streaming message container."""
    return render_to_string("humanityrules_app/chat/_streaming_start.html")


def _render_streaming_error(error_msg: str) -> str:
    """Render HTML for streaming error message."""
    return render_to_string("humanityrules_app/chat/_streaming_error.html", context={
        "error_msg": error_msg,
    })


def _format_sse(event_name: str, data: str) -> str:
    """Format data as SSE event."""
    sse_data = "\n".join(f"data: {line}" for line in data.split("\n"))
    return f"event: {event_name}\n{sse_data}\n\n"


def _format_sse_notify(notification_event: str, **kwargs) -> str:
    """Format an sse-notify event with the final DOM event name."""
    return _format_sse(event_name="sse-notify", data=json.dumps({"event": notification_event, **kwargs}))


def _format_sse_event(event: agent_service.AgentStreamEvent, show_costs: bool) -> str:
    """Convert agent_service.AgentStreamEvent to SSE format."""
    if event.type == "thinking":
        return _format_sse(event_name="sse-thinking", data=_render_streaming_thinking())
    elif event.type == "start":
        return _format_sse(event_name="sse-start", data=_render_streaming_start())
    elif event.type == "text_delta":
        return _format_sse(event_name="sse-text-delta", data=json.dumps(event.data))
    elif event.type == "text_flush":
        return _format_sse(event_name="sse-text-flush", data="{}")
    elif event.type == "question":
        return _format_sse(event_name="sse-question", data=_render_streaming_question(event.data))
    elif event.type == "tool_start":
        return _format_sse(event_name="sse-tool-start", data=_render_streaming_tool_start(event.data))
    elif event.type == "tool_result":
        tool_name = event.data["name"]
        tool_result = event.data["result"]
        result = _format_sse(event_name="sse-tool-result", data=_render_streaming_tool_result(event.data))
        if not isinstance(tool_result, dict):
            return result
        if tool_name == "mcp__humanityrules__update_permission_draft":
            result += _format_sse_notify(f"permissions-changed-{tool_result['app_permission_request_id']}")
        if tool_name == "mcp__humanityrules__save_app":
            if tool_result.get("created"):
                result += _format_sse_notify("app-created", slug=tool_result.get("slug", ""))
            else:
                result += _format_sse_notify(f"app-changed-{tool_result['id']}")
        if tool_name == "mcp__humanityrules__save_environment":
            if tool_result.get("created"):
                result += _format_sse_notify("environment-created", environment_id=tool_result.get("id", ""))
            result += _format_sse_notify(f"environment-changed-{tool_result['id']}")
        if tool_name == "mcp__humanityrules__provision_environment":
            result += _format_sse_notify(f"environment-changed-{tool_result['id']}")
        if tool_name in ("mcp__humanityrules__save_blueprint", "mcp__humanityrules__deploy_blueprint"):
            result += _format_sse_notify(f"blueprint-changed-{tool_result['app_id']}")
        return result
    elif event.type == "complete":
        result = _format_sse(event_name="sse-complete", data="")
        conversation_id = str(event.data["conversation_id"])
        if event.data.get("title"):
            result += _format_sse_notify(f"title-changed-{conversation_id}")
        if show_costs and event.data.get("total_cost"):
            result += _format_sse_notify(f"cost-changed-{conversation_id}")
        return result
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

    messages = conversation.messages.exclude(content_type=Message.ContentType.SYSTEM_TRIGGER).order_by("created_at")[offset : offset + limit]

    context = {"messages": messages, "conversation_id": conversation_id}

    # Render all messages
    html_parts = []
    for message in messages:
        context["message"] = message
        html_parts.append(
            render_to_string(
                "humanityrules_app/chat/_message.html",
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


@login_required
def chat_fork(request, conversation_id):
    """Fork a conversation: create a new conversation that branches from the source's agent session."""
    source = get_object_or_404(
        Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )
    if not source.session_id:
        return HttpResponse("Cannot fork: conversation has no agent session yet.", status=400)

    forked = Conversation.objects.create(
        user=request.user,
        organization=request.user.current_organization,
        mode=source.mode,
        context_workspace=source.context_workspace,
        context_repository=source.context_repository,
        context_aws_account=source.context_aws_account,
        context_environment=source.context_environment,
        session_id=source.session_id,
        status=Conversation.Status.ACTIVE,
    )
    return redirect("chat_view", conversation_id=forked.id)


@login_required
def chat_conversation_title(request, conversation_id):
    """Return conversation title text for HTMX refetch."""
    conversation = get_object_or_404(
        Conversation,
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )
    return HttpResponse(conversation.title or "New Conversation")


@login_required
def chat_conversation_cost(request, conversation_id):
    """Return conversation cost HTML fragment for HTMX refetch."""
    conversation = get_object_or_404(
        Conversation.objects.annotate(total_cost=Sum("llm_usage_logs__cost_usd")),
        id=conversation_id,
        user=request.user,
        organization=request.user.current_organization,
    )
    if conversation.total_cost:
        return HttpResponse(
            f'<span class="text-gray-400 dark:text-gray-500">Agent Cost:</span> ${conversation.total_cost:.4f}'
        )
    return HttpResponse("")
