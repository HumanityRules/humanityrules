"""
CLI harness for the main DevOps Hero agent.

Usage:
  # Start a fresh conversation (default: mode=app_deployment, workspace=default, repo=vmendi/ai-detector-and-humanizer)
  uv run python -m devopshero_app.services.agent.test_main_agent --prompt "Deploy"

  # Customize workspace and repo context
  uv run python -m devopshero_app.services.agent.test_main_agent --workspace myws --repo acme/flask-api --prompt "Deploy"

  # General mode with no context
  uv run python -m devopshero_app.services.agent.test_main_agent --no-context --prompt "Hello"

  # Override mode explicitly
  uv run python -m devopshero_app.services.agent.test_main_agent --mode general --prompt "Hello"

  # Fork from a conversation (default when --conversation-id provided)
  uv run python -m devopshero_app.services.agent.test_main_agent --conversation-id <uuid> --prompt "Try this"

  # Resume a conversation in place
  uv run python -m devopshero_app.services.agent.test_main_agent --conversation-id <uuid> --no-fork --prompt "Continue"

  # Interactive REPL mode
  uv run python -m devopshero_app.services.agent.test_main_agent --repl

Database snapshotting:
  - The harness copies a base SQLite DB to a per-run DB file.
  - Use --db-base to point at the base DB (default: ./local/db.sqlite3).
  - Use --db-run to choose the run DB path (default: timestamped copy in local/test_db/).
  - Use --no-copy to skip the copy and use --db-run (or --db-base) directly.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



def _get_project_root() -> Path:
    current = Path(__file__).resolve()
    return current.parent.parent.parent.parent


def _build_run_db_path(base_path: Path, run_path_arg: str | None) -> Path:
    if run_path_arg:
        return Path(run_path_arg)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = base_path.suffix or ".sqlite3"
    return Path("local/test_db") / f"{base_path.stem}.harness-{timestamp}{suffix}"


def _copy_sqlite_db(base_path: Path, run_path: Path) -> None:
    if not base_path.exists():
        raise RuntimeError(f"Base DB not found: {base_path}")
    if base_path.resolve() == run_path.resolve():
        raise RuntimeError("Run DB path must differ from base DB path.")
    run_path.parent.mkdir(parents=True, exist_ok=True)
    if run_path.exists():
        run_path.unlink()

    source = sqlite3.connect(str(base_path))
    dest = sqlite3.connect(str(run_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _setup_django(db_path: Path) -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "devopshero_site.settings")
    os.environ["DOH_DB_PATH"] = str(db_path)
    import django

    django.setup()


def _parse_args(project_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="CLI harness for the DevOps Hero main agent."
    )
    parser.add_argument(
        "--conversation-id",
        type=str,
        help="Source conversation UUID to fork from or resume. If omitted, starts fresh.",
    )
    parser.add_argument(
        "--fork",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fork to a new conversation (default). Use --no-fork to resume in place.",
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--prompt",
        type=str,
        help="Single prompt to run and exit.",
    )
    input_group.add_argument(
        "--repl",
        action="store_true",
        help="Interactive prompt loop.",
    )

    parser.add_argument(
        "--user-email",
        type=str,
        help="User email to run the agent as (defaults to first user).",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        help="User ID to run the agent as (overrides --user-email).",
    )
    parser.add_argument(
        "--db-base",
        type=str,
        default=str(project_root / "local" / "db.sqlite3"),
        help="Base SQLite DB path to copy from.",
    )
    parser.add_argument(
        "--db-run",
        type=str,
        help="Run DB path to use (defaults to timestamped copy in local/test_db/).",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Skip DB copy; use --db-run path directly (keeps state across runs).",
    )
    parser.add_argument(
        "--show-tool-io",
        action="store_true",
        help="Show tool parameters and results in full.",
    )

    # Conversation context (matches what the web UI sets via query params)
    parser.add_argument(
        "--workspace",
        type=str,
        default="default",
        help="Workspace name or UUID (default: 'default').",
    )
    parser.add_argument(
        "--repo",
        type=str,
        default="vmendi/ai-detector-and-humanizer",
        help="Repository full_name or UUID (default: 'vmendi/ai-detector-and-humanizer').",
    )
    parser.add_argument(
        "--aws-account",
        type=str,
        help="AWS account name or UUID (for environment_setup mode).",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Skip setting workspace/repo/aws-account context (pure GENERAL mode).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["general", "app_deployment", "environment_setup"],
        help="Override conversation mode (default: auto-derived from context).",
    )

    return parser.parse_args()


@dataclass
class PrintState:
    in_text_stream: bool


def _format_tool_label(tool_name: str, parameters: dict[str, Any]) -> str:
    import devopshero_app.services.agent.mcp_tools as mcp_tools

    display_name = mcp_tools.get_tool_display_name(tool_name, parameters)
    title_param = mcp_tools.get_tool_input_param_for_title(tool_name, parameters)
    if title_param:
        return f"{display_name}: {title_param}"
    return display_name


def _render_tool_payload(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, default=str)


def _print_event(event: Any, state: PrintState, show_tool_io: bool) -> None:
    if event.type == "text_delta":
        sys.stdout.write(event.data.get("text", ""))
        sys.stdout.flush()
        state.in_text_stream = True
        return

    if event.type == "text_flush":
        if state.in_text_stream:
            sys.stdout.write("\n")
            sys.stdout.flush()
        state.in_text_stream = False
        return

    if event.type == "complete":
        if state.in_text_stream:
            sys.stdout.write("\n")
            sys.stdout.flush()
        state.in_text_stream = False
        return

    if event.type == "tool_start":
        if state.in_text_stream:
            sys.stdout.write("\n")
            sys.stdout.flush()
        state.in_text_stream = False
        tool_name = event.data.get("name", "unknown")
        parameters = event.data.get("input", {})
        label = _format_tool_label(tool_name=tool_name, parameters=parameters)
        print(f"[tool:start] {label}")
        if show_tool_io:
            print(_render_tool_payload(parameters))
        return

    if event.type == "tool_result":
        if state.in_text_stream:
            sys.stdout.write("\n")
            sys.stdout.flush()
        state.in_text_stream = False
        tool_name = event.data.get("name", "unknown")
        parameters = event.data.get("input", {})
        status = event.data.get("status", "success")
        duration_ms = event.data.get("duration_ms", 0)
        label = _format_tool_label(tool_name=tool_name, parameters=parameters)
        print(f"[tool:done] {label} status={status} duration_ms={duration_ms}")
        if show_tool_io or status == "error":
            result = event.data.get("result", "")
            print(_render_tool_payload(result))
        return

    if event.type == "error":
        error_text = event.data.get("error", "Unknown error") if event.data else "Unknown error"
        print(f"[error] {error_text}", file=sys.stderr)
        state.in_text_stream = False


async def _aget_user(user_email: str | None, user_id: str | None):
    from devopshero_app.models import User

    query = User.objects.select_related("current_organization")
    if user_id:
        return await query.aget(id=user_id)
    if user_email:
        return await query.aget(email=user_email)
    user = await query.order_by("date_joined").afirst()
    if not user:
        raise RuntimeError("No users found in the database.")
    return user


async def _resolve_conversation_for_fork(user, source_id: str):
    """Fork from a source conversation (creates new conversation, copies session_id)."""
    from devopshero_app.models import Conversation

    source = await Conversation.objects.select_related("organization").aget(
        id=source_id,
        user=user,
        organization=user.current_organization,
    )
    if not source.session_id:
        raise RuntimeError("Cannot fork: source conversation has no session_id yet.")
    else:
        print(f"Forking from source conversation: {source.id} session_id={source.session_id}")

    conversation = await Conversation.objects.acreate(
        user=user,
        organization=user.current_organization,
        mode=source.mode,
        context_workspace_id=source.context_workspace_id,
        context_repository_id=source.context_repository_id,
        context_aws_account_id=source.context_aws_account_id,
        context_environment_id=source.context_environment_id,
        session_id=source.session_id,
        status=Conversation.Status.ACTIVE,
    )
    return conversation


async def _resolve_conversation_for_resume(user, conversation_id: str):
    """Resume an existing conversation in place."""
    from devopshero_app.models import Conversation

    conversation = await Conversation.objects.select_related("organization").aget(
        id=conversation_id,
        user=user,
        organization=user.current_organization,
    )
    return conversation


async def _resolve_workspace(user, workspace_arg: str | None):
    """Resolve workspace by name or UUID, returns ID or None."""
    if not workspace_arg:
        return None
    from devopshero_app.models import Workspace

    qs = Workspace.objects.filter(organization=user.current_organization)
    # Try UUID first
    try:
        import uuid as _uuid
        _uuid.UUID(workspace_arg)
        return (await qs.aget(id=workspace_arg)).id
    except (ValueError, Workspace.DoesNotExist):
        pass
    # Try by name (case-insensitive)
    ws = await qs.filter(name__iexact=workspace_arg).afirst()
    if not ws:
        raise RuntimeError(f"Workspace not found: {workspace_arg}")
    return ws.id


async def _resolve_repository(user, repo_arg: str | None):
    """Resolve repository by full_name or UUID, returns ID or None."""
    if not repo_arg:
        return None
    from devopshero_app.models import Repository

    qs = Repository.objects.filter(organization=user.current_organization)
    # Try UUID first
    try:
        import uuid as _uuid
        _uuid.UUID(repo_arg)
        return (await qs.aget(id=repo_arg)).id
    except (ValueError, Repository.DoesNotExist):
        pass
    # Try by full_name (case-insensitive)
    repo = await qs.filter(full_name__iexact=repo_arg).afirst()
    if not repo:
        raise RuntimeError(f"Repository not found: {repo_arg}")
    return repo.id


async def _resolve_aws_account(user, aws_account_arg: str | None):
    """Resolve AWS account by name or UUID, returns ID or None."""
    if not aws_account_arg:
        return None
    from devopshero_app.models import AWSAccount

    qs = AWSAccount.objects.filter(organization=user.current_organization)
    # Try UUID first
    try:
        import uuid as _uuid
        _uuid.UUID(aws_account_arg)
        return (await qs.aget(id=aws_account_arg)).id
    except (ValueError, AWSAccount.DoesNotExist):
        pass
    # Try by name (case-insensitive)
    account = await qs.filter(name__iexact=aws_account_arg).afirst()
    if not account:
        raise RuntimeError(f"AWS account not found: {aws_account_arg}")
    return account.id


async def _create_new_conversation(user, workspace_id, repo_id, aws_account_id, mode: str):
    """Create a fresh conversation with context and trigger message."""
    from asgiref.sync import sync_to_async

    from devopshero_app.services.agent import agent_service

    conversation = await sync_to_async(agent_service.create_conversation)(
        user=user,
        workspace_id=workspace_id,
        repo_id=repo_id,
        aws_account_id=aws_account_id,
        mode=mode,
        app_permission_request_id=None,
    )
    return conversation


async def _create_user_message(conversation, content: str) -> None:
    from devopshero_app.models import Message

    await Message.objects.acreate(
        conversation=conversation,
        role=Message.Role.USER,
        content_type=Message.ContentType.TEXT,
        content=content,
        metadata={},
    )
    await conversation.asave()


async def _stream_agent(conversation, agent, show_tool_io: bool) -> None:
    """Stream agent response for the current last message in the conversation."""
    from devopshero_app.services.agent.agent_runner import get_pending_user_message

    user_message = await get_pending_user_message(conversation)
    if user_message is None:
        raise ValueError("No pending user message to stream")
    state = PrintState(in_text_stream=False)
    async for event in agent.stream_turn(conversation=conversation, user_message=user_message):
        _print_event(event=event, state=state, show_tool_io=show_tool_io)


async def _run_agent_once(conversation, prompt: str, agent, show_tool_io: bool) -> None:
    """Create a user message then stream the agent response."""
    await _create_user_message(conversation=conversation, content=prompt)
    await _stream_agent(conversation=conversation, agent=agent, show_tool_io=show_tool_io)


async def _run_repl(conversation, agent, show_tool_io: bool) -> None:
    while True:
        try:
            prompt = input("you> ").strip()
        except EOFError:
            print()
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            break
        await _run_agent_once(
            conversation=conversation,
            prompt=prompt,
            agent=agent,
            show_tool_io=show_tool_io,
        )
        print(f"[conversation] {conversation.id}")


async def _run(args: argparse.Namespace, run_db_path: Path) -> int:
    user = await _aget_user(user_email=args.user_email, user_id=args.user_id)
    if not args.conversation_id:
        # Resolve context from CLI args
        if args.no_context:
            workspace_id, repo_id, aws_account_id = None, None, None
        else:
            workspace_id = await _resolve_workspace(user=user, workspace_arg=args.workspace)
            repo_id = await _resolve_repository(user=user, repo_arg=args.repo)
            aws_account_id = await _resolve_aws_account(user=user, aws_account_arg=args.aws_account)

        conversation = await _create_new_conversation(
            user=user,
            workspace_id=workspace_id,
            repo_id=repo_id,
            aws_account_id=aws_account_id,
            mode=args.mode,
        )
    elif args.fork:
        conversation = await _resolve_conversation_for_fork(
            user=user,
            source_id=args.conversation_id,
        )
    else:
        conversation = await _resolve_conversation_for_resume(
            user=user,
            conversation_id=args.conversation_id,
        )

    print(f"DB: {run_db_path}")
    print(f"User: {user.email or user.username}")
    print(f"Conversation: {conversation.id}")
    print(f"Mode: {conversation.mode}")
    if conversation.context_workspace_id:
        print(f"Workspace: {conversation.context_workspace_id}")
    if conversation.context_repository_id:
        print(f"Repository: {conversation.context_repository_id}")
    if conversation.context_aws_account_id:
        print(f"AWS Account: {conversation.context_aws_account_id}")
    if conversation.context_environment_id:
        print(f"Environment: {conversation.context_environment_id}")
    if conversation.session_id:
        print(f"Resume session: {conversation.session_id}")

    from devopshero_app.services.agent.agent_service import MainAgent

    agent = await MainAgent.create(conversation)
    try:
        # Auto-start: if conversation has a trigger message, run the agent before user input
        has_trigger = await conversation.messages.filter(
            content_type="system_trigger",
        ).aexists()
        if has_trigger:
            await _stream_agent(conversation=conversation, agent=agent, show_tool_io=args.show_tool_io)

        if args.prompt:
            await _run_agent_once(
                conversation=conversation,
                prompt=args.prompt,
                agent=agent,
                show_tool_io=args.show_tool_io,
            )
            return 0

        await _run_repl(
            conversation=conversation,
            agent=agent,
            show_tool_io=args.show_tool_io,
        )
        return 0
    finally:
        await agent.shutdown()


def main() -> int:
    project_root = _get_project_root()
    args = _parse_args(project_root=project_root)
    base_path = Path(args.db_base).expanduser()

    if args.no_copy:
        run_db_path = Path(args.db_run) if args.db_run else base_path
    else:
        run_db_path = _build_run_db_path(base_path=base_path, run_path_arg=args.db_run)
        _copy_sqlite_db(base_path=base_path, run_path=run_db_path)

    _setup_django(db_path=run_db_path)
    try:
        return asyncio.run(_run(args=args, run_db_path=run_db_path))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
