# UI Live Update Contract

This document defines the current live-update pattern in Humanity Rules. The goal is simplicity: live updates should stay explicit, local, and easy to reason about.

## Core Rules

- Server-rendered HTML fragments are the source of truth.
- Each live widget owns one fragment endpoint and one fragment template.
- The same fragment should be used for initial render and refresh responses whenever practical.
- Use the narrowest HTMX swap target that keeps the UI correct.

## Refresh Mechanism: self-terminating HTMX polling

Live updates are driven entirely by HTMX — direct user interactions plus self-terminating polling. There is no SSE or WebSocket channel in the app; every refresh is a plain fragment fetch.

Polling handles state changes that happen outside the current page interaction, especially background job updates (app deployment, environment provisioning, AWS account connection).

- The widget polls only while the underlying resource is in a transient state.
- The server re-renders the fragment.
- Once the resource reaches a terminal state, the fragment is rendered without the polling attributes, so polling stops automatically.

This keeps the behavior local to the widget and easy to understand from the template alone.

## Widget Contract

Every live widget should follow this shape:

- It has a stable wrapper element.
- It has one fragment endpoint that returns the widget fragment.
- It may self-poll only while the resource is transient.
- The server decides whether polling continues by including or omitting the polling attributes in the returned fragment.

## Why This Is The Default Pattern

This is the simplest model to maintain:

- Each widget declares its own refresh behavior in its own template.
- Background jobs are handled by self-terminating polling instead of hidden global plumbing.
- There is no global event bus or persistent connection to reason about.

## Current Examples

- `humanityrules_app/templates/humanityrules_app/partials/_app_card_status.html`
- `humanityrules_app/templates/humanityrules_app/apps/app_detail.html`
- `humanityrules_app/templates/humanityrules_app/environments/_environment_status.html`
- `humanityrules_app/templates/humanityrules_app/integrations/_aws_connect_poll.html` (connect modal polls until the account connects; a headless poller with no visible content, so it uses htmx's `every 30s` interval trigger and stops when the terminal response removes the element, rather than the self-replacing `load delay` fragment the visible widgets use)
- `humanityrules_app/templates/humanityrules_app/integrations/aws_accounts.html` (the AWS accounts list self-polls `every 30s` while any account is pending — the visible self-terminating pattern — so a row that was left pending after the modal was closed still updates to connected)
