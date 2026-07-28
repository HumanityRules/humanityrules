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

## Disjoint regions driven by one poll

When several regions of a page move together off the same resource, don't give each its own poll. Keep one poller and have its response carry the others as `hx-swap-oob="true"` fragments, so a page has a single live clock. The OOB include is gated on a flag (`with_oob` / `oob`) that only the poll response sets — on the initial page render the same partials render in place, without the OOB attribute.

Each OOB region still needs a wrapper that is always emitted, even when its content is empty, or the swap has nothing to target.

## Why This Is The Default Pattern

This is the simplest model to maintain:

- Each widget declares its own refresh behavior in its own template.
- Background jobs are handled by self-terminating polling instead of hidden global plumbing.
- There is no global event bus or persistent connection to reason about.

## Current Examples

- `humanityrules_app/templates/humanityrules_app/partials/_app_card_status.html`
- `humanityrules_app/templates/humanityrules_app/apps/_app_deployment_section.html` (the app page's single clock: its 10s poll OOB-swaps `_app_detail_actions.html` and `_app_deployment_history.html`; the Overview tab button refetches the same endpoint on click, which also restarts the 10s delay)
- `humanityrules_app/templates/humanityrules_app/environments/_environment_status.html`
- `humanityrules_app/templates/humanityrules_app/integrations/_aws_connect_poll.html` (connect modal polls until the account connects; a headless poller with no visible content, so it uses htmx's `every 30s` interval trigger and stops when the terminal response removes the element, rather than the self-replacing `load delay` fragment the visible widgets use)
- `humanityrules_app/templates/humanityrules_app/integrations/aws_accounts.html` (the AWS accounts list self-polls `every 30s` while any account is pending — the visible self-terminating pattern — so a row that was left pending after the modal was closed still updates to connected)
