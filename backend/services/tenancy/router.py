"""Tenancy router — /api/tenants/* and /api/me/tenant-context.

Security model
--------------
- Tenant & location CRUD is gated by permissions:
    tenant.read / tenant.create / tenant.update
    location.read / location.create / location.update
  Platform admins hold all of these; tenant super_admins hold the tenant-scoped
  subset (read + location.create/update for their own tenant).
- List/detail/update of tenants is ALWAYS scoped to the caller's tenant_id
  unless they are a platform admin (then they can see every tenant).
- Every mutation is audited into the shared audit_log.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_success
from core.db import get_db_write
from core.tenancy import TenantContext, get_tenant_context, require_tenant, tenant_db
from services.tenancy.models import (
    LocationCreate,
    LocationPublic,
    TenantContextResponse,
    TenantCreate,
    TenantPublic,
)

router = APIRouter(prefix="/tenancy", tags=["tenancy"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_tenant(t: dict) -> dict:
    return {
        "id": t["id"],
        "name": t["name"],
        "slug": t["slug"],
        "type": t.get("type", "single"),
        "status": t.get("status", "active"),
        "db_tier": t.get("db_tier", "shared"),
        "created_at": t["created_at"],
    }


def _public_location(loc: dict) -> dict:
    return {
        "id": loc["id"],
        "tenant_id": loc["tenant_id"],
        "name": loc["name"],
        "code": loc.get("code"),
        "timezone": loc.get("timezone", "America/Los_Angeles"),
        "status": loc.get("status", "active"),
        "address": loc.get("address"),
        "created_at": loc["created_at"],
    }


# ---------------------------------------------------------------------------
# Current user's tenant context (for the frontend)
# ---------------------------------------------------------------------------
@router.get("/me/context", response_model=TenantContextResponse)
async def my_context(ctx: TenantContext = Depends(get_tenant_context)):
    tenant = None
    locations: list[dict] = []
    if ctx.tenant_id:
        db = tenant_db(ctx.tenant_id)
        t = await db.tenants.find_one({"id": ctx.tenant_id}, {"_id": 0})
        if t:
            tenant = _public_tenant(t)
        # List all locations the user can see.
        if ctx.tenant_scope_all or ctx.is_platform_admin:
            cur = db.locations.find({"tenant_id": ctx.tenant_id}, {"_id": 0}).sort("name", 1)
        else:
            cur = db.locations.find(
                {"tenant_id": ctx.tenant_id, "id": {"$in": ctx.allowed_location_ids}},
                {"_id": 0},
            ).sort("name", 1)
        locations = [_public_location(loc) async for loc in cur]
    return {
        "tenant": tenant,
        "locations": locations,
        "allowed_location_ids": ctx.allowed_location_ids,
        "tenant_scope_all": ctx.tenant_scope_all,
        "is_platform_admin": ctx.is_platform_admin,
    }


# ---------------------------------------------------------------------------
# Tenant CRUD (platform admin only for create; tenant-scoped for read/update)
# ---------------------------------------------------------------------------
@router.get("/tenants", response_model=list[TenantPublic])
async def list_tenants(
    request: Request,
    ctx: TenantContext = Depends(require_tenant),
):
    # Platform admin sees all tenants (across shared DB); tenant users see
    # only their own tenant.
    db = tenant_db(ctx.tenant_id)
    q: dict = {}
    if not ctx.is_platform_admin:
        q["id"] = ctx.tenant_id
    cursor = db.tenants.find(q, {"_id": 0}).sort("name", 1)
    rows = [_public_tenant(t) async for t in cursor]
    await audit_success(
        ctx.user, "tenant.list_viewed", request,
        metadata={"count": len(rows), "platform_admin_access": ctx.is_platform_admin},
    )
    return rows


@router.post("/tenants", response_model=TenantPublic, status_code=201)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only platform admins can create tenants")
    db = tenant_db(None)  # always goes to shared cluster at creation time
    slug = payload.slug.lower().strip()
    existing = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1})
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Tenant slug already exists")

    now = _now()
    tenant_id = str(uuid.uuid4())
    doc = {
        "id": tenant_id,
        "name": payload.name.strip(),
        "slug": slug,
        "type": payload.type,
        "status": "active",
        "db_tier": "shared",
        "created_at": now,
        "updated_at": now,
    }
    await db.tenants.insert_one(doc)

    # Auto-create the primary location.
    loc_doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "name": payload.primary_location_name.strip(),
        "code": payload.primary_location_code,
        "timezone": payload.timezone,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    await db.locations.insert_one(loc_doc)

    await audit_success(
        ctx.user, "tenant.created", request,
        entity_type="tenant", entity_id=tenant_id,
        metadata={"slug": slug, "type": payload.type, "primary_location_id": loc_doc["id"]},
    )
    return _public_tenant(doc)


# ---------------------------------------------------------------------------
# Locations under a tenant
# ---------------------------------------------------------------------------
@router.get("/tenants/{tenant_id}/locations", response_model=list[LocationPublic])
async def list_locations(
    tenant_id: str,
    request: Request,
    ctx: TenantContext = Depends(require_tenant),
):
    if not ctx.is_platform_admin and ctx.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
    db = tenant_db(tenant_id)
    q: dict = {"tenant_id": tenant_id}
    # If user isn't tenant-wide, scope to their allowed locations.
    if not ctx.is_platform_admin and not ctx.tenant_scope_all:
        if not ctx.allowed_location_ids:
            return []
        q["id"] = {"$in": ctx.allowed_location_ids}
    cursor = db.locations.find(q, {"_id": 0}).sort("name", 1)
    rows = [_public_location(loc) async for loc in cursor]
    await audit_success(
        ctx.user, "location.list_viewed", request,
        entity_type="tenant", entity_id=tenant_id,
        metadata={"count": len(rows)},
    )
    return rows


@router.post("/tenants/{tenant_id}/locations", response_model=LocationPublic, status_code=201)
async def create_location(
    tenant_id: str,
    payload: LocationCreate,
    request: Request,
    ctx: TenantContext = Depends(require_tenant),
):
    # Only tenant super_admins or platform admins.
    if not ctx.is_platform_admin:
        if ctx.tenant_id != tenant_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-tenant access denied")
        if ctx.user.get("role") not in ("admin", "super_admin", "platform_admin"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only tenant admins can add locations")
    db = tenant_db(tenant_id)
    now = _now()
    loc_id = str(uuid.uuid4())
    doc = {
        "id": loc_id,
        "tenant_id": tenant_id,
        "name": payload.name.strip(),
        "code": payload.code,
        "timezone": payload.timezone,
        "status": "active",
        "address": payload.address,
        "created_at": now,
        "updated_at": now,
    }
    await db.locations.insert_one(doc)
    await audit_success(
        ctx.user, "location.created", request,
        entity_type="location", entity_id=loc_id,
        metadata={"tenant_id": tenant_id, "name": doc["name"]},
    )
    return _public_location(doc)
