#!/usr/bin/env python3
"""Extract Cursor conversations from the state database to markdown files.

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

Note: The current in-progress conversation may not appear until it's saved.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


CURSOR_STATE_DB = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "docs/conversations"


def get_db_connection():
    """Connect to the Cursor state database."""
    if not CURSOR_STATE_DB.exists():
        print(f"Error: Cursor database not found at {CURSOR_STATE_DB}", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(CURSOR_STATE_DB)


def list_conversations(limit, search_text, full_text_search):
    """List recent conversations with their UUIDs and preview."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Sort by content size to get conversations with actual content
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

            # Handle both old format (inline conversation) and new format (headers only)
            conversation = data.get("conversation", [])
            headers = data.get("fullConversationHeadersOnly", [])

            # For new format, resolve bubbles if needed for search
            if headers and not conversation:
                if full_text_search and search_text:
                    # Need to load all messages for full-text search
                    for header in headers:
                        bubble_id = header.get("bubbleId")
                        if bubble_id:
                            bubble = get_bubble_content(conn, uuid, bubble_id)
                            if bubble:
                                conversation.append(bubble)
                else:
                    # Just get first user message for preview
                    for header in headers:
                        if header.get("type") == 1:  # User message
                            bubble_id = header.get("bubbleId")
                            if bubble_id:
                                bubble = get_bubble_content(conn, uuid, bubble_id)
                                if bubble:
                                    conversation.append(bubble)
                                    break

            # Find first user message for preview
            preview = ""
            all_text = ""
            for msg in conversation:
                msg_text = msg.get("text", "")
                all_text += " " + msg_text
                if not preview and msg.get("type") == 1:  # User message
                    preview = msg_text[:100].replace("\n", " ")

            # Filter by search text if provided
            if search_text:
                search_target = all_text.lower() if full_text_search else preview.lower()
                if search_text.lower() not in search_target:
                    continue

            # Count messages
            msg_count = len(headers) if headers else len(conversation)
            if msg_count > 0:
                conversations.append((uuid, msg_count, preview))
                if len(conversations) >= limit:
                    break
        except json.JSONDecodeError:
            continue

    conn.close()
    return conversations


def get_bubble_content(conn, composer_id, bubble_id):
    """Get the content of a specific message bubble."""
    cursor = conn.cursor()
    key = f"bubbleId:{composer_id}:{bubble_id}"
    cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,))
    row = cursor.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def get_conversation(uuid):
    """Get a specific conversation by UUID, resolving bubble references."""
    conn = get_db_connection()
    cursor = conn.cursor()

    key = f"composerData:{uuid}"
    cursor.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (key,))
    row = cursor.fetchone()

    if not row:
        conn.close()
        return None

    data = json.loads(row[0])

    # Check if this is the new format with headers-only
    headers = data.get("fullConversationHeadersOnly", [])
    if headers and not data.get("conversation"):
        # New format: resolve each bubble reference
        messages = []
        for header in headers:
            bubble_id = header.get("bubbleId")
            if bubble_id:
                bubble = get_bubble_content(conn, uuid, bubble_id)
                if bubble:
                    messages.append(bubble)
        data["conversation"] = messages

    conn.close()
    return data


def get_most_recent_conversation():
    """Get the most recent conversation with actual messages."""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT key, value FROM cursorDiskKV 
        WHERE key LIKE 'composerData:%' 
        AND key NOT LIKE 'composerData:task-%'
        """
    )

    best_uuid = None
    max_messages = 0

    for key, value in cursor.fetchall():
        try:
            data = json.loads(value)
            # Count from either format
            conversation = data.get("conversation", [])
            headers = data.get("fullConversationHeadersOnly", [])
            msg_count = len(headers) if headers else len(conversation)

            if msg_count > max_messages:
                max_messages = msg_count
                best_uuid = key.replace("composerData:", "")
        except json.JSONDecodeError:
            continue

    conn.close()

    if not best_uuid:
        return None, None

    # Use get_conversation to resolve bubbles
    return best_uuid, get_conversation(best_uuid)


def format_message(msg):
    """Format a single message to markdown. Returns None for empty messages."""
    msg_type = msg.get("type")
    text = msg.get("text", "").strip()

    # Skip empty messages (tool calls, internal operations)
    if not text:
        return None

    if msg_type == 1:  # User message
        return f"## User\n\n{text}\n"
    elif msg_type == 2:  # Assistant message
        return f"## Assistant\n\n{text}\n"
    else:
        return f"## Message (type {msg_type})\n\n{text}\n"


def conversation_to_markdown(uuid, data):
    """Convert conversation data to markdown format."""
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
        formatted = format_message(msg)
        if formatted:  # Skip empty messages
            lines.append(formatted)
            lines.append("---\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract Cursor conversations to markdown")
    parser.add_argument("--uuid", help="Specific conversation UUID to extract")
    parser.add_argument("--list", action="store_true", help="List recent conversations")
    parser.add_argument("--search", "-s", help="Search for conversations containing text")
    parser.add_argument("--full", "-f", action="store_true", help="Full-text search (all messages, not just first)")
    parser.add_argument("--limit", type=int, default=10, help="Number of conversations to list")
    parser.add_argument("--output", "-o", help="Output file path")
    args = parser.parse_args()

    if args.list:
        conversations = list_conversations(args.limit, args.search, args.full)
        print(f"Recent conversations ({len(conversations)}):\n")
        for uuid, count, preview in conversations:
            print(f"  {uuid}")
            print(f"    Messages: {count}")
            if preview:
                print(f"    Preview: {preview}...")
            print()
        return

    # Get the conversation
    if args.uuid:
        data = get_conversation(args.uuid)
        if not data:
            print(f"Error: Conversation {args.uuid} not found", file=sys.stderr)
            sys.exit(1)
        uuid = args.uuid
    else:
        uuid, data = get_most_recent_conversation()
        if not data:
            print("Error: No conversations found", file=sys.stderr)
            sys.exit(1)
        print(f"Using most recent conversation: {uuid}")

    # Convert to markdown
    markdown = conversation_to_markdown(uuid, data)

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
        output_path = DEFAULT_OUTPUT_DIR / f"{timestamp}-{uuid[:8]}.md"

    # Write output
    output_path.write_text(markdown)
    print(f"Conversation extracted to: {output_path}")


if __name__ == "__main__":
    main()
