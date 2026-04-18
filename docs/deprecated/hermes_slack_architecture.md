# Hermes Slack Architecture: Multi-User Design

## Current State (2026-04-15)

We have two AppTemplate rows for Hermes, both built from the same Docker image (`template_repos/hermes_agent/`):

| Template | Slug | Purpose |
|---|---|---|
| Hermes (Personal) | `hermes-personal` | One per user. Web UI only. No Slack tokens. |
| Hermes (Slack) | `hermes-slack` | One per org. Slack + web UI. Shared by all workspace users. |

### How the Slack gateway works

The Hermes project has two processes:

- **WebUI** (`hermeswebui_init.bash` -> `server.py`) — serves the web chat interface on port 8787.
- **Gateway** (`python -m gateway.run`) — connects to Slack via Socket Mode, receives events, runs the agent in-process, posts responses back.

Our `start_with_gateway.sh` orchestrates both. When no Slack tokens are set, it falls through to WebUI-only mode.

### How multi-user works in the shared Slack instance

The gateway isolates users through **session keys**. Each Slack DM gets a unique session key based on the DM channel ID. Each channel @mention gets a session key that includes the Slack user ID (when `group_sessions_per_user: true`, which is our default). Sessions, conversation history, and token tracking are all per-key.

Access control is via `SLACK_ALLOW_ALL_USERS=true` (our default) or an explicit allowlist of Slack Member IDs via `SLACK_ALLOWED_USERS`.

### What's shared vs isolated in the shared instance

| Resource | Isolated per user? | Notes |
|---|---|---|
| Conversations / sessions | Yes | Session keys include user or DM channel ID |
| Conversation history | Yes | Stored per session ID |
| Agent memory (MEMORY.md) | **No** | Single file under `~/.hermes/memories/` |
| User profile (USER.md) | **No** | Single file — if Alice says "I prefer brief answers", Bob gets that too |
| Skills | **No** | Shared `~/.hermes/skills/` directory |
| Config / model | **No** | Single `config.yaml` and `.env` |
| API keys / billing | **No** | One set of provider keys for all users |

The builtin memory provider has no concept of user_id. The `user_id` threading that exists in the codebase (visible in `tests/agent/test_memory_user_id.py`) only flows to **external plugin** memory providers (Mem0, Honcho, etc.), not to the builtin MEMORY.md/USER.md files.

**Unverified**: We haven't confirmed this on a live multi-user instance. The code analysis is clear but it's worth testing with two Slack users on the same instance.


## Why a single shared instance isn't ideal

The shared Slack instance gives every user the same model, the same API keys, the same agent personality, and the same memory. That's fine for an org-wide bot, but doesn't match the "personal assistant" model where each user has their own Hermes with their own configuration and accumulated knowledge.

Meanwhile, the per-user web deployments (`hermes-personal`) give exactly that — each user has their own URL, own EFS volume, own memory, own config. But they can't do Slack because of how Slack distributes events.


## The Slack routing problem

When multiple Socket Mode connections use the same app token, Slack load-balances events across them (not broadcast). If Alice's message lands on Bob's gateway, Bob's gateway either ignores it (nobody responds) or processes it against Bob's memory/config (wrong context).

`SLACK_ALLOWED_USERS` can't fix this because the message is **delivered to only one connection**. If Alice's gateway never receives the event, filtering is irrelevant.

Creating separate Slack apps per user would solve routing, but that's impractical — Slack app installation is a workspace-admin operation, not a per-user self-service flow.


## Alternative Design: Slack Proxy Router

A lightweight proxy service that owns the Slack connection and forwards messages to per-user Hermes instances via HTTP.

### Key discovery: Hermes has a built-in API server

`gateway/platforms/api_server.py` implements an OpenAI-compatible HTTP API on port 8642:

```
POST /v1/chat/completions     — stateless or session-sticky via X-Hermes-Session-Id header
POST /v1/responses             — stateful via previous_response_id
POST /v1/runs                  — async runs with SSE event streams
GET  /v1/models                — available models
GET  /health                   — health check
GET  /health/detailed          — rich status
```

