# Telegram Managed-Bot Integration

How a Hermes agent connects to Telegram without the user ever touching a
bot token, using Telegram's Bot Management mode
(https://core.telegram.org/bots/features#managed-bots).

## The flow

DOH owns one **manager bot** with BotFather "Bot Management Mode" enabled
(settings `TELEGRAM_MANAGER_BOT_TOKEN` / `TELEGRAM_MANAGER_BOT_USERNAME`;
stored only on DOH, never injected into customer containers).

1. **Connect** in the WebUI opens the standard vault setup-session, but
   `provider_telegram.schema()` returns `mode=link_poll` instead of a paste
   form: the creation deep link
   `https://t.me/newbot/{manager_username}/{suggested_username}?name={app_name}`
   (with a per-session random `suggested_username`, e.g. `myapp_a1b2c3_bot`)
   delivered **only as a QR code** — a `qr_data_uri` SVG rendered on DOH
   with `segno`, so the WebUI extension stays dependency-free. QR-only is
   deliberate: a clickable link needs a desktop Telegram client and fails
   silently without one, while every Telegram user can scan with their phone.
2. The user scans the QR; Telegram shows a pre-filled bot-creation dialog;
   one tap creates the bot **owned by the user, managed by our manager
   bot**. The confirmation happens on the phone while the desktop WebUI
   polls — the poll below doesn't care where the `managed_bot` update came
   from.
3. The WebUI polls `POST /api/integrations/credentials/poll`
   (browser-direct, CORS-bound, same signed setup token as submit). Each
   poll, `poll_setup` calls the manager bot's `getUpdates` (filtered to
   `managed_bot` updates), looks for the suggested username, and on a match
   calls `getManagedBotToken(user_id=bot.id)` to fetch the new bot's token.
4. The token is stored in `IntegrationUserCredential` exactly as before; the
   bot's creator (`ManagedBotUpdated.user.id`) is auto-added to
   `allowed_users`, so the bot answers them immediately. The WebUI then runs
   the usual invalidate → gateway-env rewrite → `system.gateway` restart.

Everything downstream is unchanged: `VaultUrlRewrite` placeholder, the
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_ALLOWED_USERS` env bindings, and the restart
contract in `docs/gateway_env_and_restart_design.md`.

## The generic `link_poll` vault mode

Telegram is not special-cased; it's the first user of a third vault connect
shape (alongside paste forms and Slack's custom renderer):

- `schema()` may return `mode: "link_poll"` with `qr_data_uri`, `qr_caption`,
  `link_note`, `pending_message`, and a `signed_state` dict.
- The setup-session view moves `signed_state` out of the schema and into the
  signed submit token (`provider_state`), so the browser can read but never
  forge it — the suggested username is the correlation key that proves which
  session a `managed_bot` update belongs to, and letting the browser supply
  it would allow claiming another user's bot.
- `POST /api/integrations/credentials/poll` dispatches to the provider
  module's `poll_setup(owner_user, environment, app_slug, state)` →
  `({"status": "pending"} | {"status": "connected", ...}, error)`.
- The WebUI's generic vault renderer (`doh-integrations.js`) dispatches
  `mode=link_poll` to a link+poll modal that polls until connected or the
  5-minute setup token expires.

**Configure** (already connected) returns a plain `mode=form` schema with
only `allowed_users`; `save_credentials` is config-only and never touches
the managed token.

## Correlation and statelessness

- `getUpdates` is called **without an offset**, so updates are never
  confirmed/consumed: concurrent connect sessions can't eat each other's
  events, and DOH keeps no offset state. Telegram retains unconfirmed
  updates for 24h — far longer than a setup session.
- Matching is by exact (case-insensitive) bot username. If the user edits
  the suggested username in Telegram's creation dialog, the session can't
  match and times out — the modal tells them to keep the suggested username.

## Token rotation

There is none. `refresh_outcome` is a plain DB read (like Slack's): Telegram
tokens never expire, and putting a `getManagedBotToken` call inside the
broker's batched refresh would let a slow api.telegram.org fail the whole
batch. If the user revokes the bot's token via BotFather, the bot stops
working and recovery is a reconnect — `getUpdates`/`getManagedBotToken` are
called only during a connect session's polling.
