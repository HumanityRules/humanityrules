# Slack Integration Design

How a Hermes agent connects to Slack. Two modes — **personal** (one user's DMs) and **company-wide** (a shared bot in channels) — each backed by its own dedicated Slack app.

## Mechanism: Socket Mode, one app per integration

The Hermes gateway uses Slack **Socket Mode**: it dials *out* to Slack over a WebSocket (`apps.connections.open` → `wss://`), receives events, runs the agent in-process, and posts replies via the Web API (`chat.postMessage`). It does **not** receive HTTP POSTs at a request URL.

Consequences:

- **Two tokens required**, both consumed by the gateway as env vars:
  - `SLACK_APP_TOKEN` (`xapp-`) — app-level, opens the Socket Mode WebSocket. One per app.
  - `SLACK_BOT_TOKEN` (`xoxb-`) — bot, used for `chat.postMessage` and Web API calls. One per workspace install.
- **One Slack app per integration**, created by the customer. The app-level token is one-per-app, so per-integration isolation (each gateway holds its own token pair, dials Slack independently) requires a dedicated app each.
- **Marketplace / "Add to Slack" is not available** — Slack bars Socket Mode apps from the public Marketplace. There is no published-DOH-app model.

## Why the customer creates the app (and DOH can't automate it)

App creation is deliberately walled off from the OAuth/bot-token system:

- The Manifest API (`apps.manifest.create`) needs an **app configuration token** (`xoxe-`), which is *unique to a user + workspace* and can only be minted **manually** on `api.slack.com/apps` (or rotated from an existing one). There is **no OAuth scope** that grants manifest access.
- So an installed Marketplace app **cannot create or configure other apps**. DOH cannot pre-create a config token for a customer's workspace, and cannot provision the app on their behalf.

This is by design (a supply-chain guardrail): spawning apps with arbitrary scopes is a deliberate human act in the dashboard.

## Onboarding flow: prefill-URL manifest

DOH's integrations panel renders a **manifest prefill URL**:

```
https://api.slack.com/apps?new_app=1&manifest_yaml=<URL-encoded manifest>
```

The operator's steps (~4 guided clicks, no manual config — the manifest carries all scopes/subscriptions):

1. Click the prefill link → Slack "Create New App" dialog opens **pre-populated** → pick workspace → **Create**.
2. **Generate** the app-level token (`xapp-`, needs `connections:write`) — one button on Basic Information. (Only appears because the manifest sets `socket_mode_enabled: true`.)
3. **Install to Workspace** → approve scopes → mints the bot token (`xoxb-`).
4. Paste both tokens into DOH.

The two tokens come from two different actions (generate vs. install), so the DOH UI asks for both explicitly and should **verify on paste**: `apps.connections.open` for `xapp-`, `auth.test` for `xoxb-`.

## Two modes, two manifests

The config dialog has a **Personal / Company-wide selector**. The selector drives **which manifest** the prefill URL carries — so the privacy model is enforced **structurally** (what Slack delivers), not just in gateway logic.

| | **Personal** | **Company-wide** |
|---|---|---|
| Subscribes to | `message.im` only | `app_mention` (+ channel history) |
| Can receive DMs | yes (owner's) | **no — not subscribed** |
| Can be @-mentioned in channels | no | yes |
| Audience | owner only | anyone in an invited channel |
| `SLACK_ALLOWED_USERS` | `<owner_id>` (single entry) | unset (allow all) |
| Memory | private to owner | **shared / global (intended)** |
| Unauthorized sender | silent-ignore | n/a |

Defense in depth — two independent layers:

1. **Subscription** (manifest): personal can't be @-mentioned in channels; company-wide can't be DMed. Structural.
2. **Sender allowlist** (`SLACK_ALLOWED_USERS`): personal additionally ignores anyone but the owner. Runtime.

If one layer is misconfigured, the other still holds.

### Personal mode = `SLACK_ALLOWED_USERS` with one entry

"Reply only to this user_id" is **not** a new gateway capability — it's the existing `SLACK_ALLOWED_USERS` allowlist set to exactly the owner's `user_id`. Company-wide leaves it unset (allow all). `SLACK_ALLOWED_USERS` is a general sender allowlist, not DM-specific.

### Identity capture (personal mode)

Collect the owner's **email** at config time (low friction) → resolve via `users.lookupByEmail` (needs `users:read.email` scope) → **store the resolved `user_id`**, never the email. Slack events identify senders by `user_id`; emails/display names change, IDs don't. Show the resolved name back for confirmation ("This agent will reply only to *Jane Doe*").

Note: `message.im` delivers DMs from *any* user, not just the owner. The `event.user == owner_id` allowlist check is therefore load-bearing; everyone else is silently ignored.

## Company-wide: shared state is intended

A company-wide agent is **one agent with one memory/session**, shared across all users. This is a feature, not a bug — org-wide accumulated knowledge. Suppressing DMs (by not subscribing to `message.im`) avoids the *illusion* of a private side-channel, keeping the shared-conversation privacy model honest. Anyone in an invited channel can drive the bot; no allowlist.

## Sandbox credential isolation

The gateway runs **inside the sandbox** (Telegram-style — not a WhatsApp-style external bridge), with `HTTPS_PROXY` pointed at the integrations broker. Both long-lived tokens stay **outside** the sandbox; the broker phantom-swaps the placeholder Bearer for the real token in flight, exactly as in [integrations_broker_design.md](integrations_broker_design.md).

This works because every long-lived token appears only in REST `Authorization: Bearer` headers (which the broker can intercept and swap), never on the WebSocket:

| Call | Token | Where it travels | Broker swap |
|---|---|---|---|
| `apps.connections.open` (REST) | `xapp-` | `Authorization: Bearer` header | ✅ swapped |
| `wss://…` connect (WebSocket) | short-lived ticket | embedded in the returned URL | n/a — disposable, plain `CONNECT` tunnel |
| `chat.postMessage` (REST) | `xoxb-` | `Authorization: Bearer` header | ✅ swapped |

Key traced facts (slack_sdk):

- `apps.connections.open` is the **only** call that carries `xapp-`. It returns a `wss://` URL embedding a short-lived ticket; the WebSocket connects with that ticket and **never re-sends `xapp-`**. The ticket auto-expires when the socket closes (low blast radius), so the un-swappable `CONNECT` tunnel is harmless.
- Although `apps_connections_open` *passes* `xapp-` as a `token` kwarg, `base_client._build_urllib_request_headers` promotes it to an `Authorization: Bearer` header (`headers.update({"Authorization": "Bearer {}".format(token)})`) — same path as `xoxb-`. So the **existing header-based phantom-swap handles both tokens with no special-casing**.
- The Socket Mode WebSocket clients (built-in and aiohttp) honor `proxy=`/`HTTPS_PROXY` and tunnel the WebSocket via HTTP `CONNECT`, so the in-sandbox gateway reaches Slack through the broker.

Slack token presence activating the gateway binding mirrors the Telegram pattern in [gateway_env_and_restart_design.md](gateway_env_and_restart_design.md).
