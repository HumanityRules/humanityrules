"""Broker-run OpenAI Codex device-login flow.

Codex (ChatGPT-subscription) auth can't use a redirect dance — OpenAI's
first-party client lives on an Auth0 tenant we don't administer, so we can't
register a callback. Instead we run OpenAI's *device flow* from the broker
(outside the sandbox), exactly as the Codex CLI does:

  1. POST .../api/accounts/deviceauth/usercode {client_id}
         -> {user_code, device_auth_id, interval}
     Show the user `user_code` + the verification URL; they approve in their
     own browser, signed in to their ChatGPT account.
  2. Poll POST .../api/accounts/deviceauth/token {device_auth_id, user_code}
         200 -> approved; body carries {authorization_code, code_verifier}
         403/404 -> still pending; keep polling
  3. POST .../oauth/token (form) grant_type=authorization_code
         -> {access_token, refresh_token}
  4. Hand the refresh_token to DOH, which stores it and thereafter mints
     access tokens for the TLS-intercept proxy. The refresh token transits
     broker memory once and is never written to disk or shown to the sandbox.

The poll loop runs for minutes, so it lives in a background asyncio task; the
control API exposes start / status / cancel for the WebUI to drive. Only one
session runs at a time (a fresh start supersedes any in-flight one).

This module owns only the OpenAI side + the in-memory session. Persisting the
refresh_token to DOH is injected as `submit_refresh_token` so the broker can
wire it to its existing control-plane POST helper (and so this stays testable
without a control plane).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal

import httpx


logger = logging.getLogger("codex_device_flow")


# Codex's first-party public OAuth client + endpoints (secretless public client).
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_USERCODE_URL = f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICEAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/token"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/oauth/token"
CODEX_DEVICEAUTH_REDIRECT_URI = f"{CODEX_OAUTH_ISSUER}/deviceauth/callback"
# Browser URL the user opens to enter the code.
CODEX_VERIFICATION_URL = f"{CODEX_OAUTH_ISSUER}/codex/device"

# Overall ceiling on a pending approval, matching the Codex CLI's 15 minutes.
CODEX_DEVICE_MAX_WAIT_SECONDS = 15 * 60
# Floor on the server-suggested poll interval.
CODEX_DEVICE_MIN_POLL_SECONDS = 3
# Per-request HTTP timeout for the OpenAI calls.
CODEX_HTTP_TIMEOUT_SECONDS = 30


# Session phase, surfaced to the WebUI via /status.
PHASE_PENDING = "pending"      # waiting for the user to approve in their browser
PHASE_COMPLETED = "completed"  # refresh_token obtained + handed to DOH
PHASE_FAILED = "failed"        # gave up (timeout, OpenAI error, DOH store error)

DevicePhase = Literal["pending", "completed", "failed"]


@dataclass
class CodexDeviceSession:
    """In-memory state for one device-login attempt."""

    user_code: str
    device_auth_id: str
    verification_url: str
    interval: int
    started_at: float
    phase: DevicePhase = PHASE_PENDING
    error: str | None = None
    task: "asyncio.Task | None" = field(default=None, repr=False)

    def public_view(self) -> dict:
        """The subset the WebUI may see — never the device_auth_id or any token."""
        return {
            "phase": self.phase,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "error": self.error,
        }


class CodexDeviceFlowError(Exception):
    """A device-flow step failed in a way the user can act on (surfaced to the WebUI)."""


class CodexDeviceFlow:
    """Owns the single in-flight Codex device-login session for this broker."""

    def __init__(
        self,
        submit_refresh_token: Callable[[str, str], Awaitable[bool]],
        client_id: str = CODEX_OAUTH_CLIENT_ID,
    ) -> None:
        # submit_refresh_token(refresh_token, access_token) -> bool: persist to
        # DOH; returns True on success. Injected so this module stays decoupled
        # from the control-plane HTTP helper (and unit-testable).
        self._submit_refresh_token = submit_refresh_token
        self._client_id = client_id
        self._lock = asyncio.Lock()
        self._session: CodexDeviceSession | None = None

    async def start(self) -> dict:
        """Begin a device-login: get a user code, kick off the background poll.

        Returns the public session view immediately (with the user_code to
        display). Supersedes any in-flight session.
        """
        async with self._lock:
            await self._cancel_locked()
            usercode = await self._request_usercode()
            session = CodexDeviceSession(
                user_code=usercode["user_code"],
                device_auth_id=usercode["device_auth_id"],
                verification_url=CODEX_VERIFICATION_URL,
                interval=max(CODEX_DEVICE_MIN_POLL_SECONDS, int(usercode.get("interval", 5))),
                started_at=time.monotonic(),
            )
            session.task = asyncio.create_task(self._run_poll(session))
            self._session = session
            logger.info("codex device-login started (user_code=%s)", session.user_code)
            return session.public_view()

    async def status(self) -> dict | None:
        """Return the current session's public view, or None when none exists."""
        async with self._lock:
            return self._session.public_view() if self._session is not None else None

    async def cancel(self) -> None:
        """Cancel any in-flight session (e.g. the user closed the dialog)."""
        async with self._lock:
            await self._cancel_locked()

    async def _cancel_locked(self) -> None:
        """Cancel + forget the current session. Caller holds `_lock`."""
        session = self._session
        self._session = None
        if session is None or session.task is None:
            return
        session.task.cancel()
        try:
            await session.task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run_poll(self, session: CodexDeviceSession) -> None:
        """Poll for approval, exchange for tokens, hand the refresh_token to DOH.

        Runs as the session's background task. Terminal state is recorded on
        the session (`phase`/`error`) for /status to report; this coroutine
        never raises out (it's a fire-and-forget task).
        """
        try:
            approval = await self._poll_until_approved(session)
            tokens = await self._exchange_code(
                authorization_code=approval["authorization_code"],
                code_verifier=approval["code_verifier"],
            )
            refresh_token = tokens.get("refresh_token", "")
            access_token = tokens.get("access_token", "")
            if not refresh_token:
                raise CodexDeviceFlowError("OpenAI returned no refresh_token")
            stored = await self._submit_refresh_token(refresh_token, access_token)
            if not stored:
                raise CodexDeviceFlowError("could not store the credential in the control plane")
            session.phase = PHASE_COMPLETED
            logger.info("codex device-login completed (user_code=%s)", session.user_code)
        except asyncio.CancelledError:
            raise
        except CodexDeviceFlowError as exc:
            session.phase = PHASE_FAILED
            session.error = str(exc)
            logger.error("codex device-login failed (user_code=%s): %s", session.user_code, exc)
        except Exception as exc:
            session.phase = PHASE_FAILED
            session.error = "unexpected error during device login"
            logger.exception("codex device-login crashed (user_code=%s): %s", session.user_code, exc)

    async def _request_usercode(self) -> dict:
        """Step 1: get a user_code + device_auth_id from OpenAI."""
        async with httpx.AsyncClient(timeout=CODEX_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                CODEX_USERCODE_URL,
                json={"client_id": self._client_id},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise CodexDeviceFlowError(f"OpenAI usercode request failed (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError:
            raise CodexDeviceFlowError("OpenAI usercode response was not JSON")
        if not body.get("user_code") or not body.get("device_auth_id"):
            raise CodexDeviceFlowError("OpenAI usercode response missing user_code/device_auth_id")
        return body

    async def _poll_until_approved(self, session: CodexDeviceSession) -> dict:
        """Step 2: poll deviceauth/token until 200 (approved) or we time out.

        200 -> approved, body carries authorization_code + code_verifier.
        403/404 -> still pending, keep going. Other -> hard error.
        """
        async with httpx.AsyncClient(timeout=CODEX_HTTP_TIMEOUT_SECONDS) as client:
            while True:
                if time.monotonic() - session.started_at > CODEX_DEVICE_MAX_WAIT_SECONDS:
                    raise CodexDeviceFlowError("device login timed out before approval")
                await asyncio.sleep(session.interval)
                resp = await client.post(
                    CODEX_DEVICEAUTH_TOKEN_URL,
                    json={"device_auth_id": session.device_auth_id, "user_code": session.user_code},
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        raise CodexDeviceFlowError("OpenAI approval response was not JSON")
                    if not body.get("authorization_code") or not body.get("code_verifier"):
                        raise CodexDeviceFlowError("OpenAI approval missing authorization_code/code_verifier")
                    return body
                if resp.status_code in (403, 404):
                    continue
                raise CodexDeviceFlowError(f"OpenAI poll failed (HTTP {resp.status_code})")

    async def _exchange_code(self, authorization_code: str, code_verifier: str) -> dict:
        """Step 3: exchange the authorization_code (+ PKCE verifier) for tokens."""
        async with httpx.AsyncClient(timeout=CODEX_HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": CODEX_DEVICEAUTH_REDIRECT_URI,
                    "client_id": self._client_id,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise CodexDeviceFlowError(f"OpenAI token exchange failed (HTTP {resp.status_code})")
        try:
            return resp.json()
        except ValueError:
            raise CodexDeviceFlowError("OpenAI token exchange response was not JSON")
