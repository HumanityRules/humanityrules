"""Tests for the PDP decision cache."""

from policy_proxy.pdp import PdpDecision
from policy_proxy.pdp_cache import PdpDecisionCache


def _fake_clock(start: float = 100.0):
    """Return (now_fn, advance) — a controllable monotonic clock for tests."""
    t = [start]

    def now() -> float:
        return t[0]

    def advance(seconds: float) -> None:
        t[0] += seconds

    return now, advance


ALLOW = PdpDecision(decision="allow", reason="mock")
DENY = PdpDecision(decision="deny", reason="mock")


def test_disabled_when_ttl_zero() -> None:
    cache = PdpDecisionCache(ttl_seconds=0)
    assert not cache.enabled
    cache.put(provider="oidc", sub="sub1", decision=ALLOW)
    assert cache.size() == 0
    assert cache.get(provider="oidc", sub="sub1") is None


def test_hit_within_ttl() -> None:
    now, _ = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="sub1", decision=ALLOW)
    assert cache.get(provider="oidc", sub="sub1") == ALLOW


def test_miss_after_expiry() -> None:
    now, advance = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="sub1", decision=ALLOW)
    advance(61)
    assert cache.get(provider="oidc", sub="sub1") is None
    # Expired entry evicted lazily.
    assert cache.size() == 0


def test_different_users_do_not_share_entries() -> None:
    now, _ = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="alice", decision=ALLOW)
    cache.put(provider="oidc", sub="bob", decision=DENY)
    assert cache.get(provider="oidc", sub="alice") == ALLOW
    assert cache.get(provider="oidc", sub="bob") == DENY


def test_same_sub_different_providers_do_not_collide() -> None:
    """A workos user_id and an oidc subject may coincidentally match — keep them separate."""
    now, _ = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="user_01H", decision=ALLOW)
    cache.put(provider="workos", sub="user_01H", decision=DENY)
    assert cache.get(provider="oidc", sub="user_01H") == ALLOW
    assert cache.get(provider="workos", sub="user_01H") == DENY


def test_deny_decisions_are_cached() -> None:
    """Deny responses cache exactly like allows — no special-casing by decision value."""
    now, _ = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="sub1", decision=DENY)
    assert cache.get(provider="oidc", sub="sub1") == DENY


def test_put_refreshes_ttl() -> None:
    now, advance = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="sub1", decision=ALLOW)
    advance(30)
    cache.put(provider="oidc", sub="sub1", decision=ALLOW)  # extend window
    advance(40)  # 70s since first put, 40s since second; still alive
    assert cache.get(provider="oidc", sub="sub1") == ALLOW


def test_clear_drops_everything() -> None:
    now, _ = _fake_clock()
    cache = PdpDecisionCache(ttl_seconds=60, now_fn=now)
    cache.put(provider="oidc", sub="a", decision=ALLOW)
    cache.put(provider="oidc", sub="b", decision=ALLOW)
    assert cache.size() == 2
    cache.clear()
    assert cache.size() == 0