This means each personal Hermes instance can receive messages over HTTP without needing its own Slack connection.

### Proposed architecture

```
Slack workspace
    |
    | Socket Mode (single connection)
    v
DOH Slack Proxy (one per org)
    |
    | Looks up: Slack user ID -> Hermes instance URL
    | POST /v1/chat/completions with X-Hermes-Session-Id: slack:{channel_id}
    |
    +---> hermes-alice.internal:8642 (Alice's personal instance, Alice's EFS)
    +---> hermes-bob.internal:8642   (Bob's personal instance, Bob's EFS)
    +---> hermes-carol.internal:8642 (Carol's personal instance, Carol's EFS)
```

### What the proxy does

1. Connects to Slack via Socket Mode (holds the app/bot tokens).
2. Receives a message event with a Slack user ID.
3. Looks up which Hermes instance belongs to that user (from DOH database or config).
4. Forwards the message to that instance's API server.
5. Receives the response.
6. Posts it back to Slack.

### What the proxy does NOT do

- Run an agent loop or make LLM calls.
- Store state, sessions, or memory.
- Know anything about Hermes internals beyond the HTTP API.

### User-to-instance mapping

The mapping could be:
- **From DOH's database**: query which `hermes-personal` app belongs to which user. Requires a mechanism to associate a Slack user ID with a DOH user/app.
- **From a config file or environment variable**: simple `{slack_user_id: hermes_url}` mapping, managed manually or by a DOH management command.
- **From ECS service discovery**: if personal instances register in Cloud Map, the proxy could look them up by app name.

### What each personal instance needs

- The API server must be **enabled and accessible** on port 8642. Currently `api_server.py` defaults to `127.0.0.1:8642` — this would need to bind to `0.0.0.0` or the task's private IP, and the ECS security group would need to allow traffic from the proxy.
- The API server starts automatically when the gateway runs. For web-only personal instances (no gateway), we'd need to start it separately, or always start the gateway even without Slack tokens. This needs investigation — the API server may be a gateway platform adapter that requires the full gateway lifecycle.

### Open questions

1. **Does the API server start independently of the gateway?** Or does it require the full gateway bootstrap? If the latter, personal instances would need to run the gateway even without Slack tokens, purely to expose the API.

2. **Authentication on the API server.** The proxy would be making unauthenticated HTTP calls to internal Hermes instances. The API server may have auth (API key, gateway token). This needs checking.

3. **Streaming and long responses.** Hermes agent calls can take tens of seconds (tool execution, multi-turn reasoning). Does the proxy need to handle streaming, or can it wait for a complete response? The API server supports SSE streaming via `/v1/runs/{run_id}/events`.

4. **Slack threading.** The gateway currently manages thread tracking (which threads to auto-respond in, thread-to-session mapping). The proxy would need to replicate this or delegate it.

5. **Slack reactions and typing indicators.** The gateway posts typing indicators and emoji reactions during processing. The proxy would need to handle these, or the response would feel less interactive.

6. **Error handling and fallback.** What happens when a user's personal instance is down or hasn't been deployed? The proxy needs a graceful fallback (error message to user, not silent failure).

7. **Shared memory concern.** Even with the proxy, the builtin memory (MEMORY.md/USER.md) would now be per-user (each instance has its own EFS). This is actually an improvement over the shared instance model.

### Effort estimate considerations

The proxy itself is small — a Socket Mode listener, an HTTP client, and a routing table. The complexity is in:
- Replicating the Slack UX polish (reactions, threading, typing indicators).
- Ensuring the API server works for personal instances that don't run the full gateway.
- Building and maintaining the user-to-instance mapping.
- A new AppTemplate or configuration for the proxy service itself.

### When to build this

The two-template approach (personal web + shared Slack) works today with zero custom code. Build the proxy when:
- Users need their Slack conversations to use their personal memory/config.
- The shared memory limitation becomes a real problem (not just theoretical).
- There's demand for unified web+Slack experience on the same instance.

Until then, the shared Slack instance with per-user session isolation is good enough.
