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
https://api.slack.com/apps?new_app=1&manifest_json=<URL-encoded manifest>
```

The operator's steps (~4 guided clicks, no manual config — the manifest carries all scopes/subscriptions):

1. Click the prefill link → Slack "Create New App" dialog opens **pre-populated** → pick workspace → **Create**.
2. **Generate** the app-level token (`xapp-`, needs `connections:write`) — one button on Basic Information. (Only appears because the manifest sets `socket_mode_enabled: true`.)
3. **Install to Workspace** → approve scopes → mints the bot token (`xoxb-`).
4. Paste both tokens into DOH.

The two tokens come from two different actions (generate vs. install), so the DOH UI asks for both explicitly and should **verify on paste**: `apps.connections.open` for `xapp-`, `auth.test` for `xoxb-`.

## App name (operator-editable, defaults to the template name)

The config dialog has an editable **App name** field. It feeds **both** Slack
manifest name fields — there is no single "bot name vs app name" toggle; one
entry drives both, sanitized per Slack's differing field rules:

- `display_information.name` (the app's name on its page / install dialog) — **≤35 chars**, any characters, used verbatim.
- `features.bot_user.display_name` (the bot as it posts in channels/DMs) — **≤80 chars**, restricted to `[a-z0-9._-]`; the name is lowercased and non-matching runs collapse to `-`.

The **default** is the deploying app's template name — `App.source_template.name`
(the `AppTemplate.name` chosen in the DOH control plane at deploy time, e.g.
"Hermes Agent"). If there is no template (the `SET_NULL` edge case) or the
operator clears the field, it falls back to `"Slackbot"` / `"slackbot"`.

The chosen name is persisted in the Slack `config["app_name"]` so it
repopulates on reopen (the template default only seeds the first connect). The
name lives **inside the manifest JSON** the operator clicks, so the WebUI
re-encodes the prefill URL on every keystroke. Sanitization is mirrored in
Python (`provider_slack.py`) and JS (`doh-integrations.js`) so the live link
matches what the server bakes and stores.

**The name only affects app *creation*.** Like any manifest change, renaming in
DOH does not touch an already-created Slack app (see "Changing the manifest…"
below); an existing install must re-apply the manifest and reinstall to pick up
a new name.

## Two modes, two manifests

The config dialog has a **Personal / Company-wide selector**. The selector drives **which manifest** the prefill URL carries — so the privacy model is enforced **structurally** (what Slack delivers), not just in gateway logic.

| | **Personal** | **Company-wide** |
|---|---|---|
| Subscribes to | `message.im` only | `app_mention` + `message.channels` + `message.groups` |
| Can receive DMs | yes (owner's) | **no — not subscribed** |
| Can be @-mentioned in channels | no | yes |
| Audience | owner only | anyone in an invited channel |
| `SLACK_ALLOW_ALL_USERS` | unset | `true` |
| `SLACK_ALLOWED_USERS` | `<owner_id>` (single entry) | unset |
| Memory | private to owner | **shared / global (intended)** |
| Unauthorized sender | silent-ignore | n/a |

Defense in depth — two independent layers:

1. **Subscription** (manifest): personal can't be @-mentioned in channels; company-wide can't be DMed. Structural.
2. **Runtime auth** (gateway env): personal uses `SLACK_ALLOWED_USERS` (owner only); company-wide sets `SLACK_ALLOW_ALL_USERS=true` (the gateway denies by default — an unset allowlist is *not* allow-all).

If one layer is misconfigured, the other still holds.

### The gateway denies by default — company-wide must opt in

**Important:** the upstream Hermes gateway's `_is_user_authorized` defaults to **deny**. Its precedence is: per-platform allow-all flag → env allowlist → DM pairing → global allow-all → *default deny*. An unset `SLACK_ALLOWED_USERS` is **not** "allow all" — it's "deny all". So company-wide mode must explicitly set **`SLACK_ALLOW_ALL_USERS=true`**, or every user is rejected (`Unauthorized user: …`).

How each mode opens access (rendered into `/workspace/.hermes/.env` from the Slack `config`):

- **Company-wide** → `config["allow_all_users"] = "true"` → `SLACK_ALLOW_ALL_USERS=true`. No allowlist.
- **Personal** (not yet enabled) → `config["allowed_users"] = [owner_id]` → `SLACK_ALLOWED_USERS=<owner_id>`. No allow-all flag.

`SLACK_ALLOWED_USERS` is a general sender allowlist, not DM-specific; "reply only to this user_id" is just that allowlist with one entry.

### Identity capture (personal mode)

Collect the owner's **email** at config time (low friction) → resolve via `users.lookupByEmail` (needs `users:read.email` scope) → **store the resolved `user_id`**, never the email. Slack events identify senders by `user_id`; emails/display names change, IDs don't. Show the resolved name back for confirmation ("This agent will reply only to *Jane Doe*").

Note: `message.im` delivers DMs from *any* user, not just the owner. The `event.user == owner_id` allowlist check is therefore load-bearing; everyone else is silently ignored.

## Company-wide: shared state is intended

A company-wide agent is **one agent with one memory/session**, shared across all users. This is a feature, not a bug — org-wide accumulated knowledge. Suppressing DMs (by not subscribing to `message.im`) avoids the *illusion* of a private side-channel, keeping the shared-conversation privacy model honest. Anyone in an invited channel can drive the bot (`SLACK_ALLOW_ALL_USERS=true`; no per-user allowlist).

### Why company-wide subscribes to `message.channels`, not just `app_mention`

`app_mention` fires **only** on messages that explicitly `@`-mention the bot ([docs](https://docs.slack.dev/reference/events/app_mention)) — *"you'll receive only the messages pertinent to your app."* It does **not** fire for ordinary replies in a thread, even one the bot was just mentioned in and is actively answering. The upstream gateway is built to follow a thread once pulled in (it records the thread in `_mentioned_threads` on the first mention, then auto-responds to subsequent non-mention replies via the `in_mentioned_thread` / `reply_to_bot_thread` / active-session branches in `_handle_slack_message`), but that logic only runs on events Slack delivers. Under an `app_mention`-only subscription Slack delivers nothing for the follow-ups, so the bot goes silent after the first turn.

`message.channels` (backed by `channels:history`) and `message.groups` (backed by `groups:history`) deliver every public- and private-channel message into the generic `message` handler; the gateway's gating then decides what to answer. Private-channel events arrive with `channel_type == "group"`, which the gateway treats as a channel (not a DM — `is_dm` is only `im`/`mpim`), so they flow through the same mention/thread gating. This does **not** weaken the privacy model: there is still no `message.im` subscription, so company-wide DMs remain structurally impossible.

The manifest also carries `channels:read`, `groups:read`, `im:read`, `mpim:read`. These are unrelated to message routing — the gateway's `channel_directory._build_slack` calls `users.conversations` to resolve channels by name, and Slack requires all four `*:read` scopes for that method regardless of the `types` filter. Without them the directory build logs a `missing_scope` error (harmless to routing, but noisy and it breaks name lookup).

**Changing the manifest does not touch an already-created Slack app.** Existing company-wide installs must re-apply the manifest (or add the `message.channels` / `message.groups` subscriptions and the new scopes by hand) and **reinstall to the workspace**. New installs via the prefill URL pick it up automatically. The bot must also be **invited to each private channel** it should follow — `groups:history` grants access only to private channels the bot is a member of.

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

## Implementation

Slack reuses the generic integration credential plumbing; the provider-specific
parts fork in five places, everything else is shared:

- **Credential method `VaultHeaderInject`** (`tls_intercept.py`) — neither `OAuthHeader`
  nor `VaultUrlRewrite` fit: Slack is *vault-pasted* (connect_mode `vault`, restart-
  required, has `gateway_env` bindings) **and** *header-injected* (`Authorization: Bearer`,
  not URL rewrite) **and** *multi-secret*.
- **Secret selection by placeholder reverse-map, not request path.** The gateway env
  hands the sandbox two distinct placeholder bearers (`xapp-…PLACEHOLDER`,
  `xoxb-…PLACEHOLDER`); the sandbox already sends the correct token per call (app token
  opens the socket, bot token posts), so the proxy maps the incoming placeholder bearer →
  real secret name. No per-request path logic in the hot path. `placeholders` is a
  secret_name → placeholder map; the hot path fails closed on an unknown bearer.
- **`TlsProviderSpec` hosts** `slack.com`, `www.slack.com` (REST only). The `wss://`
  Socket Mode host is not intercepted — it passes through as a plain CONNECT tunnel
  (carries only the disposable ticket).
- **Validation** (`slack_vault.py`) — `xoxb-` via `auth.test`, `xapp-` via
  `apps.connections.open`. `refresh_slack_outcome` returns both secrets.
- **UI** — a custom `_VAULT_RENDERERS["slack"]` modal: editable app-name field
  (defaults to the template name, re-bakes both manifest names on edit), mode
  selector, manifest prefill link, two token fields.

The shared pieces carry both secrets via the `secrets: dict[str,str]` map on
`RefreshResult`/`_TokenCacheEntry`, and Slack registers into the per-provider dispatch
tables (`_SCHEMA_BUILDERS`/`_CREDENTIAL_SAVERS`, `_OUTCOME_HANDLERS`, `_VAULT_RENDERERS`).
The Merge `slack` connector is excluded so it never appears beside the native one.

Personal mode's manifest is defined but the mode is disabled: it needs an owner-only
allowlist plus email→user_id resolution that isn't implemented.
