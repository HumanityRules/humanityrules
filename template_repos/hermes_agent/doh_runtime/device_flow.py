"""Broker-run OAuth device-flow sessions for TLS-intercept providers."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx


logger = logging.getLogger("device_flow")


PHASE_PENDING = "pending"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"

DevicePhase = Literal["pending", "completed", "failed"]

CODEX_PROVIDER_SLUG = "openai-codex"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_USERCODE_URL = f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/usercode"
CODEX_DEVICEAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/token"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/oauth/token"
CODEX_DEVICEAUTH_REDIRECT_URI = f"{CODEX_OAUTH_ISSUER}/deviceauth/callback"
CODEX_VERIFICATION_URL = f"{CODEX_OAUTH_ISSUER}/codex/device"
CODEX_DEVICE_MAX_WAIT_SECONDS = 15 * 60
CODEX_DEVICE_MIN_POLL_SECONDS = 3

NOUS_PROVIDER_SLUG = "nous"
NOUS_PORTAL_BASE_URL = "https://portal.nousresearch.com"
NOUS_INFERENCE_BASE_URL = "https://inference-api.nousresearch.com/v1"
NOUS_OAUTH_CLIENT_ID = "hermes-cli"
NOUS_INFERENCE_INVOKE_SCOPE = "inference:invoke"
NOUS_LEGACY_AGENT_KEY_SCOPE = "inference:mint_agent_key"
NOUS_DEFAULT_SCOPE = f"{NOUS_INFERENCE_INVOKE_SCOPE} {NOUS_LEGACY_AGENT_KEY_SCOPE}"
NOUS_DEVICE_MIN_POLL_SECONDS = 1
NOUS_DEVICE_MAX_POLL_SECONDS = 30

DEVICE_HTTP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class DeviceStart:
    """Provider-normalized device-code start payload."""

    user_code: str
    verification_url: str
    interval: int
    expires_in: int
    opaque: dict[str, str]


@dataclass
class DeviceSession:
    """In-memory state for one provider device-login attempt."""

    provider: str
    user_code: str
    verification_url: str
    interval: int
    expires_in: int
    opaque: dict[str, str]
    started_at: float
    phase: DevicePhase = PHASE_PENDING
    error: str | None = None
    task: "asyncio.Task | None" = field(default=None, repr=False)

    def public_view(self) -> dict:
        """Return the subset the WebUI may see."""
        return {
            "provider": self.provider,
            "phase": self.phase,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "error": self.error,
        }


class DeviceFlowError(Exception):
    """A device-flow step failed in a way the WebUI can surface."""


class DeviceProvider:
    """Provider adapter used by the generic device-flow runner."""

    slug: str
    display_name: str

    async def start(self) -> DeviceStart:
        """Return the provider's user-facing device-code state."""
        raise NotImplementedError

    async def poll_tokens(self, session: DeviceSession) -> dict:
        """Poll until approval and return provider tokens."""
        raise NotImplementedError


