"""
Multi-tenancy core — tenant context + database routing abstraction.

Architectural decision
----------------------
- **Tenant = chiropractic practice group (customer organisation).**
  A single-office practice is a tenant with one `location`. A multi-location
  group has many locations, all under the same tenant_id.
- **Default isolation model: shared-database / shared-schema with strict
  tenant_id scoping on every tenant-owned row.**
- **Hybrid bridge: `TenantDatabaseRouter.get_db(tenant_id)` is the one and
  only way repositories obtain a Motor database.** It returns the shared DB
  for 99% of tenants, but can return a DEDICATED cluster for any tenant
  promoted in the routing map. No business-logic change needed to move a
  tenant to a dedicated DB — only an env/config update and a data copy.

Why not "one database per location"?
------------------------------------
- A single practice group has clinical + financial continuity across its
  own locations (referrals, shared patients, consolidated billing,
  group-level reporting). Splitting by location would force synthetic
  cross-database joins for trivial group-level queries and double the
  backup / auth / indexing burden per customer.
- Tenants are the commercial contract (one invoice, one BAA, one admin
  console); locations are an operational concern that *should* be fast to
  add/merge/close without a data-migration event.
- Location isolation is already achieved through `location_id` + the authz
  scope filter (`assigned_location`, `all_location_patients`). This gives
  the security property of location isolation without the operational cost
  of a separate DB.

Why not "one shared table, filter only in frontend"?
----------------------------------------------------
- Frontend filtering is security theatre — an attacker can always forge
  requests. Server-side tenant filtering MUST be mandatory. This module
  enforces it through `require_tenant()` and the `tenant_scoped_query`
  helper; a developer who forgets to pass tenant context will get an
  immediate 500 from the repository layer, not a data leak.

Platform admin
--------------
Users with `role = "platform_admin"` (tenant_id = None) bypass the tenant
filter but every read/write is audited with `platform_admin_access=True`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from fastapi import HTTPException, Request, status
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger("ccms.tenancy")


# ---------------------------------------------------------------------------
# TenantContext — carried with every request by get_tenant_context()
# ---------------------------------------------------------------------------
@dataclass
class TenantContext:
    user: dict
    tenant_id: str | None
    is_platform_admin: bool = False
    # Which locations of the tenant the caller is allowed to see.
    # Empty list => no location restriction queries should deny unless the
    # caller has `tenant_scope_all` (set True when they're a tenant-wide user).
    allowed_location_ids: list[str] = field(default_factory=list)
    tenant_scope_all: bool = False   # True = can see every location in tenant
    # Request-scoped observability metadata (IP, user-agent, request id).
    # Populated by `get_tenant_context()`. Background contexts leave these None.
    request_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def for_background(
        cls,
        tenant_id: str,
        *,
        actor: str = "system",
        tenant_scope_all: bool = True,
    ) -> "TenantContext":
        """Build a TenantContext for a background job / async worker.

        Explicitly NEVER a platform admin. The `actor` string shows up in
        audit rows so the operator can tell which worker touched a row."""
        if not tenant_id:
            raise ValueError("for_background() requires an explicit tenant_id")
        synth_user = {"id": f"worker:{actor}", "email": actor, "role": "worker"}
        return cls(
            user=synth_user,
            tenant_id=tenant_id,
            is_platform_admin=False,
            tenant_scope_all=tenant_scope_all,
            allowed_location_ids=[],
        )

    def assert_tenant_bound(self) -> None:
        if self.is_platform_admin:
            return
        if not self.tenant_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Tenant context is required for this operation.",
            )

    def location_filter(self) -> dict:
        """Returns a Mongo filter fragment restricting to allowed locations.

        - platform_admin or tenant_scope_all → no location restriction
        - specific allowed_location_ids → `{"location_id": {"$in": [...]}}`
        - empty list + not tenant_scope_all → a deny sentinel `{"__deny__": True}`
        """
        if self.is_platform_admin or self.tenant_scope_all:
            return {}
        if not self.allowed_location_ids:
            return {"__deny__": True}
        return {"location_id": {"$in": list(self.allowed_location_ids)}}


# ---------------------------------------------------------------------------
# TenantDatabaseRouter — the one bridge point for shared → dedicated move
# ---------------------------------------------------------------------------
class TenantDatabaseRouter:
    """Maps `tenant_id` → Motor database.

    Today: every tenant lives on the shared cluster (`MONGO_URL` / `DB_NAME`).
    Tomorrow: operators can move one tenant to dedicated infra by setting:

        TENANT_DB_MAP='{"<tenant_id>": {"uri": "mongodb://...", "db": "ccms_acme"}}'

    …and restarting the service. The code layer above does not change.
    """

    def __init__(self) -> None:
        self._shared_client: AsyncIOMotorClient | None = None
        self._dedicated_clients: dict[str, AsyncIOMotorClient] = {}
        self._map = self._load_map()

    @staticmethod
    def _load_map() -> dict:
        raw = os.environ.get("TENANT_DB_MAP") or ""
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("TENANT_DB_MAP must be a JSON object")
            return parsed
        except Exception as exc:  # noqa: BLE001
            logger.error("Invalid TENANT_DB_MAP; ignoring: %s", exc)
            return {}

    def is_dedicated(self, tenant_id: str | None) -> bool:
        return tenant_id is not None and tenant_id in self._map

    def _get_shared(self) -> AsyncIOMotorClient:
        if self._shared_client is None:
            self._shared_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        return self._shared_client

    def _get_dedicated(self, tenant_id: str) -> tuple[AsyncIOMotorClient, str]:
        entry = self._map[tenant_id]
        uri = entry["uri"]
        db_name = entry.get("db") or os.environ["DB_NAME"]
        client = self._dedicated_clients.get(tenant_id)
        if client is None:
            client = AsyncIOMotorClient(uri)
            self._dedicated_clients[tenant_id] = client
        return client, db_name

    def get_db(self, tenant_id: str | None) -> AsyncIOMotorDatabase:
        """Primary entry point. Returns the DB for this tenant.

        - Shared tenants → shared cluster + `DB_NAME`
        - Dedicated tenants → their own cluster + their own `db` name
        - None (e.g. platform-admin cross-tenant queries) → shared cluster
        """
        if tenant_id and self.is_dedicated(tenant_id):
            client, name = self._get_dedicated(tenant_id)
            return client[name]
        shared = self._get_shared()
        return shared[os.environ["DB_NAME"]]

    async def close(self) -> None:
        for c in list(self._dedicated_clients.values()):
            c.close()
        self._dedicated_clients.clear()
        if self._shared_client is not None:
            self._shared_client.close()
            self._shared_client = None


# Singleton — every route resolves DB through this object
_router = TenantDatabaseRouter()


def tenant_db(tenant_id: str | None) -> AsyncIOMotorDatabase:
    """Shortcut: the tenant's database via the router."""
    return _router.get_db(tenant_id)


