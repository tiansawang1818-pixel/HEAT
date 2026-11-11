import threading
from contextlib import contextmanager
from typing import Iterator

from app.core.config import settings
from app.services.connectors.base import ConnectorError


_local_locks = {}
_guard = threading.Lock()

# The lease outlives a worker's hard deadline, including the shutdown margin.
MONITOR_SOFT_TIME_LIMIT_SECONDS = 50 * 60
MONITOR_HARD_TIME_LIMIT_SECONDS = 60 * 60
MONITOR_LOCK_LEASE_SECONDS = MONITOR_HARD_TIME_LIMIT_SECONDS + 5 * 60


class LockBackendUnavailable(ConnectorError):
    pass


@contextmanager
def distributed_lock(key: str, timeout_seconds: int = MONITOR_LOCK_LEASE_SECONDS) -> Iterator[bool]:
    """Fail closed in production; permit local locks only for development/tests."""
    client = None
    lock = None
    try:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3)
            lock = client.lock("heatshift:%s" % key, timeout=timeout_seconds, blocking_timeout=0)
            acquired = lock.acquire(blocking=False)
        except Exception as exc:
            if settings.environment.lower() == "production":
                raise LockBackendUnavailable("Distributed ingestion lock is unavailable; source check was not started") from exc
            with _guard:
                lock = _local_locks.setdefault(key, threading.Lock())
            acquired = lock.acquire(blocking=False)
        try:
            yield bool(acquired)
        finally:
            if acquired:
                try:
                    lock.release()
                except Exception:
                    pass
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
