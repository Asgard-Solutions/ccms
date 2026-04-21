"""
Shared pytest fixtures for backend integration tests.

Problem:
  The running FastAPI backend is shared across test functions. Several
  tests (login rate limiting, change-password failure throttling, PIN
  verify lockouts) deliberately trip rate limiters. When the full suite
  runs, bursts of admin logins from many test files hit the
  `login:{ip}` ceiling (60 req / 60 s) and every subsequent test errors
  with 429 during fixture setup.

Fix:
  Before each test we POST `/api/_debug/rate-limit/reset` which wipes
  both the in-process deques in `core/rate_limit.py` and the Redis
  `rl:*` / `rlfail:*` namespace. The endpoint is only exposed when
  `APP_ENV != "production"` (see `core/debug_router.py`).

Graceful degradation:
  If the reset endpoint returns 404 or the backend isn't reachable, the
  fixture silently skips — the existing tests will then behave as
  before (individually still green) without masking the real problem.
"""
from __future__ import annotations

import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
_API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

_RESET_ENDPOINT = f"{_API}/_debug/rate-limit/reset"


def _reset_rate_limits() -> None:
    try:
        requests.post(_RESET_ENDPOINT, timeout=3)
    except requests.RequestException:
        # Never fail a test because the reset hook is unavailable.
        pass


@pytest.fixture(autouse=True)
def _clear_rate_limits_between_tests():
    """Reset rate-limit state *before* every test.

    We reset before (not after) so an early-exit in a previous test
    still leaves the limiter clean for the next one."""
    _reset_rate_limits()
    yield
