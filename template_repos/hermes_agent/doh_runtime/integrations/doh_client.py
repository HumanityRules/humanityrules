"""Outbound JSON client for DOH's per-env integration endpoints.

One `DohClient` instance, built by the broker at startup, owns the
control-plane URL, the env bearer, and the owner/app identity that every
per-env integration endpoint requires. Everything in the broker that talks
to DOH (token refresh, device-flow completion, disconnect, vault setup
sessions) goes through it — the bearer never leaves this module.
"""

import json
import logging
import urllib.error
import urllib.request


logger = logging.getLogger("doh_client")


class DohClient:
    """Sync JSON client bound to one env bearer and one owner/app identity."""

    def __init__(self, control_plane_url: str, bearer: str, owner_username: str, app_slug: str) -> None:
        self.control_plane_url = control_plane_url.rstrip("/")
        self.owner_username = owner_username
        self.app_slug = app_slug
        self._bearer = bearer

    def post_json(self, path: str, payload: dict, timeout_seconds: int) -> tuple[int, dict]:
        """POST JSON to DOH and return `(status, parsed body)`.

        `owner_username` and `app_slug` are merged into every payload — all
        of DOH's per-env integration endpoints take them. Transport failures
        and unparseable bodies come back as a synthetic 502 so callers only
        ever branch on the status code.
        """
        url = f"{self.control_plane_url}{path}"
        data = json.dumps({
            "owner_username": self.owner_username,
            "app_slug": self.app_slug,
            **payload,
        }).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=data,
            method="POST",
            headers={"Authorization": f"Bearer {self._bearer}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                body = {"error": f"control plane returned HTTP {exc.code}"}
            return exc.code, body
        except Exception as exc:
            logger.error("control plane request failed path=%s: %s", path, exc)
            return 502, {"error": "control plane request failed"}