class CodexDeviceProvider(DeviceProvider):
    """OpenAI Codex first-party device flow."""

    slug = CODEX_PROVIDER_SLUG
    display_name = "ChatGPT"

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id

    async def start(self) -> DeviceStart:
        """Get a Codex user code and verification URL."""
        async with httpx.AsyncClient(timeout=DEVICE_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                CODEX_USERCODE_URL,
                json={"client_id": self._client_id},
                headers={"Content-Type": "application/json"},
            )
        if response.status_code != 200:
            raise DeviceFlowError(f"OpenAI usercode request failed (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError:
            raise DeviceFlowError("OpenAI usercode response was not JSON")
        if not body.get("user_code") or not body.get("device_auth_id"):
            raise DeviceFlowError("OpenAI usercode response missing user_code/device_auth_id")
        return DeviceStart(
            user_code=str(body["user_code"]),
            verification_url=CODEX_VERIFICATION_URL,
            interval=max(CODEX_DEVICE_MIN_POLL_SECONDS, int(body.get("interval", 5))),
            expires_in=CODEX_DEVICE_MAX_WAIT_SECONDS,
            opaque={"device_auth_id": str(body["device_auth_id"])},
        )

    async def poll_tokens(self, session: DeviceSession) -> dict:
        """Poll Codex approval, then exchange the approval code for tokens."""
        approval = await self._poll_until_approved(session=session)
        return await self._exchange_code(
            authorization_code=approval["authorization_code"],
            code_verifier=approval["code_verifier"],
        )

    async def _poll_until_approved(self, session: DeviceSession) -> dict:
        """Poll Codex deviceauth/token until approved or expired."""
        device_auth_id = session.opaque.get("device_auth_id", "")
        async with httpx.AsyncClient(timeout=DEVICE_HTTP_TIMEOUT_SECONDS) as client:
            while True:
                if time.monotonic() - session.started_at > session.expires_in:
                    raise DeviceFlowError("device login timed out before approval")
                await asyncio.sleep(session.interval)
                response = await client.post(
                    CODEX_DEVICEAUTH_TOKEN_URL,
                    json={"device_auth_id": device_auth_id, "user_code": session.user_code},
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError:
                        raise DeviceFlowError("OpenAI approval response was not JSON")
                    if not body.get("authorization_code") or not body.get("code_verifier"):
                        raise DeviceFlowError("OpenAI approval missing authorization_code/code_verifier")
                    return body
                if response.status_code in (403, 404):
                    continue
                raise DeviceFlowError(f"OpenAI poll failed (HTTP {response.status_code})")

    async def _exchange_code(self, authorization_code: str, code_verifier: str) -> dict:
        """Exchange the Codex authorization code for access/refresh tokens."""
        async with httpx.AsyncClient(timeout=DEVICE_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(
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
        if response.status_code != 200:
            raise DeviceFlowError(f"OpenAI token exchange failed (HTTP {response.status_code})")
        try:
            return response.json()
        except ValueError:
            raise DeviceFlowError("OpenAI token exchange response was not JSON")


class NousDeviceProvider(DeviceProvider):
    """Nous Portal OAuth device flow."""

    slug = NOUS_PROVIDER_SLUG
    display_name = "Nous Portal"

    def __init__(self, portal_base_url: str, client_id: str, scope: str, inference_base_url: str) -> None:
        self._portal_base_url = portal_base_url.rstrip("/")
        self._client_id = client_id
        self._scope = scope
        self._inference_base_url = inference_base_url.rstrip("/")

    async def start(self) -> DeviceStart:
        """Get a Nous Portal device code and verification URL."""
        async with httpx.AsyncClient(timeout=DEVICE_HTTP_TIMEOUT_SECONDS, headers={"Accept": "application/json"}) as client:
            response = await client.post(
                f"{self._portal_base_url}/api/oauth/device/code",
                data={"client_id": self._client_id, "scope": self._scope},
            )
        if response.status_code != 200:
            raise DeviceFlowError(f"Nous Portal device-code request failed (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError:
            raise DeviceFlowError("Nous Portal device-code response was not JSON")
        missing = [
            key
            for key in ("device_code", "user_code", "verification_uri_complete", "expires_in", "interval")
            if key not in body
        ]
        if missing:
            raise DeviceFlowError(f"Nous Portal device-code response missing {', '.join(missing)}")
        return DeviceStart(
            user_code=str(body["user_code"]),
            verification_url=str(body["verification_uri_complete"]),
            interval=max(NOUS_DEVICE_MIN_POLL_SECONDS, int(body["interval"])),
            expires_in=max(1, int(body["expires_in"])),
            opaque={"device_code": str(body["device_code"])},
        )

    async def poll_tokens(self, session: DeviceSession) -> dict:
        """Poll Nous Portal until approval and return OAuth tokens."""
        deadline = session.started_at + session.expires_in
        current_interval = min(max(NOUS_DEVICE_MIN_POLL_SECONDS, session.interval), NOUS_DEVICE_MAX_POLL_SECONDS)
        async with httpx.AsyncClient(timeout=DEVICE_HTTP_TIMEOUT_SECONDS, headers={"Accept": "application/json"}) as client:
            while time.monotonic() < deadline:
                await asyncio.sleep(current_interval)
                response = await client.post(
                    f"{self._portal_base_url}/api/oauth/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": self._client_id,
                        "device_code": session.opaque.get("device_code", ""),
                    },
                )
                if response.status_code == 200:
                    try:
                        payload = response.json()
                    except ValueError:
                        raise DeviceFlowError("Nous Portal token response was not JSON")
                    if not payload.get("access_token") or not payload.get("refresh_token"):
                        raise DeviceFlowError("Nous Portal token response missing access_token/refresh_token")
                    return {
                        **payload,
                        "portal_base_url": self._portal_base_url,
                        "inference_base_url": payload.get("inference_base_url") or self._inference_base_url,
                    }
                error_code = self._error_code(response=response)
                if error_code == "authorization_pending":
                    continue
                if error_code == "slow_down":
                    current_interval = min(current_interval + 1, NOUS_DEVICE_MAX_POLL_SECONDS)
                    continue
                raise DeviceFlowError(f"Nous Portal poll failed ({error_code or 'unknown error'})")
        raise DeviceFlowError("device login timed out before approval")

    def _error_code(self, response: httpx.Response) -> str:
        """Return an OAuth error code from a failed Nous response."""
        try:
            body = response.json()
        except ValueError:
            return f"http {response.status_code}"
        return str(body.get("error") or f"http {response.status_code}")


class OAuthDeviceFlow:
    """Owns in-flight device-login sessions across provider adapters."""

    def __init__(self, providers: dict[str, DeviceProvider], submit_tokens: Callable[[str, dict], Awaitable[bool]]) -> None:
        self._providers = providers
        self._submit_tokens = submit_tokens
        self._lock = asyncio.Lock()
        self._sessions: dict[str, DeviceSession] = {}

    async def start(self, provider_slug: str) -> dict:
        """Begin a provider device-login and return its public session view."""
        provider = self._providers.get(provider_slug)
        if provider is None:
            raise DeviceFlowError(f"{provider_slug} is not a device-flow provider")
        async with self._lock:
            await self._cancel_locked(provider_slug=provider_slug)
            start = await provider.start()
            session = DeviceSession(
                provider=provider.slug,
                user_code=start.user_code,
                verification_url=start.verification_url,
                interval=start.interval,
                expires_in=start.expires_in,
                opaque=start.opaque,
                started_at=time.monotonic(),
            )
            session.task = asyncio.create_task(self._run_poll(provider=provider, session=session))
            self._sessions[provider_slug] = session
            logger.info("%s device-login started (user_code=%s)", provider.slug, session.user_code)
            return session.public_view()

    async def status(self, provider_slug: str) -> dict | None:
        """Return the provider's current public session view, or None."""
        async with self._lock:
            session = self._sessions.get(provider_slug)
            return session.public_view() if session is not None else None

    async def cancel(self, provider_slug: str) -> None:
        """Cancel the provider's in-flight session."""
        async with self._lock:
            await self._cancel_locked(provider_slug=provider_slug)

    async def _cancel_locked(self, provider_slug: str) -> None:
        """Cancel + forget one provider session. Caller holds `_lock`."""
        session = self._sessions.pop(provider_slug, None)
        if session is None or session.task is None:
            return
        session.task.cancel()
        try:
            await session.task
        except (asyncio.CancelledError, Exception):
            pass

    async def _run_poll(self, provider: DeviceProvider, session: DeviceSession) -> None:
        """Poll for provider approval, submit tokens to DOH, and record terminal state."""
        try:
            tokens = await provider.poll_tokens(session=session)
            refresh_token = tokens.get("refresh_token", "")
            if not refresh_token:
                raise DeviceFlowError(f"{provider.display_name} returned no refresh_token")
            stored = await self._submit_tokens(provider.slug, tokens)
            if not stored:
                raise DeviceFlowError("could not store the credential in the control plane")
            session.phase = PHASE_COMPLETED
            logger.info("%s device-login completed (user_code=%s)", provider.slug, session.user_code)
        except asyncio.CancelledError:
            raise
        except DeviceFlowError as exc:
            session.phase = PHASE_FAILED
            session.error = str(exc)
            logger.error("%s device-login failed (user_code=%s): %s", provider.slug, session.user_code, exc)
        except Exception as exc:
            session.phase = PHASE_FAILED
            session.error = "unexpected error during device login"
            logger.exception("%s device-login crashed (user_code=%s): %s", provider.slug, session.user_code, exc)


def build_default_device_flow(submit_tokens: Callable[[str, dict], Awaitable[bool]]) -> OAuthDeviceFlow:
    """Build the device-flow registry for DOH-managed OAuth providers."""
    providers: dict[str, DeviceProvider] = {
        CODEX_PROVIDER_SLUG: CodexDeviceProvider(client_id=CODEX_OAUTH_CLIENT_ID),
        NOUS_PROVIDER_SLUG: NousDeviceProvider(
            portal_base_url=NOUS_PORTAL_BASE_URL,
            client_id=NOUS_OAUTH_CLIENT_ID,
            scope=NOUS_DEFAULT_SCOPE,
            inference_base_url=NOUS_INFERENCE_BASE_URL,
        ),
    }
    return OAuthDeviceFlow(providers=providers, submit_tokens=submit_tokens)
