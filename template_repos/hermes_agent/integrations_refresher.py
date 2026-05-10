"""Outside-the-sandbox refresher for per-user third-party integration tokens.

Runs as a supervisor-managed sidecar (same trust level as the sigv4 / haproxy
helpers). For each configured provider, periodically asks DOH's control plane
for a fresh short-lived access token, writes it to a well-known file the nono
sandbox can read, and publishes a per-provider status line to a shared
`integrations_status.json` that the WebUI extension renders.

Design properties:
- Refresh tokens, DOH's OAuth client secrets, and the env bearer NEVER cross
  into the sandbox. Only short-lived access tokens do, via per-provider files.
- Token file paths and the status-file path are hardcoded. The nono profile
  grants targeted read access to them; any in-sandbox bridge hardcodes the
  same paths.
- Soft-fail on "not connected" (DOH 404): log, remove the token file, publish
  `status: not_connected`, keep polling. The sandbox boots, the agent runs,
  provider-dependent tool calls fail with a clear message until the user
  connects.
- Fail-fast on startup misconfiguration (missing env vars) — the supervisor
  treats refresher exit as a container-replace signal, which is correct.

Environment contract (set by deploy_app.py's env-bearer overlay):
- DOH_ENV_BEARER       — bearer for DOH's per-env integration endpoints.
- DOH_OWNER_USERNAME   — whose grants this container is for.
- DOH_CONTROL_PLANE_URL — base URL for DOH (e.g. https://devopshero.ai).

Local-testing escape hatches:
- DOH_TOKEN_FILE_OVERRIDE — redirect Google's token file to this absolute
  path instead of the hardcoded container path.
- DOH_STATUS_FILE_OVERRIDE — redirect the shared status file.
"""

import json
import logging
import os
import random
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Hardcoded paths so the nono profile's read grants and any in-sandbox bridge
# can target them without env-var indirection.
DEFAULT_TOKEN_DIR = "/home/hermeswebui/.doh/credentials"
DEFAULT_GOOGLE_TOKEN_FILE = f"{DEFAULT_TOKEN_DIR}/google_access_token"

# The status file lives in the WebUI extension directory so the browser can
# fetch it same-origin via /extensions/integrations_status.json. The dir is
# chowned to hermeswebui at image build time.
DEFAULT_EXTENSION_DIR = "/opt/doh/webui-extension"
DEFAULT_STATUS_FILE = f"{DEFAULT_EXTENSION_DIR}/integrations_status.json"

# How close to expiry we trigger the next refresh. Google access tokens are
# typically 3600s; 300s of head-room leaves comfortable slack.
REFRESH_LEAD_SECONDS = 300

# Min sleep between refreshes even if DOH hands us something very short.
# Guards against a tight loop if DOH's expires_in is ever zero/tiny.
MIN_SLEEP_SECONDS = 30

# Cap on connected-state sleep so post-connect/post-disconnect state flips
# surface in the WebUI within a minute. Without this, the loop would sleep
# `expires_in - REFRESH_LEAD_SECONDS` (≈55min) and the Integrations pane
# would show stale "connected" status for up to an hour after a revoke.
# Cost: one DOH refresh per minute per container while connected — fine
# at pre-beta scale, will need a separate status-only endpoint later.
MAX_CONNECTED_POLL_SECONDS = 60

# Polling interval when the user hasn't connected this provider yet.
NOT_CONNECTED_POLL_SECONDS = 60

# Exponential backoff bounds for transient errors (network, 5xx).
BACKOFF_INITIAL_SECONDS = 5
BACKOFF_MAX_SECONDS = 300


logger = logging.getLogger("integrations_refresher")

# Serializes concurrent read-modify-write on the shared status file. One
# refresher thread per provider; they all touch the same file.
_status_lock = threading.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_google_token_file() -> str:
    override = os.environ.get("DOH_TOKEN_FILE_OVERRIDE", "")
    return override or DEFAULT_GOOGLE_TOKEN_FILE


def _resolve_status_file() -> str:
    override = os.environ.get("DOH_STATUS_FILE_OVERRIDE", "")
    return override or DEFAULT_STATUS_FILE


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        logger.error("FATAL: %s must be set", name)
        sys.exit(1)
    return value


