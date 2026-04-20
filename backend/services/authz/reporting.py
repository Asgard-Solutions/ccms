"""
Compliance evidence reports (read-only). Mounted under /api/access/reports.

All reports require `audit_log.read` permission. Heavy queries are capped
with explicit limits.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query, Request

from core.audit import audit_success
from core.db import get_db_read
from services.authz.constants import PRIVILEGED_PERMISSIONS
from services.authz.policy import require_permission

router = APIRouter(prefix="/access/reports", tags=["access-reports"])


@router.get("/users-by-role")
async def users_by_role(
    request: Request,
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    out: dict[str, list[dict]] = {}
    async for r in db.roles.find({}, {"_id": 0, "key": 1, "name": 1}):
        out[r["key"]] = {"name": r["name"], "users": []}
    async for ur in db.user_roles.find({"status": "active"}, {"_id": 0}):
        u = await db.users.find_one(
            {"id": ur["user_id"]},
            {"_id": 0, "id": 1, "email": 1, "name": 1, "status": 1, "last_login_at": 1},
        )
        if u and ur["role_key"] in out:
            out[ur["role_key"]]["users"].append(u)
    return {"roles": out}


@router.get("/permissions-by-role")
async def permissions_by_role(
    request: Request,
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    grants = [g async for g in db.role_permissions.find({}, {"_id": 0})]
    by_role: dict[str, list[dict]] = {}
    for g in grants:
        by_role.setdefault(g["role_key"], []).append({
            "permission_key": g["permission_key"],
            "scope": g.get("scope"),
            "requires_mfa": g.get("requires_mfa", False),
            "requires_approval": g.get("requires_approval", False),
            "break_glass_allowed": g.get("break_glass_allowed", False),
        })
    return by_role


@router.get("/privileged-users")
async def privileged_users(
    request: Request,
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    # Users whose effective role-grants include any PRIVILEGED_PERMISSIONS.
    perms = list(PRIVILEGED_PERMISSIONS)
    priv_roles = {
        g["role_key"]
        async for g in db.role_permissions.find(
            {"permission_key": {"$in": perms}}, {"_id": 0, "role_key": 1},
        )
    }
    users: list[dict] = []
    async for ur in db.user_roles.find(
        {"status": "active", "role_key": {"$in": list(priv_roles)}},
        {"_id": 0},
    ):
        u = await db.users.find_one(
            {"id": ur["user_id"]},
            {"_id": 0, "id": 1, "email": 1, "name": 1, "status": 1, "last_login_at": 1},
        )
        if u:
            users.append({**u, "role_key": ur["role_key"]})
    await audit_success(
        admin, "access_report.privileged_users", request,
        metadata={"count": len(users)},
    )
    return {"users": users}


@router.get("/recent-role-changes")
async def recent_role_changes(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [
        r async for r in db.audit_logs.find(
            {
                "action": {"$in": ["authz.role_assigned", "authz.role_revoked",
                                    "user.updated"]},
                "created_at": {"$gte": since},
            },
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
    ]
    return {"since": since, "events": rows}


@router.get("/phi-access-history")
async def phi_access_history(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [
        r async for r in db.audit_logs.find(
            {"phi_accessed": True, "created_at": {"$gte": since}},
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
    ]
    return {"since": since, "count": len(rows), "events": rows}


@router.get("/export-history")
async def export_history(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [
        r async for r in db.audit_logs.find(
            {
                "action": {"$in": [
                    "patient.exported", "account.self_exported",
                    "audit_log.exported", "privacy_request.fulfill_export",
                ]},
                "created_at": {"$gte": since},
            },
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
    ]
    return {"since": since, "count": len(rows), "events": rows}


@router.get("/break-glass-history")
async def break_glass_history(
    request: Request,
    days: int = Query(90, ge=1, le=365),
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [
        r async for r in db.audit_logs.find(
            {
                "$or": [
                    {"metadata.emergency_access": True},
                    {"action": {"$regex": "^elevation\\."}},
                ],
                "created_at": {"$gte": since},
            },
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
    ]
    return {"since": since, "count": len(rows), "events": rows}


@router.get("/failed-authz")
async def failed_authz(
    request: Request,
    days: int = Query(7, ge=1, le=90),
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    db = get_db_read()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [
        r async for r in db.audit_logs.find(
            {
                "action": {"$in": [
                    "authz.denied", "authz.mfa_required", "authz.approval_required",
                ]},
                "created_at": {"$gte": since},
            },
            {"_id": 0},
        ).sort("created_at", -1).limit(500)
    ]
    return {"since": since, "count": len(rows), "events": rows}


@router.get("/access-review")
async def access_review_summary(
    request: Request,
    admin: dict = Depends(require_permission("audit_log", "read")),
):
    """Compact snapshot used by the Access Review dashboard."""
    db = get_db_read()
    now = datetime.now(timezone.utc)
    d7 = (now - timedelta(days=7)).isoformat()
    d30 = (now - timedelta(days=30)).isoformat()

    users_total = await db.users.count_documents({"status": {"$ne": "disabled"}})
    users_disabled = await db.users.count_documents({"status": "disabled"})
    user_roles_active = await db.user_roles.count_documents({"status": "active"})
    elevations_pending = await db.elevation_requests.count_documents({"status": "pending"})
    elevations_approved_30d = await db.elevation_requests.count_documents(
        {"status": {"$in": ["approved", "used"]}, "created_at": {"$gte": d30}}
    )
    phi_reads_7d = await db.audit_logs.count_documents(
        {"phi_accessed": True, "created_at": {"$gte": d7}},
    )
    denials_7d = await db.audit_logs.count_documents(
        {"action": "authz.denied", "created_at": {"$gte": d7}},
    )
    break_glass_30d = await db.audit_logs.count_documents(
        {"metadata.emergency_access": True, "created_at": {"$gte": d30}},
    )
    return {
        "as_of": now.isoformat(),
        "users": {"active": users_total, "disabled": users_disabled,
                  "role_assignments_active": user_roles_active},
        "elevations": {"pending": elevations_pending,
                       "approved_30d": elevations_approved_30d},
        "phi_reads_7d": phi_reads_7d,
        "authz_denials_7d": denials_7d,
        "break_glass_30d": break_glass_30d,
    }
