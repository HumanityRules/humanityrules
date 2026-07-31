"""Outbound JSON client for HUMR's per-env integration endpoints.

One `HumrClient` instance, built by the broker at startup, owns the
control-plane URL, the env bearer, and the owner/app identity that every
per-env integration endpoint requires. Everything in the broker that talks
to HUMR (token refresh, device-flow completion, disconnect, vault setup
sessions, usage reporting, entitlement refresh) goes through it — the bearer
never leaves this module.
"""

import logging

import httpx


logger = logging.getLogger("humr_client")


class HumrClient:
    """Async JSON client bound to one env bearer and one owner/app identity."""

    def __init__(self, control_plane_url: str, bearer: str, owner_username: str, app_slug: str) -> None:
        self.control_plane_url = control_plane_url.rstrip("/")
        self.owner_username = owner_username
        self.app_slug = app_slug
        self._bearer = bearer

    async def post_json(self, path: str, payload: dict, timeout_seconds: int) -> tuple[int, dict]:
        """POST JSON to HUMR and return `(status, parsed body)`.

        `owner_username` and `app_slug` are merged into every payload — all
        of HUMR's per-env integration endpoints take them. Transport failures
        and unparseable success bodies come back as a synthetic 502, and an
        unparseable error body keeps its real status with a fallback body,
        so callers only ever branch on the status code.
        """
        url = f"{self.control_plane_url}{path}"
        body = {
            "owner_username": self.owner_username,
            "app_slug": self.app_slug,
            **payload,
        }
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url=url, json=body, headers={"Authorization": f"Bearer {self._bearer}"})
        except Exception as exc:
            logger.error("control plane request failed path=%s: %s", path, exc)
            return 502, {"error": "control plane request failed"}
        return self._parsed(response=response, path=path)

    async def get_json(self, path: str, timeout_seconds: int) -> tuple[int, dict]:
        """GET JSON from HUMR and return `(status, parsed body)`.

        Same failure contract as `post_json`: callers only ever branch on the
        status code. Nothing is merged into the request — a GET has no body to
        carry owner/app identity, and the endpoints reached this way resolve
        everything they need from the env bearer.
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
