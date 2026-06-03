"""Seed a placeholder Codex token into Hermes's auth store at container boot.

Credential model A (TLS-intercept): the real ChatGPT access token lives outside
the sandbox and the broker swaps it onto the wire to chatgpt.com. But Hermes
won't emit a Codex request at all unless `~/.hermes/auth.json` already holds a
codex token — `_read_codex_tokens` requires both `access_token` and
`refresh_token` non-empty. So we seed a non-JWT sentinel for both before the
gateway starts.

Why a non-JWT sentinel works (verified against the pinned agent's auth.py):
- `_codex_access_token_is_expiring` parses the JWT `exp`; a non-JWT string has
  no parseable claims, so it reads as never-expiring. Hermes therefore never
  self-refreshes on the normal path and never rewrites auth.json — the broker's
  swapped token is what actually authenticates upstream.
- We write only the `providers.openai-codex` singleton (via the agent's own
  `_save_codex_tokens`, which locks + atomically writes + sets active_provider).
  At first boot there is no `credential_pool`, so the pool-sync is a no-op and
  the singleton-first resolver returns the sentinel without a network call.

Idempotent: if a usable codex token is already present (e.g. a prior boot, or a
real token written by some future in-sandbox flow), leave it untouched.

Run as the gateway user (auth.json is mode 0600) via the WebUI venv python:
    python seed_codex_placeholder.py
HERMES_HOME selects the store location, exactly as the agent reads it.
"""

import sys

SENTINEL = "DOH_PLACEHOLDER"


def main() -> int:
    try:
        from hermes_cli import auth
    except Exception as exc:  # pragma: no cover - import wiring is environment-specific
        print(f"[seed-codex] cannot import hermes_cli.auth: {exc}", file=sys.stderr)
        return 1

    # Already have a usable codex token? Don't clobber it.
    try:
        existing = auth._read_codex_tokens()
        tokens = existing.get("tokens", {}) if isinstance(existing, dict) else {}
        if tokens.get("access_token") and tokens.get("refresh_token"):
            print("[seed-codex] codex token already present; leaving it untouched")
            return 0
    except Exception:
        # No/!invalid existing token (the common first-boot case): fall through and seed.
        pass

    auth._save_codex_tokens({"access_token": SENTINEL, "refresh_token": SENTINEL})
    print(f"[seed-codex] seeded placeholder codex token at {auth.get_hermes_home() / 'auth.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
