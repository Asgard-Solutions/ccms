"""
Async Redis client with graceful fallback.

If Redis is unreachable, every helper degrades to a no-op (cache miss / allow
rate limit) so requests never fail. We log the unavailability once and re-probe
on demand. This is critical for HIPAA — the application must remain available
even if a non-source-of-truth dependency is degraded.

In production you should replace the local Redis with a managed Redis (with a
BAA if used for any PHI surface) and remove the silent-fallback logging.
"""
import logging
import os

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger("ccms.redis")

_client: aioredis.Redis | None = None
_unavailable_logged = False


def get_redis() -> aioredis.Redis | None:
    """Returns the singleton client, or None if Redis is not configured."""
    global _client
    if _client is not None:
        return _client
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    _client = aioredis.from_url(
        url, decode_responses=True, socket_connect_timeout=0.25, socket_timeout=0.5
    )
    return _client


async def safe_call(coro_factory, default=None):
    """Run an async Redis call; return `default` if Redis is unavailable.

    `coro_factory` is a zero-arg callable that returns the awaitable to run.
    We construct the coroutine *inside* the try/except so a connection failure
    never leaks an un-awaited coroutine warning.
    """
    global _unavailable_logged
    client = get_redis()
    if client is None:
        return default
    try:
        return await coro_factory()
    except RedisError as exc:
        if not _unavailable_logged:
            logger.warning("Redis unavailable, falling back to in-process behaviour: %s", exc)
            _unavailable_logged = True
        return default


async def ping() -> bool:
    return bool(await safe_call(lambda: get_redis().ping(), default=False))


async def close() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        _client = None
