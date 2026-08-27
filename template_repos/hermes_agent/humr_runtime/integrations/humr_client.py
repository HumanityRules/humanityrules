"""Outbound JSON client for HUMR's app-facing integration endpoints.

One `HumrClient` instance, built by the broker at startup, owns the
control-plane URL and the app's bearer. Everything in the broker that talks
to HUMR (token refresh, device-flow completion, disconnect, vault setup
sessions, usage reporting, entitlement refresh) goes through it — the bearer
never leaves this module.

The bearer is per-app, so HUMR derives the App and its owner from it; nothing
about identity travels in the request. `owner_username` and `app_slug` are
kept on the instance only for the WebUI status card and logs — they are never
put on the wire, so a loopback caller of the control API cannot override them.
"""

import logging

import httpx


logger = logging.getLogger("humr_client")


class HumrClient:
    """Async JSON client bound to one app bearer."""

    def __init__(self, control_plane_url: str, bearer: str, owner_username: str, app_slug: str) -> None:
        self.control_plane_url = control_plane_url.rstrip("/")
        self.owner_username = owner_username
        self.app_slug = app_slug
        self._bearer = bearer

    async def post_json(self, path: str, payload: dict, timeout_seconds: int) -> tuple[int, dict]:
        """POST JSON to HUMR and return `(status, parsed body)`.

        The payload is sent exactly as given — identity comes from the bearer.
        Transport failures and unparseable success bodies come back as a
        synthetic 502, and an unparseable error body keeps its real status
        with a fallback body, so callers only ever branch on the status code.
        """
        url = f"{self.control_plane_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url=url, json=payload, headers={"Authorization": f"Bearer {self._bearer}"})
        except Exception as exc:
            logger.error("control plane request failed path=%s: %s", path, exc)
            return 502, {"error": "control plane request failed"}
        return self._parsed(response=response, path=path)

    async def get_json(self, path: str, timeout_seconds: int) -> tuple[int, dict]:
        """GET JSON from HUMR and return `(status, parsed body)`.

        Same failure contract as `post_json`: callers only ever branch on the
        status code.
        """
        url = f"{self.control_plane_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.get(url=url, headers={"Authorization": f"Bearer {self._bearer}"})
        except Exception as exc:
            logger.error("control plane request failed path=%s: %s", path, exc)
            return 502, {"error": "control plane request failed"}
        return self._parsed(response=response, path=path)

    def _parsed(self, response: httpx.Response, path: str) -> tuple[int, dict]:
        """Parse a HUMR response body, turning an unparseable success into a 502."""
        try:
            return response.status_code, response.json()
        except Exception as exc:
            if 200 <= response.status_code < 300:
                logger.error("control plane request failed path=%s: %s", path, exc)
                return 502, {"error": "control plane request failed"}
            return response.status_code, {"error": f"control plane returned HTTP {response.status_code}"}