def _write_file_atomically(path: str, content: str) -> None:
    """Write *content* to *path* via temp+rename so readers never see a partial write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def _remove_file(path: str) -> None:
    """Delete *path* if present. Used when DOH says the connection is gone."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _read_status_file(status_file: str) -> dict:
    try:
        with open(status_file) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _publish_status(
    status_file: str,
    control_plane_url: str,
    env_slug: str,
    owner_username: str,
    provider: str,
    provider_entry: dict,
) -> None:
    """Merge *provider_entry* into the status file under ``providers.<provider>``.

    Read-modify-write under a lock so concurrent provider loops don't clobber
    each other's entries. The envelope fields (control plane URL, env, owner)
    are rewritten on every publish — cheap, keeps the file self-describing
    even if a reader catches it between provider initializations.
    """
    with _status_lock:
        current = _read_status_file(status_file=status_file)
        providers = current.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}
        providers[provider] = provider_entry
        updated = {
            "doh_control_plane_url": control_plane_url,
            "env_slug": env_slug,
            "owner_username": owner_username,
            "providers": providers,
        }
        _write_file_atomically(
            path=status_file,
            content=json.dumps(updated, indent=2) + "\n",
        )


def _fetch_google_access_token(control_plane_url: str, bearer: str, owner_username: str) -> dict:
    """Call DOH's Google refresh endpoint and classify the response.

    Returns one of:
      {"kind": "ok", "access_token": str, "expires_in": int}
      {"kind": "not_connected"}               — DOH 404
      {"kind": "revoked"}                     — DOH 410
      {"kind": "transient", "detail": str}    — retryable (network, 5xx)
      {"kind": "fatal", "detail": str}        — unrecoverable (401 bad bearer, 500 misconfig)
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


def _google_refresh_loop(
    control_plane_url: str,
    bearer: str,
    env_slug: str,
    owner_username: str,
    token_file: str,
    status_file: str,
) -> None:
    """Refresh Google access tokens forever; publish provider status on each tick."""
    label = "Google Workspace"
    backoff = BACKOFF_INITIAL_SECONDS

    def _publish(entry: dict) -> None:
        _publish_status(
            status_file=status_file,
            control_plane_url=control_plane_url,
            env_slug=env_slug,
            owner_username=owner_username,
            provider="google",
            provider_entry=entry,
        )

    while True:
        outcome = _fetch_google_access_token(
            control_plane_url=control_plane_url,
            bearer=bearer,
            owner_username=owner_username,
        )
        kind = outcome["kind"]

        if kind == "ok":
            _write_file_atomically(path=token_file, content=outcome["access_token"])
            _publish({"label": label, "status": "connected", "last_refreshed_at": _utc_now_iso()})
            sleep_for = max(
                MIN_SLEEP_SECONDS,
                min(MAX_CONNECTED_POLL_SECONDS, outcome["expires_in"] - REFRESH_LEAD_SECONDS),
            )
            logger.info("refreshed google access token, next refresh in %ds", sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "not_connected":
            _remove_file(path=token_file)
            _publish({"label": label, "status": "not_connected", "last_refreshed_at": None})
            sleep_for = NOT_CONNECTED_POLL_SECONDS
            logger.info("google not connected for user=%s, polling in %ds", owner_username, sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "revoked":
            _remove_file(path=token_file)
            _publish({"label": label, "status": "revoked", "last_refreshed_at": None})
            sleep_for = NOT_CONNECTED_POLL_SECONDS
            logger.error("google refresh token revoked for user=%s, polling in %ds", owner_username, sleep_for)
            backoff = BACKOFF_INITIAL_SECONDS
        elif kind == "fatal":
            logger.error("FATAL: %s", outcome["detail"])
            sys.exit(1)
        else:  # transient
            _publish({"label": label, "status": "transient_error", "last_refreshed_at": None})
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
        format="[%(asctime)s] [integrations_refresher] %(message)s",
    )
    _install_signal_handlers()

    control_plane_url = _require_env(name="DOH_CONTROL_PLANE_URL")
    bearer = _require_env(name="DOH_ENV_BEARER")
    owner_username = _require_env(name="DOH_OWNER_USERNAME")
    env_slug = os.environ.get("DOH_ENV_SLUG", "")
    google_token_file = _resolve_google_token_file()
    status_file = _resolve_status_file()

    logger.info(
        "starting refresher for owner=%s env=%s against %s, token_file=%s, status_file=%s",
        owner_username, env_slug, control_plane_url, google_token_file, status_file,
    )

    # One provider today, on the main thread. Adding Slack/Notion means
    # another `_*_refresh_loop` run in a daemon thread; the shared status
    # file is already read-modify-write safe.
    _google_refresh_loop(
        control_plane_url=control_plane_url,
        bearer=bearer,
        env_slug=env_slug,
        owner_username=owner_username,
        token_file=google_token_file,
        status_file=status_file,
    )


if __name__ == "__main__":
    main()
