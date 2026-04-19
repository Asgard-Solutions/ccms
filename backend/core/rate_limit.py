"""
Sliding-window IP rate limit, Redis-backed with in-process fallback.
"""
import logging
import time
from collections import defaultdict, deque

from core import metrics
from core.redis_client import get_redis, safe_call

logger = logging.getLogger("ccms.rate_limit")

_local_buckets: dict[str, deque] = defaultdict(deque)
_local_blocks = 0


def local_blocks() -> int:
    return _local_blocks


async def is_allowed(key: str, *, limit: int, window_seconds: int) -> bool:
    bucket = int(time.time() // window_seconds)
    redis_key = f"rl:{key}:{window_seconds}:{bucket}"

    client = get_redis()
    if client is not None:
        async def _bump():
            pipe = client.pipeline(transaction=False)
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            return await pipe.execute()

        result = await safe_call(_bump, default=None)
        if result is not None:
            count = int(result[0])
            allowed = count <= limit
            if not allowed:
                try:
                    metrics.rate_limit_blocks_total.labels(source="redis").inc()
                except Exception:
                    pass
            return allowed

    return _local_check(key, limit, window_seconds)


def _local_check(key: str, limit: int, window_seconds: int) -> bool:
    global _local_blocks
    now = time.time()
    cutoff = now - window_seconds
    q = _local_buckets[key]
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        _local_blocks += 1
        try:
            metrics.rate_limit_blocks_total.labels(source="local").inc()
        except Exception:
            pass
        return False
    q.append(now)
    return True
