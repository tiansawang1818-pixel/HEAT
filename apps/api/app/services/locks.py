import threading
from contextlib import contextmanager
from typing import Iterator

from app.core.config import settings


_local_locks = {}
_guard = threading.Lock()


@contextmanager
def distributed_lock(key: str, timeout_seconds: int = 900) -> Iterator[bool]:
    """Redis lock in deployments, process-local lock in dependency-light tests."""
    client = None
    lock = None
    try:
        try:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
            client.ping()
            lock = client.lock("heatshift:%s" % key, timeout=timeout_seconds, blocking_timeout=0)
        except Exception:
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
