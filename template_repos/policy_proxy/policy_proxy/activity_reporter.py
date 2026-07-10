"""Nonblocking, coalesced policy-proxy activity reporting."""

import asyncio
import datetime
import logging
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

REPORT_TIMEOUT_SECONDS = 5.0


class PolicyProxyActivityReporter:
    """Report the newest authorized-traffic timestamp at a bounded cadence."""

    def __init__(
        self,
        *,
        http_client_provider: Callable[[], httpx.AsyncClient],
        endpoint_url: str,
        env_bearer_token: str,
        app_id: str,
        interval_seconds: float,
    ) -> None:
        self._http_client_provider = http_client_provider
        self._endpoint_url = endpoint_url
        self._env_bearer_token = env_bearer_token
        self._app_id = app_id
        self._interval_seconds = max(0.0, interval_seconds)
        self._latest_observed_at: datetime.datetime | None = None
        self._last_reported_at: datetime.datetime | None = None
        self._flush_task: asyncio.Task[None] | None = None
        self._closed = False

    def observe(self) -> None:
        """Record authorized traffic and ensure a background flush is running."""
        if self._closed:
            return
        observed_at = datetime.datetime.now(tz=datetime.UTC)
        if self._latest_observed_at is None or observed_at > self._latest_observed_at:
            self._latest_observed_at = observed_at
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_until_caught_up())

    async def close(self) -> None:
        """Stop the flush loop after a best-effort final report."""
        self._closed = True
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        if (
            self._latest_observed_at is not None
            and self._latest_observed_at != self._last_reported_at
        ):
            await self._report(observed_at=self._latest_observed_at)

    async def _flush_until_caught_up(self) -> None:
        try:
            while not self._closed:
                observed_at = self._latest_observed_at
                if observed_at is None:
                    return
                reported = await self._report(observed_at=observed_at)
                if reported:
                    self._last_reported_at = observed_at

                await asyncio.sleep(self._interval_seconds)
                if reported and self._latest_observed_at == observed_at:
                    return
        except asyncio.CancelledError:
            raise

    async def _report(self, observed_at: datetime.datetime) -> bool:
        try:
            response = await self._http_client_provider().post(
                url=self._endpoint_url,
                json={
                    "app_id": self._app_id,
                    "observed_at": observed_at.isoformat(),
                },
                headers={"Authorization": f"Bearer {self._env_bearer_token}"},
                timeout=REPORT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error("policy-proxy activity report failed: %s", exc.__class__.__name__)
            return False
        if not 200 <= response.status_code < 300:
            logger.error("policy-proxy activity report returned HTTP %d", response.status_code)
            return False
        return True
