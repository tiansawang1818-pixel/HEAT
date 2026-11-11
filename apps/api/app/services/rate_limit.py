import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Optional

import redis

from app.core.config import settings


logger = logging.getLogger("heatshift.rate_limit")


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    backend_available: bool = True


class DistributedRateLimiter:
    _SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None

    def _client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.Redis.from_url(
                settings.redis_url,
                # The first TLS handshake to managed Redis after a restart can take
                # over a second; later calls reuse the pooled connection.
                socket_connect_timeout=2,
                socket_timeout=1,
                decode_responses=True,
            )
        return self._redis

    def check(self, identifier: str, scope: str, limit: int, window_seconds: int, fail_closed: bool = False) -> RateLimitDecision:
        if not settings.rate_limit_enabled:
            return RateLimitDecision(True, limit, limit, 0)
        window_seconds = max(1, window_seconds)
        limit = max(1, limit)
        now = int(time.time())
        window = now // window_seconds
        digest = hashlib.sha256(identifier.encode("utf-8", errors="ignore")).hexdigest()[:24]
        key = "heatshift:rate-limit:%s:%s:%s" % (scope, window, digest)
        retry_after = window_seconds - (now % window_seconds)
        try:
            count = int(self._client().eval(self._SCRIPT, 1, key, window_seconds + 1))
        except redis.RedisError:
            # Requests fall back to the fail-open/closed decision below, so a brief
            # Redis stall is an expected degradation, not an application error.
            logger.warning("rate-limit backend unavailable", extra={"route": scope})
            return RateLimitDecision(not fail_closed, limit, 0, retry_after, backend_available=False)
        return RateLimitDecision(count <= limit, limit, max(0, limit - count), retry_after)


rate_limiter = DistributedRateLimiter()
