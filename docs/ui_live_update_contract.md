# UI Live Update Contract

This document defines the current live-update pattern in DevOps Hero. The goal is simplicity: live updates should stay explicit, local, and easy to reason about.

## Core Rules

- Server-rendered HTML fragments are the source of truth.
- Each live widget owns one fragment endpoint and one fragment template.
- The same fragment should be used for initial render and refresh responses whenever practical.
- Use the narrowest HTMX swap target that keeps the UI correct.

## Two Refresh Mechanisms

We use two different mechanisms today, and each has a clear job.

### 1. Chat-scoped SSE invalidation

Use chat SSE only on pages that already render the chat panel.

- The browser opens an SSE connection through `chat_stream`.
- `chat_stream` can emit `sse-notify` messages.
- The chat panel turns those into `doh:*` document events.
- Other widgets on the same page can listen for those events and refetch their own fragment endpoints.

This is an invalidation mechanism, not a general event bus.

### 2. Self-terminating HTMX polling

Use polling for state changes that can happen outside the current page interaction, especially background job updates.

- The widget polls only while the underlying resource is in a transient state.
- The server re-renders the fragment.
- Once the resource reaches a terminal state, the fragment is rendered without the polling attributes, so polling stops automatically.

This keeps the behavior local to the widget and easy to understand from the template alone.

## Widget Contract

Every live widget should follow this shape:

- It has a stable wrapper element.
- It has one fragment endpoint that returns the widget fragment.
- It may listen for zero or more `doh:*` invalidation events.
- It may self-poll only while the resource is transient.
- The server decides whether polling continues by including or omitting the polling attributes in the returned fragment.

## Current Architectural Boundary

The only SSE channel in the app today is the chat stream, and it is scoped to pages that include the chat panel.

That means:

- SSE-driven invalidation is available only when the chat panel is present.
- Pages without the chat panel must rely on direct HTMX interactions or polling.
- Background-worker state changes should not depend on chat SSE.

## Why This Is The Default Pattern

This split is the simplest model to maintain:

- Chat pages can refresh nearby widgets with lightweight invalidation events.
- Non-chat pages remain straightforward because each widget declares its own refresh behavior.
- Background jobs are handled by self-terminating polling instead of hidden global plumbing.

## Current Examples

- `devopshero_app/templates/devopshero_app/partials/_app_card_status.html`
- `devopshero_app/templates/devopshero_app/apps/app_detail.html`
- `devopshero_app/templates/devopshero_app/deploy/_blueprint_section.html`
- `devopshero_app/templates/devopshero_app/environments/_environment_editor_setup_section.html`
- `devopshero_app/templates/devopshero_app/chat/_chat_panel.html`
- `devopshero_app/views/chat.py`
