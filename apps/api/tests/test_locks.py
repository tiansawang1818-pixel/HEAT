from unittest.mock import MagicMock

import pytest
import redis

from app.core.config import settings
from app.services.locks import (
    LockBackendUnavailable,
    MONITOR_HARD_TIME_LIMIT_SECONDS,
    MONITOR_LOCK_LEASE_SECONDS,
    distributed_lock,
)


def test_production_fails_closed_when_distributed_lock_is_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(redis.Redis, "from_url", MagicMock(side_effect=redis.ConnectionError("Redis is down")))
    with pytest.raises(LockBackendUnavailable):
        with distributed_lock("test-source"):
            pytest.fail("A production job must not start without its shared lock")


def test_lock_contention_does_not_fall_back_to_a_local_lock(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    client = MagicMock()
    client.lock.return_value.acquire.return_value = False
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: client)
    with distributed_lock("test-contention") as acquired:
        assert acquired is False
    client.lock.assert_called_once_with("heatshift:test-contention", timeout=MONITOR_LOCK_LEASE_SECONDS, blocking_timeout=0)
    client.lock.return_value.release.assert_not_called()
    client.close.assert_called_once()
    assert MONITOR_LOCK_LEASE_SECONDS > MONITOR_HARD_TIME_LIMIT_SECONDS


def test_development_fallback_still_excludes_duplicate_local_execution(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(redis.Redis, "from_url", MagicMock(side_effect=redis.ConnectionError("Redis is down")))
    with distributed_lock("local-only-test") as first:
        with distributed_lock("local-only-test") as second:
            assert first is True
            assert second is False
    with distributed_lock("local-only-test") as next_run:
        assert next_run is True
