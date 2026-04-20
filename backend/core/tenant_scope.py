"""
Tenant-scoped query helpers.

Every repository/query method that touches tenant-owned data MUST run the
caller's filter through `scoped_filter()` so tenant_id (and location_id
when applicable) is always injected. Forgetting to do so is the #1 cause
of SaaS data leaks — this helper makes the safe path the easy path.

Usage
-----
    from core.tenancy import TenantContext
    from core.tenant_scope import scoped_filter

    async def list_patients(ctx: TenantContext, q: dict):
        q = scoped_filter(q, ctx)
        if q.get("__deny__"):
            return []                # caller can't see any location
        return await db.patients.find(q).to_list(None)

    # For a tenant-owned but location-agnostic collection (audit_logs,
    # invoices, etc.), pass `location_scoped=False` so only tenant_id is
    # injected and no location filter is applied.
"""
from __future__ import annotations

from core.tenancy import TenantContext


def scoped_filter(
    q: dict | None,
    ctx: TenantContext,
    *,
    location_scoped: bool = False,
) -> dict:
    """Returns a new Mongo filter that is guaranteed to be tenant-safe.

    - Platform admin with no override: returns input unchanged (all tenants).
    - Platform admin with an `X-Tenant-Id` override: filters to that tenant.
    - Regular user: adds `tenant_id = ctx.tenant_id`, and optionally a
      `location_id` restriction if `location_scoped` is True and the user
      is not tenant-wide.
    """
    out = dict(q or {})

    if ctx.is_platform_admin:
        if ctx.tenant_id:
            out["tenant_id"] = ctx.tenant_id
        return out

    if not ctx.tenant_id:
        # Non-platform user without tenant_id — should not be reachable,
        # but defend against it anyway.
        return {"__deny__": True}

    out["tenant_id"] = ctx.tenant_id

    if location_scoped and not ctx.tenant_scope_all:
        loc = ctx.location_filter()
        if loc.get("__deny__"):
            return {"__deny__": True}
        if loc:
            # Merge location filter; never overwrite a caller-supplied
            # location_id restriction — narrow further instead.
            if "location_id" in out and isinstance(out["location_id"], str):
                if out["location_id"] not in ctx.allowed_location_ids:
                    return {"__deny__": True}
            else:
                out.update(loc)
    return out


def stamp_for_write(doc: dict, ctx: TenantContext, *, location_id: str | None = None) -> dict:
    """Attaches tenant_id (+ optional location_id) to a document about to be inserted.

    Raises 400 at the route layer if we're missing tenant context; this
    function treats missing ctx.tenant_id as programmer error and raises
    ValueError to fail fast in tests.
    """
    if not ctx.tenant_id and not ctx.is_platform_admin:
        raise ValueError("stamp_for_write() called without tenant context")
    doc = dict(doc)
    # Platform admin writes still need a target tenant — if they don't
    # supply one via X-Tenant-Id the write is refused at route layer.
    if ctx.tenant_id:
        doc["tenant_id"] = ctx.tenant_id
    if location_id is not None:
        doc["location_id"] = location_id
    return doc