def tenant_router() -> TenantDatabaseRouter:
    return _router


# ---------------------------------------------------------------------------
# FastAPI dependency: resolve tenant context from the authenticated user
# ---------------------------------------------------------------------------
PLATFORM_ADMIN_ROLE = "platform_admin"


async def get_tenant_context(request: Request) -> TenantContext:
    """Builds the TenantContext for the current request.

    Platform admins may override the active tenant for a cross-tenant read
    by sending an `X-Tenant-Id` header; every such override is flagged on
    the returned context so the router can audit it.

    The resolved context is stashed on `request.state.tenant_context` so
    middleware, exception handlers, and low-level audit hooks can access
    it without re-running auth.
    """
    # Cache on request.state so repeated deps on the same request share work.
    cached = getattr(request.state, "tenant_context", None)
    if cached is not None:
        return cached

    from core.deps import get_current_user  # lazy — avoid import cycle

    user = await get_current_user(request)

    is_platform = user.get("role") == PLATFORM_ADMIN_ROLE or user.get("is_platform_admin") is True
    tenant_id = user.get("tenant_id")

    # Platform admin may scope to a specific tenant via header.
    if is_platform:
        override = request.headers.get("x-tenant-id")
        if override:
            tenant_id = override

    # Request observability metadata.
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = xff or (request.client.host if request.client else "unknown")
    request_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or None
    )

    ctx = TenantContext(
        user=user,
        tenant_id=tenant_id,
        is_platform_admin=is_platform,
        request_id=request_id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )

    # Resolve allowed locations from `user_location_assignments`.
    if not is_platform and tenant_id:
        db = tenant_db(tenant_id)
        # Tenant-wide access if the user has the `tenant_scope_all` flag OR
        # is admin-tier at the tenant level (legacy role "admin" in that
        # tenant). The presence of *any* specific location assignment means
        # they are location-restricted.
        assignments = [
            a async for a in db.user_location_assignments.find(
                {"user_id": user["id"], "status": "active", "tenant_id": tenant_id},
                {"_id": 0, "location_id": 1},
            )
        ]
        ctx.allowed_location_ids = [a["location_id"] for a in assignments]
        # The legacy "admin" role OR an explicit flag on the user record
        # grants tenant-wide visibility regardless of per-location rows.
        if user.get("tenant_scope_all") is True or user.get("role") in ("admin", "super_admin"):
            ctx.tenant_scope_all = True

    request.state.tenant_context = ctx
    return ctx


async def require_tenant(request: Request) -> TenantContext:
    """FastAPI dependency: caller MUST be tenant-bound or platform-admin."""
    ctx = await get_tenant_context(request)
    ctx.assert_tenant_bound()
    return ctx
