"""Add / remove the local Codex auth marker that gates WebUI dropdown visibility.

In our architecture the real ChatGPT token lives in DOH; the broker swaps it onto
the wire. But the WebUI's model picker derives *availability* purely from local
state — `config.yaml` and `~/.hermes/auth.json`. Codex is a secondary provider
that must appear in the dropdown only while connected, so we mirror connect-state
into auth.json: write the `providers.openai-codex` block on connect, delete it on
disconnect. The token value is the inert placeholder (same as the boot seed); its
*presence*, not its value, is the dropdown signal. The picker's cache keys on a
semantic hash of auth.json, so add/remove flips it and the next `/api/models`
rebuild shows/hides Codex without a WebUI restart.

`active_provider` ends up set to openai-codex by the writer, but that's harmless:
config.yaml's `model.provider` (bedrock, the real default) wins in the WebUI's
resolution order, so Codex stays a *secondary* pick — verified.

Run AS THE GATEWAY USER (hermeswebui) so auth.json/auth.lock stay 0600 and owned
by the sandbox — the broker is root and must `runuser` into this. The agent's own
locked, atomic primitives do the write, so this is safe against a concurrent
gateway refresh.

    python codex_auth_marker.py connect      # seed the placeholder block
    python codex_auth_marker.py disconnect   # remove it

HERMES_HOME selects the store, exactly as the agent reads it. Exit 0 on success.
"""

import sys

SENTINEL = "DOH_PLACEHOLDER"
PROVIDER = "openai-codex"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("connect", "disconnect"):
        print("usage: codex_auth_marker.py {connect|disconnect}", file=sys.stderr)
        return 2
    action = argv[1]

    try:
        from hermes_cli import auth
    except Exception as exc:  # pragma: no cover - environment-specific import wiring
        print(f"[codex-marker] cannot import hermes_cli.auth: {exc}", file=sys.stderr)
        return 1

    if action == "connect":
        # Idempotent: only write if the block isn't already a usable placeholder.
        try:
            existing = auth._read_codex_tokens()
            tokens = existing.get("tokens", {}) if isinstance(existing, dict) else {}
            if tokens.get("access_token") and tokens.get("refresh_token"):
                print("[codex-marker] codex block already present; nothing to do")
                return 0
        except Exception:
            pass
        auth._save_codex_tokens({"access_token": SENTINEL, "refresh_token": SENTINEL})
        print(f"[codex-marker] wrote placeholder codex block to {auth.get_hermes_home() / 'auth.json'}")
        return 0

    # disconnect: remove the provider block (and pool/active entry) under lock.
    cleared = auth.clear_provider_auth(PROVIDER)
    print(f"[codex-marker] cleared codex block: {cleared}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
