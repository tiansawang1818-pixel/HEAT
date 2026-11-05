import redis

from app.core.config import settings
from app.services.rate_limit import DistributedRateLimiter


class FakeRedis:
    def __init__(self):
        self.count = 0

    def eval(self, script, keys, key, ttl):
        assert keys == 1
        assert key.startswith("heatshift:rate-limit:forecast:")
        assert ttl == 61
        self.count += 1
        return self.count


class BrokenRedis:
    def eval(self, *args):
        raise redis.ConnectionError("offline")


def test_distributed_rate_limit_enforces_scope_without_storing_raw_identity(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    limiter = DistributedRateLimiter()
    limiter._redis = FakeRedis()

    first = limiter.check("203.0.113.5", "forecast", limit=2, window_seconds=60)
    second = limiter.check("203.0.113.5", "forecast", limit=2, window_seconds=60)
    third = limiter.check("203.0.113.5", "forecast", limit=2, window_seconds=60)
    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not third.allowed


def test_rate_limit_fails_closed_only_for_admin_when_redis_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    limiter = DistributedRateLimiter()
    limiter._redis = BrokenRedis()

    public = limiter.check("client", "forecast", 5, 60, fail_closed=False)
    admin = limiter.check("client", "admin", 5, 60, fail_closed=True)
    assert public.allowed and not public.backend_available
    assert not admin.allowed and not admin.backend_available
