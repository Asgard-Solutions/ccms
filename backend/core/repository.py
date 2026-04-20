"""
Tenant-scoped repository base class.

Why this exists
---------------
In a multi-tenant SaaS the single most common data-leak root cause is a
developer calling `db.patients.find({"id": x})` without remembering to add
`tenant_id`. `scoped_filter()` solves half of that problem — it injects
tenant_id into a query — but it is still *opt-in*: a careless `find_one` with
no filter will happily return another tenant's row.

`TenantScopedRepository` closes that gap. It wraps a Motor collection and
forces a `TenantContext` on every call. A missing context raises
`MissingTenantContext` *before* we ever touch the database, so the failure
is loud and visible in dev and impossible to hide in prod.

Design goals
------------
1. **Fail closed.** Every method requires a `TenantContext`; if the caller
   is not platform_admin, tenant_id must be present. No silent defaults.
2. **No unsafe "get by id".** `find_one_by_id(id, ctx)` composes the id
   filter with the tenant filter. There is no method that accepts a raw
   filter without running it through `scoped_filter`.
3. **Cross-tenant attempt audit.** When `find_one_by_id` returns nothing
   we do one (cheap) probe on the unscoped collection to detect whether
   the id exists in a DIFFERENT tenant. If so we emit a
   `security.cross_tenant_attempt` audit row and still return 404 to the
   caller (no enumeration leak).
4. **Bulk ops are tenant-bounded.** `update_many`/`delete_many` apply the
   tenant filter automatically and refuse the "empty filter = all rows"
   footgun.
5. **Writes auto-stamp tenant_id.** `insert_one` stamps `tenant_id` (and
   optional `location_id`) before calling the driver. Callers cannot
   accidentally insert a row into the wrong tenant.

Background workers / async jobs
-------------------------------
Background jobs (the comm subscribers, retention sweeper, elevation-expiry
worker, etc.) don't have a `Request`. For those, use
`TenantContext.for_background(tenant_id, actor="system:retention-worker")`
to create a synthetic context that is explicitly non-platform-admin and
tenant-bound. The same repository API then applies.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase

from core.audit import log_audit
from core.tenancy import TenantContext, tenant_db
from core.tenant_scope import scoped_filter, stamp_for_write

logger = logging.getLogger("ccms.repo")


class MissingTenantContext(RuntimeError):
    """Raised when a repository call is made without a TenantContext.

    This is always a programmer error. Never swallow this exception —
    the correct response is a 500 to the client; the real fix belongs
    in the calling code."""


class UnsafeQueryError(RuntimeError):
    """Raised when a query shape would trivially leak cross-tenant data
    (e.g. an update_many with an empty filter)."""


def _require_ctx(ctx: TenantContext | None) -> TenantContext:
    if ctx is None:
        raise MissingTenantContext(
            "Repository call attempted without a TenantContext. "
            "Inject `ctx: TenantContext = Depends(get_tenant_context)` into your route "
            "or call `TenantContext.for_background(tenant_id=...)` from a worker."
        )
    ctx.assert_tenant_bound()
    return ctx


class TenantScopedRepository:
    """Base class for any tenant-owned collection.

    Subclasses should declare:
      * `collection_name` — the Mongo collection name
      * `location_scoped` — True if rows carry `location_id` and should be
        filtered by the caller's `allowed_location_ids`.

    The class is deliberately thin — no ORM fanciness, just safe accessors."""

    collection_name: str = ""
    location_scoped: bool = False

    def __init__(self, collection_name: str | None = None, *, location_scoped: bool | None = None):
        # Allow quick ad-hoc instantiation without subclassing.
        if collection_name:
            self.collection_name = collection_name
        if location_scoped is not None:
            self.location_scoped = location_scoped
        if not self.collection_name:
            raise ValueError("TenantScopedRepository requires a collection_name")

    # ------------------------------------------------------------------ db
    def collection(self, ctx: TenantContext) -> AsyncIOMotorCollection:
        """Returns the Motor collection bound to the tenant's database.

        Routes via `TenantDatabaseRouter` so a future dedicated-DB promotion
        is transparent."""
        db: AsyncIOMotorDatabase = tenant_db(ctx.tenant_id)
        return db[self.collection_name]

    # ----------------------------------------------------------------- reads
    async def find_one(self, q: dict, ctx: TenantContext, *, projection: dict | None = None) -> dict | None:
        ctx = _require_ctx(ctx)
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return None
        return await self.collection(ctx).find_one(scoped, projection or {"_id": 0})

    async def find_one_by_id(
        self, entity_id: str, ctx: TenantContext, *,
        projection: dict | None = None,
        audit_cross_tenant: bool = True,
    ) -> dict | None:
        """Safe `get by id` — always combines the id filter with tenant scope.

        If a row with that id exists in a different tenant, we emit a
        `security.cross_tenant_attempt` audit row and still return None so
        the caller returns 404 (no enumeration leak)."""
        ctx = _require_ctx(ctx)
        row = await self.find_one({"id": entity_id}, ctx, projection=projection)
        if row is None and audit_cross_tenant and not ctx.is_platform_admin and ctx.tenant_id:
            # One unscoped probe to detect the cross-tenant leak attempt.
            # Cheap: indexed lookup by id. Value: we can alert on serial
            # attacks enumerating ids from another tenant.
            other = await self.collection(ctx).find_one({"id": entity_id}, {"_id": 0, "tenant_id": 1})
            if other and other.get("tenant_id") and other["tenant_id"] != ctx.tenant_id:
                await log_audit(
                    action="security.cross_tenant_attempt",
                    actor_id=ctx.user.get("id") if ctx.user else None,
                    actor_email=(ctx.user or {}).get("email"),
                    actor_role=(ctx.user or {}).get("role"),
                    outcome="failure",
                    tenant_id=ctx.tenant_id,
                    entity_type=self.collection_name,
                    entity_id=entity_id,
                    reason="cross_tenant_id_lookup",
                    metadata={
                        "actor_tenant_id": ctx.tenant_id,
                        "target_tenant_id": other["tenant_id"],
                    },
                )
        return row

    async def find(
        self, q: dict, ctx: TenantContext, *,
        projection: dict | None = None,
        sort: list[tuple[str, int]] | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        ctx = _require_ctx(ctx)
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return []
        cur = self.collection(ctx).find(scoped, projection or {"_id": 0})
        if sort:
            cur = cur.sort(sort)
        if limit:
            cur = cur.limit(limit)
        return [r async for r in cur]

    async def count(self, q: dict, ctx: TenantContext) -> int:
        ctx = _require_ctx(ctx)
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return 0
        return await self.collection(ctx).count_documents(scoped)

    # ----------------------------------------------------------------- writes
    async def insert_one(self, doc: dict, ctx: TenantContext, *, location_id: str | None = None) -> dict:
        ctx = _require_ctx(ctx)
        stamped = stamp_for_write(doc, ctx, location_id=location_id)
        await self.collection(ctx).insert_one(stamped)
        return stamped

    async def update_one(self, q: dict, update: dict, ctx: TenantContext) -> int:
        ctx = _require_ctx(ctx)
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return 0
        result = await self.collection(ctx).update_one(scoped, update)
        return result.matched_count

    async def update_many(self, q: dict, update: dict, ctx: TenantContext) -> int:
        ctx = _require_ctx(ctx)
        if not q and not ctx.is_platform_admin:
            raise UnsafeQueryError(
                "update_many() called with empty filter. Provide a filter or "
                "use bulk_set_all_in_tenant() if you really want tenant-wide."
            )
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return 0
        result = await self.collection(ctx).update_many(scoped, update)
        return result.modified_count

    async def delete_one(self, q: dict, ctx: TenantContext) -> int:
        ctx = _require_ctx(ctx)
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return 0
        result = await self.collection(ctx).delete_one(scoped)
        return result.deleted_count

    async def delete_many(self, q: dict, ctx: TenantContext) -> int:
        ctx = _require_ctx(ctx)
        if not q and not ctx.is_platform_admin:
            raise UnsafeQueryError(
                "delete_many() called with empty filter — refusing to tenant-wide delete."
            )
        scoped = scoped_filter(q, ctx, location_scoped=self.location_scoped)
        if scoped.get("__deny__"):
            return 0
        result = await self.collection(ctx).delete_many(scoped)
        return result.deleted_count


# ---------------------------------------------------------------------------
# Concrete repositories for existing collections (new services should import
# these directly rather than create their own.)
# ---------------------------------------------------------------------------

class PatientRepository(TenantScopedRepository):
    collection_name = "patients"
    location_scoped = True


class AppointmentRepository(TenantScopedRepository):
    collection_name = "appointments"
    location_scoped = True


class MedicalRecordRepository(TenantScopedRepository):
    collection_name = "medical_records"
    location_scoped = True


class NotificationRepository(TenantScopedRepository):
    collection_name = "notifications"
    location_scoped = False


class AuditLogRepository(TenantScopedRepository):
    collection_name = "audit_logs"
    location_scoped = False
