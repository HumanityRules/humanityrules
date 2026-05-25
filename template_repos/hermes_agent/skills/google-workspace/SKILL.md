---
name: google-workspace
description: Read-only Gmail and Google Workspace access — Calendar, Drive, Contacts, Sheets, and Docs.

version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [Google, Gmail, Calendar, Drive, Sheets, Docs, Contacts, Email]
---

# Google Workspace

Gmail, Calendar, Drive, Contacts, Sheets, and Docs — through the `gws` CLI, using a platform-managed read-only access token.

## How auth works

Auth is injected by the platform's integrations broker — you don't handle
tokens. Every Google API call goes through an HTTPS proxy that swaps in
the current short-lived access token before forwarding to Google. You
pass a placeholder; the broker replaces it.

If Google isn't connected, the broker returns a 503 with a clear message:
"google integration not connected in DOH — connect it from the Integrations
pane." Surface that to the user verbatim.

## References

- `references/gmail-search-syntax.md` — Gmail search operators.

## Usage

All commands go through the API script. Set `GAPI` as a shorthand:

```bash
GAPI="python ${HERMES_WEBUI_AGENT_DIR:-/opt/hermes/agent}/skills/productivity/google-workspace/scripts/google_api.py"
```

### Gmail

```bash
$GAPI gmail search "is:unread" --max 10
$GAPI gmail search "from:boss@company.com newer_than:1d"
$GAPI gmail search "has:attachment filename:pdf newer_than:7d"

$GAPI gmail get MESSAGE_ID

$GAPI gmail labels
```

### Calendar

```bash
$GAPI calendar list
$GAPI calendar list --start 2026-03-01T00:00:00Z --end 2026-03-07T23:59:59Z
```

### Drive

```bash
$GAPI drive search "quarterly report" --max 10
$GAPI drive search "mimeType='application/pdf'" --raw-query --max 5
```

### Contacts

```bash
$GAPI contacts list --max 20
```

### Sheets

```bash
$GAPI sheets get SHEET_ID "Sheet1!A1:D10"
```

### Docs

```bash
$GAPI docs get DOC_ID
```

## Output Format

All commands return JSON. Parse with `jq` or read directly. Key fields:

- **Gmail search**: `[{id, threadId, from, to, subject, date, snippet, labels}]`
- **Gmail get**: `{id, threadId, from, to, subject, date, labels, body}`
- **Calendar list**: `[{id, summary, start, end, location, description, htmlLink}]`
- **Drive search**: `[{id, name, mimeType, modifiedTime, webViewLink}]`
- **Contacts list**: `[{name, emails: [...], phones: [...]}]`
- **Sheets get**: `[[cell, cell, ...], ...]`

## Rules

1. **Read-only access only.** Do not send, modify, create, delete, or update Google Workspace data from this skill.
2. **Use the Gmail search syntax reference** for complex queries — load it with `skill_view("google-workspace", file_path="references/gmail-search-syntax.md")`.
3. **Calendar times must include timezone** — use ISO 8601 with offset (e.g., `2026-03-01T10:00:00-06:00`) or UTC (`Z`).
4. **Respect rate limits** — batch reads when possible; avoid rapid-fire sequential API calls.

## Troubleshooting

- **503 "google integration not connected in DOH"** — the user hasn't connected Google yet (or disconnected it). Ask them to connect from the Integrations pane.
- **`HttpError 403: Insufficient Permission`** — missing API scope. User needs to reconnect with the needed scopes.
- **`HttpError 403: Access Not Configured`** — Google API not enabled. Surface to the user; they need to get it enabled.
- **`invalid_grant`** — refresh token revoked at Google's end. The Integrations pane will show Google as not connected; user needs to reconnect.
