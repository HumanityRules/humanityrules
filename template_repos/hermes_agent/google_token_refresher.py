"""Outside-the-sandbox refresher for the user's Google access token.

Runs as part of the supervisor process (same trust level as the sigv4 /
haproxy sidecars). Periodically asks DOH's control plane for a fresh
Google access token for the owner user, and writes just the bare token
string to a file that the nono sandbox can read.

Design properties:
- The refresh token, DOH's OAuth client_secret, and the env bearer NEVER
  cross into the sandbox. Only short-lived access tokens do, via the file.
- The file path is hardcoded. The nono profile has a `read_file` grant
  for exactly this path; any future agent-side bridge hardcodes the
  same path.
- Soft-fail on "not connected" (DOH returns 404): log and keep polling.
  The sandbox boots, Hermes runs, Google-dependent tool calls fail with
  a clear message until the user connects their account.
- Fail-fast on startup misconfiguration (missing env vars, DOH unreachable
  with auth errors) — the supervisor treats exit as a container-replace
  signal, which is the correct response.

Environment contract (set by deploy_app.py's env-bearer overlay):
- DOH_ENV_BEARER     — bearer for DOH /api/integrations/google/token.
- DOH_OWNER_USERNAME — whose Google account this container is for.
- DOH_CONTROL_PLANE_URL — base URL for DOH (e.g. https://devopshero.ai).

Local-testing escape hatch:
- DOH_TOKEN_FILE_OVERRIDE — when set, writes tokens to this absolute path
  instead of the hardcoded container path. Used for Mac-local Layer-2 smoke
  runs where /home/hermeswebui/... doesn't exist. Do not set in production.
"""

import json
import logging
import os
import random
import signal
import sys
import time
import urllib.error
import urllib.request

# Hardcoded paths so the nono profile's read_file grant and any future
# in-sandbox bridge can target them without env-var indirection.
DEFAULT_TOKEN_DIR = "/home/hermeswebui/.doh/credentials"
DEFAULT_TOKEN_FILE = f"{DEFAULT_TOKEN_DIR}/google_access_token"


def _resolve_token_file() -> str:
    override = os.environ.get("DOH_TOKEN_FILE_OVERRIDE", "")
    return override or DEFAULT_TOKEN_FILE

# How close to expiry we trigger the next refresh. Google access tokens
# are typically 3600s; 300s of head-room leaves comfortable slack.
REFRESH_LEAD_SECONDS = 300

# Min sleep between refreshes even if DOH hands us something very short.
# Guards against a tight loop if DOH's expires_in is ever zero/tiny.
MIN_SLEEP_SECONDS = 30

# Polling interval when the user hasn't connected Google yet (DOH returns 404).
# Short enough that a connect shows up quickly, long enough not to hammer DOH.
NOT_CONNECTED_POLL_SECONDS = 60

# Exponential backoff bounds for transient errors (network, 5xx).
BACKOFF_INITIAL_SECONDS = 5
BACKOFF_MAX_SECONDS = 300


logger = logging.getLogger("google_token_refresher")


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


def _write_token_atomically(token: str, token_file: str) -> None:
    """Write *token* to *token_file* via temp+rename so readers never see a partial write."""
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    tmp = f"{token_file}.tmp"
    with open(tmp, "w") as f:
        f.write(token)
    os.replace(tmp, token_file)


def _remove_token(token_file: str) -> None:
    """Delete *token_file* if present. Used when DOH says the connection is gone."""
    try:
        os.remove(token_file)
    except FileNotFoundError:
        pass


def _fetch_access_token(control_plane_url: str, bearer: str, owner_username: str) -> dict:
    """Call DOH's refresh endpoint and return a parsed outcome.

    Returns a dict with one of:
      {"kind": "ok", "access_token": str, "expires_in": int}
      {"kind": "not_connected"}       — DOH 404 (user hasn't connected Google)
      {"kind": "revoked"}              — DOH 410 (refresh token revoked at Google)
      {"kind": "transient", "detail": str} — retryable failures (network, 5xx)
      {"kind": "fatal", "detail": str}     — unrecoverable (401 bad bearer, 500 misconfig)
    """
    url = f"{control_plane_url.rstrip('/')}/api/integrations/google/token"
    body = json.dumps({"owner_username": owner_username}).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
    except urllib.error.URLError as exc:
        return {"kind": "transient", "detail": f"network: {exc.reason}"}
    except Exception as exc:
        return {"kind": "transient", "detail": f"unexpected: {exc}"}

    if status == 200:
        return {
            "kind": "ok",
            "access_token": payload["access_token"],
            "expires_in": int(payload.get("expires_in", 0)),
        }
    if status == 404:
        return {"kind": "not_connected"}
    if status == 410:
        return {"kind": "revoked"}
    if status in (401, 500):
        return {"kind": "fatal", "detail": f"http {status}: {payload.get('error', 'unknown')}"}
    return {"kind": "transient", "detail": f"http {status}: {payload.get('error', 'unknown')}"}


def _main_loop(control_plane_url: str, bearer: str, owner_username: str, token_file: str) -> None:
    backoff = BACKOFF_INITIAL_SECONDS
    while True:
        outcome = _fetch_access_token(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
        )
        kind = outcome["kind"]

        if kind == "ok":
            _write_token_atomically(token=outcome["access_token"], token_file=token_file)
            sleep_for = max(MIN_SLEEP_SECONDS, outcome["expires_in"] - REFRESH_LEAD_SECONDS)
            logger.info("refreshed google access token, next refresh in %ds", sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "not_connected":
            _remove_token(token_file=token_file)
            sleep_for = NOT_CONNECTED_POLL_SECONDS
            logger.info("google not connected for user=%s, polling in %ds", owner_username, sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "revoked":
            _remove_token(token_file=token_file)
            sleep_for = NOT_CONNECTED_POLL_SECONDS
            logger.error("google refresh token revoked for user=%s, polling in %ds", owner_username, sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "fatal":
            logger.error("FATAL: %s", outcome["detail"])
            sys.exit(1)
        else:  # transient
            # Jitter so parallel refreshers across envs don't synchronize.
            sleep_for = backoff + random.uniform(0, backoff / 2)
            logger.error("transient refresh failure (%s), retrying in %.1fs", outcome["detail"], sleep_for)
            backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)

        time.sleep(sleep_for)


def _install_signal_handlers() -> None:
    """Exit cleanly on SIGTERM/SIGINT so supervisor can reap the child."""
    def _exit(signum, frame):
        logger.info("received signal %d, exiting", signum)
        sys.exit(0)
    signal.signal(signal.SIGTERM, _exit)
    signal.signal(signal.SIGINT, _exit)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [google_token_refresher] %(message)s",
    )
    _install_signal_handlers()

    control_plane_url = _require_env("DOH_CONTROL_PLANE_URL")
    bearer = _require_env("DOH_ENV_BEARER")
    owner_username = _require_env("DOH_OWNER_USERNAME")
    token_file = _resolve_token_file()

    logger.info(
        "starting refresher for owner=%s against %s, token_file=%s",
        owner_username, control_plane_url, token_file,
    )
    _main_loop(
        control_plane_url=control_plane_url,
        bearer=bearer,
        owner_username=owner_username,
        token_file=token_file,
    )


if __name__ == "__main__":
    main()
