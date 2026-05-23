"""Tiny in-memory PDP decision cache.

Keyed on (provider, sub). Each policy-proxy container serves exactly one app
(DOH_APP_ID is baked in at deploy time), and v1 has no route-level policy
overrides, so expanding the key with app_id or request path would just waste
memory. Add path-keying here when route overrides land; app_id will never be
needed as long as one-proxy-per-app holds.

Provider is part of the key because ``sub`` means different things across
providers — for "workos" it's a WorkOS user id, for "oidc" it's the OIDC
subject — and the PDP looks them up in different User columns.
"""

import time
from dataclasses import dataclass

from . import pdp as pdp_mod


@dataclass
class _Entry:
    decision: pdp_mod.PdpDecision
    expires_at: float


class PdpDecisionCache:
    """Fixed-TTL cache of PDP allow/deny decisions keyed on (provider, sub).

    Not thread-safe in the sense that two concurrent requests for the same
    cold key will both hit the PDP. That's fine: both get the same answer
    and both try to write it, last-writer-wins. No correctness issue.

    ttl_seconds=0 disables the cache (every lookup is a miss).
    """

    def __init__(self, ttl_seconds: int, now_fn=time.monotonic) -> None:
        self._ttl = max(0, int(ttl_seconds))
        self._now = now_fn
        self._entries: dict[tuple[str, str], _Entry] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def get(self, provider: str, sub: str) -> pdp_mod.PdpDecision | None:
        if not self.enabled:
            return None
        key = (provider, sub)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._now():
            # Lazy eviction keeps the hot path lock-free.
            self._entries.pop(key, None)
            return None
        return entry.decision

    def put(self, provider: str, sub: str, decision: pdp_mod.PdpDecision) -> None:
        if not self.enabled:
            return
        self._entries[(provider, sub)] = _Entry(
            decision=decision,
            expires_at=self._now() + self._ttl,
        )

    def clear(self) -> None:
        self._entries.clear()

    def size(self) -> int:
        return len(self._entries)
