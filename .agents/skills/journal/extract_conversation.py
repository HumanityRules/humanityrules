#!/usr/bin/env python3
"""Extract conversations from Cursor, Claude CLI, or Codex to markdown files.

Usage:
    # Extract most recent conversation (by message count)
    python extract_conversation.py

    # Extract specific conversation by UUID
    python extract_conversation.py --uuid 001af02e-9395-4fe6-90a2-79be2ad1f059

    # List recent conversations (sorted by size)
    python extract_conversation.py --list

    # Search conversations (searches first user message)
    python extract_conversation.py --list --search "keyword"

    # Full-text search across all messages
    python extract_conversation.py --list --search "keyword" --full

    # Extract to specific output file
    python extract_conversation.py --output /path/to/file.md

    # Force a specific source (cursor, claude-cli, or codex)
    python extract_conversation.py --source claude-cli

Note: The current in-progress conversation may not appear until it's saved.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


CURSOR_STATE_DB = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
CLAUDE_CLI_PROJECTS_DIR = Path.home() / ".claude/projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex/sessions"
CODEX_SESSION_INDEX = Path.home() / ".codex/session_index.jsonl"


# =============================================================================
# Project-root resolution (worktree-aware)
# =============================================================================
#
# Claude Code, Codex, and Cursor all key their session storage off the working
# directory at session start. When a session begins in the main checkout and
# later switches into a git worktree (via EnterWorktree), the session log stays
# under the original mangled-cwd directory while os.getcwd() returns the
# worktree path. Lookups by current cwd alone miss the session.
#
# Resolve to BOTH the current worktree root and the main checkout root so each
# source's lookup logic can search both. Cursor stores everything in a single
# global SQLite DB with no cwd filtering, so it doesn't need this.

def project_roots() -> list[Path]:
    """Return git roots to search: current worktree first, then main checkout.

    Falls back to [cwd] when not in a git repo. Each path is resolved and the
    list is deduped while preserving order.
    """
    roots: list[Path] = []

    def _add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved not in roots:
            roots.append(resolved)

    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if toplevel:
            _add(Path(toplevel))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # `--git-common-dir` returns the shared .git for both worktrees and the main
    # checkout; its parent is the main checkout root. For the main checkout this
    # collapses to the same path as --show-toplevel and gets deduped.
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if common:
            common_path = Path(common)
            if not common_path.is_absolute():
                common_path = Path.cwd() / common_path
            _add(common_path.parent)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    if not roots:
        _add(Path.cwd())

    return roots


def default_output_dir() -> Path:
    """`docs/conversations` under the current worktree (or cwd if not a repo)."""
    return project_roots()[0] / "docs/conversations"


# =============================================================================
# Cursor (SQLite database)
# =============================================================================

def cursor_get_db_connection():
    """Connect to the Cursor state database."""
    if not CURSOR_STATE_DB.exists():
        return None
    return sqlite3.connect(CURSOR_STATE_DB)


def cursor_get_bubble_content(conn, composer_id, bubble_id):
    """Get the content of a specific message bubble."""
    cursor = conn.cursor()
    key = f"bubbleId:{composer_id}:{bubble_id}"
    cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,))
    row = cursor.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def cursor_list_conversations(limit, search_text, full_text_search):
    """List recent Cursor conversations with their UUIDs and preview."""
    conn = cursor_get_db_connection()
    if not conn:
        return []

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT key, value FROM cursorDiskKV
        WHERE key LIKE 'composerData:%'
        AND key NOT LIKE 'composerData:task-%'
        ORDER BY length(value) DESC
        """
    )

    conversations = []
    for key, value in cursor.fetchall():
        if not value:
            continue
        uuid = key.replace("composerData:", "")
        try:
            data = json.loads(value)
            conversation = data.get("conversation", [])
            headers = data.get("fullConversationHeadersOnly", [])

            if headers and not conversation:
                if full_text_search and search_text:
                    for header in headers:
                        bubble_id = header.get("bubbleId")
                        if bubble_id:
                            bubble = cursor_get_bubble_content(conn, uuid, bubble_id)
                            if bubble:
                                conversation.append(bubble)
                else:
                    for header in headers:
                        if header.get("type") == 1:
                            bubble_id = header.get("bubbleId")
                            if bubble_id:
                                bubble = cursor_get_bubble_content(conn, uuid, bubble_id)
                                if bubble:
                                    conversation.append(bubble)
                                    break

            preview = ""
            all_text = ""
            for msg in conversation:
                msg_text = msg.get("text", "")
                all_text += " " + msg_text
                if not preview and msg.get("type") == 1:
                    preview = msg_text[:100].replace("\n", " ")

            if search_text:
                search_target = all_text.lower() if full_text_search else preview.lower()
                if search_text.lower() not in search_target:
                    continue

            msg_count = len(headers) if headers else len(conversation)
            if msg_count > 0:
                conversations.append(("cursor", uuid, msg_count, preview))
                if len(conversations) >= limit:
                    break
        except json.JSONDecodeError:
            continue

    conn.close()
    return conversations


