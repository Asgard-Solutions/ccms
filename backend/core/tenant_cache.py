"""
Tenant-aware cache — safe-by-default primitives.

Central rule: every key for tenant-owned data MUST be built through
`key_for(tenant_id, *parts)`. Ad-hoc string keys are forbidden (enforced
by the `TenantCache.get/set` API: they reject keys that don't start with
`t:`).

Key grammar
-----------
    t:<tenant_id>:<segment>[:<segment>]...

Conventions:
    t:{tid}:patient:{pid}
    t:{tid}:patients:list:{role}:{filters_hash}
    t:{tid}:loc:{loc_id}:schedule:{YYYY-MM-DD}
    t:{tid}:u:{uid}:permissions
    t:{tid}:report:{report}:{filters_hash}

Platform admins have their own namespace: `pa:<segment>…`. Never mix.

What MUST NOT be cached
-----------------------
- unmasked PHI fields (addresses, DOB, diagnosis, notes, treatment)
- any break-glass or audit-only data
- raw JWTs / reauth tokens / mfa tickets / password-reset tokens
- export download tokens

What CAN be cached (short TTL)
-----------------------------
- masked patient list (30s)
- providers list per tenant (300s)
- appointment list per tenant+location+filter (30s)
- effective permissions per user (120s) — invalidate on session_epoch bump
- report result set (300-3600s depending on report)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

from core import cache as _underlying_cache   # existing Redis-or-in-memory cache

logger = logging.getLogger("ccms.tenant_cache")

PLATFORM_NAMESPACE = "pa"
TENANT_NAMESPACE = "t"


def filters_hash(d: dict) -> str:
    """Stable short hash of a filter dict so cache keys stay bounded."""
    if not d:
        return "none"
    blob = json.dumps(d, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def key_for(tenant_id: str | None, *parts: str | int) -> str:
    """Build a tenant-namespaced cache key.

    Platform-admin-only data (no tenant) uses `pa:` namespace; callers must
    opt-in explicitly with `tenant_id=None`.
    """
    if not parts:
        raise ValueError("cache key requires at least one segment")
    segs = [str(p) for p in parts]
    ns = TENANT_NAMESPACE + ":" + str(tenant_id) if tenant_id else PLATFORM_NAMESPACE
    return ns + ":" + ":".join(segs)


def tenant_prefix(tenant_id: str) -> str:
    """Prefix used by `invalidate_tenant()` — matches every key for a tenant."""
    return f"{TENANT_NAMESPACE}:{tenant_id}:"


class UnsafeCacheKeyError(RuntimeError):
    """Raised when an operation tries to use a non-tenant-namespaced key."""


def _assert_safe(key: str) -> None:
    if not (key.startswith(TENANT_NAMESPACE + ":") or key.startswith(PLATFORM_NAMESPACE + ":")):
        raise UnsafeCacheKeyError(
            f"Refusing unsafe cache key {key!r}. Use `tenant_cache.key_for(ctx.tenant_id, ...)`.",
        )


class TenantCache:
    """Thin wrapper over `core.cache` that refuses unsafe keys."""

    @staticmethod
    async def get(key: str) -> Any | None:
        _assert_safe(key)
        return await _underlying_cache.get(key)

    @staticmethod
    async def set(key: str, value: Any, ttl_seconds: int) -> None:
        _assert_safe(key)
        if ttl_seconds <= 0 or ttl_seconds > 86400:
            raise ValueError("ttl_seconds must be in (0, 86400]; no infinite caches for tenant data.")
        await _underlying_cache.set_(key, value, ttl_seconds)

    @staticmethod
    async def get_or_set(
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        _assert_safe(key)
        cached = await TenantCache.get(key)
        if cached is not None:
            return cached
        value = await loader()
        await TenantCache.set(key, value, ttl_seconds)
        return value

    @staticmethod
    async def invalidate_tenant(tenant_id: str) -> int:
        """Wipe every cached entry for a tenant — used after role-epoch bumps,
        tenant-level config changes, or data-export revocations."""
        return await _underlying_cache.invalidate_prefix(tenant_prefix(tenant_id))

    @staticmethod
    async def invalidate(prefix: str) -> int:
        _assert_safe(prefix)
        return await _underlying_cache.invalidate_prefix(prefix)
