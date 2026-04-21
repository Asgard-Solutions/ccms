"""
Non-production debug endpoints.

All routes mounted here return 404 when `APP_ENV=production`. They exist
purely to support deterministic integration tests by exposing a safe way
to reset process-local + Redis rate-limit state between test functions.

We intentionally do NOT gate the reset on a role/role-based guard:
  * Pytest's conftest runs before any user is authenticated.
  * The endpoint only manipulates rate-limit counters — it cannot read
    or mutate PHI, user records, or any domain state.
  * In production the router is removed entirely (not registered), so
    the code path is unreachable.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status

from core import rate_limit

router = APIRouter(prefix="/_debug", tags=["_debug"])


def _is_production() -> bool:
    return (os.environ.get("APP_ENV") or "dev").strip().lower() == "production"


@router.post("/rate-limit/reset")
async def reset_rate_limit_state():
    """Clear in-process + Redis rate-limit buckets.

    Used by `backend/tests/conftest.py` between tests so a previous
    test's rapid login bursts don't trip `login:{ip}` on the next one.
    Returns the number of buckets dropped (local + redis) for logging.
    """
    if _is_production():
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    local_cleared = rate_limit.reset_local_state()
    redis_cleared = await rate_limit.reset_redis_state()
    return {
        "ok": True,
        "local_buckets_cleared": local_cleared,
        "redis_keys_cleared": redis_cleared,
    }