def cursor_get_conversation(uuid):
    """Get a specific Cursor conversation by UUID."""
    conn = cursor_get_db_connection()
    if not conn:
        return None

    cursor = conn.cursor()
    key = f"composerData:{uuid}"
    cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    data = json.loads(row[0])
    headers = data.get("fullConversationHeadersOnly", [])
    if headers and not data.get("conversation"):
        messages = []
        for header in headers:
            bubble_id = header.get("bubbleId")
            if bubble_id:
                bubble = cursor_get_bubble_content(conn, uuid, bubble_id)
                if bubble:
                    messages.append(bubble)
        data["conversation"] = messages

    conn.close()
    return data


def cursor_format_message(msg):
    """Format a Cursor message to markdown."""
    msg_type = msg.get("type")
    text = msg.get("text", "").strip()

    if not text:
        return None

    if msg_type == 1:
        return f"## User\n\n{text}\n"
    elif msg_type == 2:
        return f"## Assistant\n\n{text}\n"
    else:
        return f"## Message (type {msg_type})\n\n{text}\n"


def cursor_to_markdown(uuid, data):
    """Convert Cursor conversation to markdown."""
    lines = [
        f"# Cursor Conversation",
        f"",
        f"**Conversation ID:** `{uuid}`",
        f"**Extracted:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"---",
        f"",
    ]

    conversation = data.get("conversation", [])
    for msg in conversation:
        formatted = cursor_format_message(msg)
        if formatted:
            lines.append(formatted)
            lines.append("---\n")

    return "\n".join(lines)


# =============================================================================
# Claude CLI (JSONL files)
# =============================================================================

