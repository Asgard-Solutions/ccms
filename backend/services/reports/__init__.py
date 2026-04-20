"""Tenant-scoped reports — safe-by-default aggregations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from core.tenancy import TenantContext, tenant_db

ReportRunner = Callable[[TenantContext, dict], Awaitable[dict]]

_registry: dict[str, ReportRunner] = {}


class UnknownReportError(ValueError):
    pass


class UnauthorizedReportScopeError(PermissionError):
    pass


def report(name: str):
    def _wrap(fn: ReportRunner) -> ReportRunner:
        _registry[name] = fn
        return fn
    return _wrap


def registered_reports() -> list[str]:
    return sorted(_registry)


def _validate_location_scope(ctx: TenantContext, requested_locations: list[str] | None) -> list[str] | None:
    """Returns the sanitised list of location_ids the caller may use.

    - platform admin or tenant-wide: returns requested_locations as-is (may be None = all).
    - location-restricted user: must pass only locations they're assigned to.
    """
    if ctx.is_platform_admin or ctx.tenant_scope_all:
        return requested_locations
    allowed = set(ctx.allowed_location_ids)
    if requested_locations is None:
        return list(allowed) or []
    for loc in requested_locations:
        if loc not in allowed:
            raise UnauthorizedReportScopeError(
                f"location {loc} is not assigned to this user",
            )
    return list(requested_locations)


async def run_report(ctx: TenantContext, name: str, filters: dict | None = None) -> dict:
    """Entry point — every route should go through this."""
    ctx.assert_tenant_bound()
    runner = _registry.get(name)
    if not runner:
        raise UnknownReportError(f"report {name!r} is not registered; known: {registered_reports()}")
    f = dict(filters or {})
    f["location_ids"] = _validate_location_scope(ctx, f.get("location_ids"))
    return await runner(ctx, f)


# ---------------------------------------------------------------------------
# Built-in reports
# ---------------------------------------------------------------------------

def _tenant_match(ctx: TenantContext, base: dict | None = None) -> dict:
    m = dict(base or {})
    m["tenant_id"] = ctx.tenant_id
    return m


def _apply_location_filter(match: dict, location_ids: list[str] | None) -> dict:
    if location_ids:
        match = dict(match)
        match["location_id"] = {"$in": location_ids}
    return match


@report("appointments_by_day")
async def appointments_by_day(ctx: TenantContext, filters: dict) -> dict:
    """Count scheduled appointments per day for the last N days (default 30)."""
    days = int(filters.get("days", 30))
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat()

    match = _apply_location_filter(
        _tenant_match(ctx, {"start_time": {"$gte": start}}),
        filters.get("location_ids"),
    )
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": {"$substr": ["$start_time", 0, 10]},
            "count": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    db = tenant_db(ctx.tenant_id)
    rows = [r async for r in db.appointments.aggregate(pipeline)]
    return {
        "report": "appointments_by_day",
        "tenant_id": ctx.tenant_id,
        "location_ids": filters.get("location_ids"),
        "days": days,
        "buckets": [{"date": r["_id"], "count": r["count"]} for r in rows if r["_id"]],
        "total": sum(r["count"] for r in rows),
        "generated_at": now.isoformat(),
    }


@report("provider_productivity")
async def provider_productivity(ctx: TenantContext, filters: dict) -> dict:
    """Appointments per provider for the date range."""
    days = int(filters.get("days", 30))
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).isoformat()
    match = _apply_location_filter(
        _tenant_match(ctx, {"start_time": {"$gte": start}}),
        filters.get("location_ids"),
    )
    db = tenant_db(ctx.tenant_id)
    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$provider_id", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    rows = [r async for r in db.appointments.aggregate(pipeline)]
    # Hydrate provider names (tenant-scoped).
    ids = [r["_id"] for r in rows if r["_id"]]
    providers: dict[str, str] = {}
    if ids:
        cur = db.users.find(
            {"tenant_id": ctx.tenant_id, "id": {"$in": ids}},
            {"_id": 0, "id": 1, "name": 1},
        )
        providers = {u["id"]: u["name"] async for u in cur}
    return {
        "report": "provider_productivity",
        "tenant_id": ctx.tenant_id,
        "days": days,
        "location_ids": filters.get("location_ids"),
        "rows": [{"provider_id": r["_id"],
                  "provider_name": providers.get(r["_id"]),
                  "appointments": r["count"]} for r in rows],
        "generated_at": now.isoformat(),
    }


@report("location_performance")
async def location_performance(ctx: TenantContext, filters: dict) -> dict:
    """Patients + appointments per location (tenant-wide or subset)."""
    allowed = filters.get("location_ids")
    db = tenant_db(ctx.tenant_id)

    loc_q: dict = {"tenant_id": ctx.tenant_id, "status": "active"}
    if allowed:
        loc_q["id"] = {"$in": allowed}
    locations = [loc async for loc in db.locations.find(loc_q, {"_id": 0})]
    rows = []
    for loc in locations:
        patient_q = {"tenant_id": ctx.tenant_id, "location_id": loc["id"], "status": "active"}
        appt_q = {"tenant_id": ctx.tenant_id, "location_id": loc["id"]}
        patients = await db.patients.count_documents(patient_q)
        appts = await db.appointments.count_documents(appt_q)
        rows.append({
            "location_id": loc["id"],
            "location_name": loc["name"],
            "patients": patients,
            "appointments": appts,
        })
    return {
        "report": "location_performance",
        "tenant_id": ctx.tenant_id,
        "rows": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