def claude_cli_project_dirs() -> list[Path]:
    """Get Claude CLI project directories for the current worktree AND main checkout.

    Claude Code mangles cwd as `/Users/foo/bar` -> `-Users-foo-bar` to derive
    the per-project log dir. When a session was started from the main checkout
    and EnterWorktree later switched cwd, the log stays under the original
    mangling. Search both so lookups find it regardless.
    """
    dirs: list[Path] = []
    for root in project_roots():
        mangled = str(root).replace("/", "-")
        candidate = CLAUDE_CLI_PROJECTS_DIR / mangled
        if candidate.exists() and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def claude_cli_list_conversations(limit, search_text, full_text_search):
    """List recent Claude CLI conversations."""
    project_dirs = claude_cli_project_dirs()
    if not project_dirs:
        return []

    jsonl_files: list[Path] = []
    for project_dir in project_dirs:
        jsonl_files.extend(project_dir.glob("*.jsonl"))
    jsonl_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    conversations = []
    for jsonl_file in jsonl_files[:limit * 2]:  # Check more files than limit in case some are filtered
        uuid = jsonl_file.stem
        try:
            messages = []
            preview = ""
            all_text = ""

            with open(jsonl_file, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        if entry.get("type") not in ("user", "assistant"):
                            continue

                        msg_content = entry.get("message", {}).get("content", "")
                        if isinstance(msg_content, list):
                            text_parts = [p.get("text", "") for p in msg_content if p.get("type") == "text"]
                            msg_text = "\n".join(text_parts)
                        else:
                            msg_text = msg_content

                        if msg_text:
                            messages.append({
                                "role": entry.get("type"),
                                "text": msg_text
                            })
                            all_text += " " + msg_text
                            if not preview and entry.get("type") == "user":
                                preview = msg_text[:100].replace("\n", " ")
                    except json.JSONDecodeError:
                        continue

            if search_text:
                search_target = all_text.lower() if full_text_search else preview.lower()
                if search_text.lower() not in search_target:
                    continue

            if len(messages) > 0:
                conversations.append(("claude-cli", uuid, len(messages), preview))
                if len(conversations) >= limit:
                    break
        except Exception:
            continue

    return conversations


def claude_cli_get_conversation(uuid):
    """Get a specific Claude CLI conversation by UUID."""
    jsonl_file: Path | None = None
    for project_dir in claude_cli_project_dirs():
        candidate = project_dir / f"{uuid}.jsonl"
        if candidate.exists():
            jsonl_file = candidate
            break
    if jsonl_file is None:
        return None

    messages = []
    session_id = None

    with open(jsonl_file, "r") as f:
        for line in f:
            try:
                entry = json.loads(line)

                if not session_id:
                    session_id = entry.get("sessionId")

                if entry.get("type") not in ("user", "assistant"):
                    continue

                msg_content = entry.get("message", {}).get("content", "")
                if isinstance(msg_content, list):
                    text_parts = [p.get("text", "") for p in msg_content if p.get("type") == "text"]
                    msg_text = "\n".join(text_parts)
                else:
                    msg_text = msg_content

                if msg_text:
                    messages.append({
                        "role": entry.get("type"),
                        "text": msg_text,
                        "timestamp": entry.get("timestamp"),
                    })
            except json.JSONDecodeError:
                continue

    return {
        "sessionId": session_id,
        "messages": messages,
    }


def claude_cli_to_markdown(uuid, data):
    """Convert Claude CLI conversation to markdown."""
    lines = [
        f"# Claude CLI Conversation",
        f"",
        f"**Session ID:** `{uuid}`",
        f"**Extracted:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"---",
        f"",
    ]

    for msg in data.get("messages", []):
        role = msg.get("role", "unknown")
        text = msg.get("text", "").strip()

        if not text:
            continue

        if role == "user":
            lines.append(f"## User\n\n{text}\n")
        elif role == "assistant":
            lines.append(f"## Assistant\n\n{text}\n")
        else:
            lines.append(f"## {role}\n\n{text}\n")

        lines.append("---\n")

    return "\n".join(lines)


# =============================================================================
# Codex (JSONL session files)
# =============================================================================

def codex_iter_session_files() -> list[Path]:
    """List Codex session JSONL files newest first."""
    if not CODEX_SESSIONS_DIR.exists():
        return []

    return sorted(CODEX_SESSIONS_DIR.glob("**/*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)


def codex_load_thread_names() -> dict[str, str]:
    """Load Codex thread names from the session index."""
    if not CODEX_SESSION_INDEX.exists():
        return {}

    thread_names: dict[str, str] = {}
    with open(CODEX_SESSION_INDEX, mode="r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(entry, dict):
                continue

            session_id = entry.get("id")
            thread_name = entry.get("thread_name")
            if isinstance(session_id, str) and isinstance(thread_name, str):
                thread_names[session_id] = thread_name

    return thread_names


def codex_text_from_content(content: Any) -> str:
    """Extract readable text from Codex message content."""
    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return ""

    if not isinstance(content, list):
        return ""

    text_parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
            continue

        if not isinstance(part, dict):
            continue

        if part.get("type") not in ("input_text", "output_text", "text"):
            continue

        text = part.get("text")
        if isinstance(text, str):
            text_parts.append(text)

    return "\n".join(text_parts)


def codex_should_skip_message(role: str, text: str) -> bool:
    """Filter Codex messages that are metadata rather than conversation."""
    if not text.strip():
        return True

    return role == "user" and text.startswith("# AGENTS.md instructions for ")


def codex_matches_current_cwd(cwd: str | None) -> bool:
    """Check whether a Codex session belongs to the current project.

    Matches any of the project roots (current worktree + main checkout) so a
    Codex session started in one and continued in the other still resolves.
    """
    if not cwd:
        return True

    try:
        session_path = Path(cwd).resolve()
    except OSError:
        return cwd in {str(r) for r in project_roots()} or cwd == os.getcwd()

    return session_path in project_roots()


def codex_read_conversation_file(jsonl_file: Path, thread_names: dict[str, str]) -> dict[str, Any] | None:
    """Read one Codex JSONL file into a normalized conversation."""
    session_id: str | None = None
    cwd: str | None = None
    messages: list[dict[str, Any]] = []

    with open(jsonl_file, mode="r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(entry, dict):
                continue

            payload = entry.get("payload", {})
            if not isinstance(payload, dict):
                continue

            if entry.get("type") == "session_meta" and not session_id:
                meta_session_id = payload.get("id")
                if isinstance(meta_session_id, str):
                    session_id = meta_session_id

                meta_cwd = payload.get("cwd")
                if isinstance(meta_cwd, str):
                    cwd = meta_cwd
                continue

            if entry.get("type") != "response_item":
                continue

            if payload.get("type") != "message":
                continue

            role = payload.get("role")
            if not isinstance(role, str) or role not in ("user", "assistant"):
                continue

            text = codex_text_from_content(content=payload.get("content", "")).strip()
            if codex_should_skip_message(role=role, text=text):
                continue

            messages.append({
                "role": role,
                "text": text,
                "timestamp": entry.get("timestamp"),
                "phase": payload.get("phase"),
            })

    if not session_id:
        return None

    if not codex_matches_current_cwd(cwd=cwd):
        return None

    return {
        "sessionId": session_id,
        "threadName": thread_names.get(session_id),
        "cwd": cwd,
        "messages": messages,
        "path": str(jsonl_file),
    }


def codex_list_conversations(limit: int, search_text: str | None, full_text_search: bool) -> list[tuple[str, str, int, str]]:
    """List recent Codex conversations for the current project."""
    conversations: list[tuple[str, str, int, str]] = []
    thread_names = codex_load_thread_names()

    for jsonl_file in codex_iter_session_files():
        data = codex_read_conversation_file(jsonl_file=jsonl_file, thread_names=thread_names)
        if not data:
            continue

        messages = data.get("messages", [])
        preview = ""
        all_text = ""
        for msg in messages:
            msg_text = msg.get("text", "")
            all_text += " " + msg_text
            if not preview and msg.get("role") == "user":
                preview = msg_text[:100].replace("\n", " ")

        if search_text:
            search_target = all_text.lower() if full_text_search else preview.lower()
            if search_text.lower() not in search_target:
                continue

        if messages:
            conversations.append(("codex", data["sessionId"], len(messages), preview))
            if len(conversations) >= limit:
                break

    return conversations


def codex_get_conversation(uuid: str) -> dict[str, Any] | None:
    """Get a specific Codex conversation by session UUID."""
    thread_names = codex_load_thread_names()

    for jsonl_file in codex_iter_session_files():
        data = codex_read_conversation_file(jsonl_file=jsonl_file, thread_names=thread_names)
        if data and data.get("sessionId") == uuid:
            return data

    return None


def codex_to_markdown(uuid: str, data: dict[str, Any]) -> str:
    """Convert Codex conversation to markdown."""
    lines = [
        f"# Codex Conversation",
        f"",
        f"**Session ID:** `{uuid}`",
        f"**Extracted:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]

    thread_name = data.get("threadName")
    if thread_name:
        lines.append(f"**Thread:** {thread_name}")

    lines.extend([
        f"",
        f"---",
        f"",
    ])

    for msg in data.get("messages", []):
        role = msg.get("role", "unknown")
        text = msg.get("text", "").strip()

        if not text:
            continue

        if role == "user":
            lines.append(f"## User\n\n{text}\n")
        elif role == "assistant":
            lines.append(f"## Assistant\n\n{text}\n")
        else:
            lines.append(f"## {role}\n\n{text}\n")

        lines.append("---\n")

    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def list_conversations(limit: int, search_text: str | None, full_text_search: bool, source: str | None) -> list[tuple[str, str, int, str]]:
    """List conversations from all sources (or a specific source)."""
    conversations = []

    if source in (None, "cursor"):
        conversations.extend(cursor_list_conversations(limit=limit, search_text=search_text, full_text_search=full_text_search))

    if source in (None, "claude-cli"):
        conversations.extend(claude_cli_list_conversations(limit=limit, search_text=search_text, full_text_search=full_text_search))

    if source in (None, "codex"):
        conversations.extend(codex_list_conversations(limit=limit, search_text=search_text, full_text_search=full_text_search))

    # Sort by message count descending
    conversations.sort(key=lambda x: x[2], reverse=True)
    return conversations[:limit]


def get_conversation(uuid: str, source: str | None) -> tuple[str | None, Any | None]:
    """Get a conversation by UUID, trying all sources if source not specified."""
    if source in (None, "codex"):
        data = codex_get_conversation(uuid=uuid)
        if data:
            return "codex", data

    if source in (None, "claude-cli"):
        data = claude_cli_get_conversation(uuid=uuid)
        if data:
            return "claude-cli", data

    if source in (None, "cursor"):
        data = cursor_get_conversation(uuid=uuid)
        if data:
            return "cursor", data

    return None, None


def to_markdown(source: str, uuid: str, data: Any) -> str:
    """Convert conversation to markdown based on source."""
    if source == "cursor":
        return cursor_to_markdown(uuid=uuid, data=data)
    elif source == "claude-cli":
        return claude_cli_to_markdown(uuid=uuid, data=data)
    elif source == "codex":
        return codex_to_markdown(uuid=uuid, data=data)
    else:
        raise ValueError(f"Unknown source: {source}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract conversations to markdown")
    parser.add_argument("--uuid", help="Specific conversation UUID to extract")
    parser.add_argument("--list", action="store_true", help="List recent conversations")
    parser.add_argument("--search", "-s", help="Search for conversations containing text")
    parser.add_argument("--full", "-f", action="store_true", help="Full-text search (all messages)")
    parser.add_argument("--limit", type=int, default=10, help="Number of conversations to list")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--source", choices=["cursor", "claude-cli", "codex"], help="Source to use (default: all)")
    args = parser.parse_args()

    if args.list:
        conversations = list_conversations(
            limit=args.limit,
            search_text=args.search,
            full_text_search=args.full,
            source=args.source,
        )
        print(f"Recent conversations ({len(conversations)}):\n")
        for source, uuid, count, preview in conversations:
            print(f"  [{source}] {uuid}")
            print(f"    Messages: {count}")
            if preview:
                print(f"    Preview: {preview}...")
            print()
        return

    # Get the conversation
    if args.uuid:
        source, data = get_conversation(uuid=args.uuid, source=args.source)
        if not data:
            print(f"Error: Conversation {args.uuid} not found", file=sys.stderr)
            sys.exit(1)
        uuid = args.uuid
    else:
        # Get most recent by listing and picking first
        conversations = list_conversations(limit=1, search_text=None, full_text_search=False, source=args.source)
        if not conversations:
            print("Error: No conversations found", file=sys.stderr)
            sys.exit(1)
        source, uuid, _, _ = conversations[0]
        _, data = get_conversation(uuid=uuid, source=source)
        print(f"Using most recent conversation: [{source}] {uuid}")

    # Convert to markdown
    markdown = to_markdown(source=source, uuid=uuid, data=data)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = default_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        output_path = output_dir / f"{timestamp}-{uuid[:8]}.md"

    # Write output
    output_path.write_text(markdown)
    print(f"Conversation extracted to: {output_path}")


if __name__ == "__main__":
    main()
