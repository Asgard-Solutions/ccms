"""
/api/authz/* router — role / permission / elevation / assignment admin + reports.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_success, log_audit
from core.db import get_db_read, get_db_write
from core.deps import get_current_user, require_role
from core import metrics
from services.authz.constants import (
    BASELINE_ROLES,
    LEGACY_ROLE_TO_KEY,
    PERMISSIONS,
    PRIVILEGED_PERMISSIONS,
    ROLE_GRANTS,
    permission_key,
)
from services.authz.models import (
    ElevationApprove,
    ElevationOut,
    ElevationRequestCreate,
    LocationCreate,
    LocationOut,
    PatientAssignmentCreate,
    PermissionOverrideCreate,
    PermissionOverrideOut,
    RoleAssign,
    RoleUnassign,
    UserLocationAssign,
)
from services.authz.policy import (
    evaluate,
    effective_grants,
    require_permission,
)

router = APIRouter(prefix="/authz", tags=["authorization"])


# ---------------------------------------------------------------------------
# Self — current user's effective permissions (used by frontend PermissionsCtx)
# ---------------------------------------------------------------------------

@router.get("/me/permissions")
async def my_permissions(user: dict = Depends(get_current_user)):
    db = get_db_read()
    grants = await effective_grants(user)
    role_rows = [
        r async for r in db.user_roles.find(
            {"user_id": user["id"], "status": "active"}, {"_id": 0},
        )
    ]
    role_keys = [r["role_key"] for r in role_rows]
    if not role_keys:
        legacy = (user.get("role") or "").lower()
        mapped = LEGACY_ROLE_TO_KEY.get(legacy)
        if mapped:
            role_keys = [mapped]
    locs = [
        l async for l in db.user_location_assignments.find(
            {"user_id": user["id"], "status": "active"}, {"_id": 0},
        )
    ]
    return {
        "user_id": user["id"],
        "email": user["email"],
        "legacy_role": user.get("role"),
        "role_keys": role_keys,
        "location_ids": [l["location_id"] for l in locs],
        "permissions": [
            {
                "key": g.permission_key,
                "scope": g.scope,
                "requires_mfa": g.requires_mfa,
                "requires_approval": g.requires_approval,
                "break_glass_allowed": g.break_glass_allowed,
                "source_role": g.source_role,
                "via_elevation": g.via_elevation,
            }
            for g in grants
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/check")
async def check_permission(
    payload: dict,
    user: dict = Depends(get_current_user),
):
    """Quick decision probe (non-audited). Does not count as access."""
    resource = payload.get("resource") or ""
    action = payload.get("action") or ""
    ctx = payload.get("ctx") or {}
    d = await evaluate(user, resource, action, ctx)
    return {
        "allow": d.allow,
        "scope": d.scope,
        "requires_mfa": d.requires_mfa,
        "requires_approval": d.requires_approval,
        "break_glass_allowed": d.break_glass_allowed,
        "via_elevation": d.via_elevation,
        "reason": d.reason,
    }


# ---------------------------------------------------------------------------
# Roles + Permissions catalogue (read + limited admin)
# ---------------------------------------------------------------------------

@router.get("/roles")
async def list_roles(
    request: Request,
    _admin: dict = Depends(require_permission("role", "read")),
):
    db = get_db_read()
    rows = [r async for r in db.roles.find({}, {"_id": 0}).sort("name", 1)]
    # attach grants
    out = []
    for r in rows:
        grants = [
            g async for g in db.role_permissions.find(
                {"role_key": r["key"]}, {"_id": 0},
            )
        ]
        r["grants"] = grants
        out.append(r)
    return out


@router.get("/permissions")
async def list_permissions(
    request: Request,
    _admin: dict = Depends(require_permission("permission", "read")),
):
    db = get_db_read()
    rows = [
        p async for p in db.permissions.find({}, {"_id": 0}).sort(
            [("resource", 1), ("action", 1)],
        )
    ]
    # enrich with "privileged" flag for the UI
    for p in rows:
        p["privileged"] = p["key"] in PRIVILEGED_PERMISSIONS
    return rows


@router.get("/matrix")
async def permission_matrix(
    _admin: dict = Depends(require_permission("role", "read")),
):
    """Returns the full role×permission matrix for the admin UI."""
    db = get_db_read()
    roles = [r async for r in db.roles.find({}, {"_id": 0}).sort("name", 1)]
    perms = [
        p async for p in db.permissions.find({}, {"_id": 0}).sort(
            [("resource", 1), ("action", 1)],
        )
    ]
    grants = [
        g async for g in db.role_permissions.find({}, {"_id": 0})
    ]
    # grants_map[role_key][perm_key] = grant
    grants_map: dict[str, dict[str, dict]] = {}
    for g in grants:
        grants_map.setdefault(g["role_key"], {})[g["permission_key"]] = g
    return {
        "roles": roles,
        "permissions": [
            {**p, "privileged": p["key"] in PRIVILEGED_PERMISSIONS}
            for p in perms
        ],
        "grants_by_role": grants_map,
    }


# ---------------------------------------------------------------------------
# Role assignment to users
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/roles")
async def assign_role(
    user_id: str,
    payload: RoleAssign,
    request: Request,
    admin: dict = Depends(require_permission("role", "assign")),
):
    db = get_db_write()
    role = await db.roles.find_one({"key": payload.role_key}, {"_id": 0, "id": 1, "key": 1})
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "email": 1})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    now = datetime.now(timezone.utc).isoformat()
    existing = await db.user_roles.find_one(
        {"user_id": user_id, "role_key": payload.role_key, "status": "active"},
        {"_id": 0, "id": 1},
    )
    if existing:
        return {"message": "Already assigned"}
    await db.user_roles.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "role_key": payload.role_key,
        "status": "active",
        "assigned_at": now,
        "assigned_by_id": admin["id"],
        "assigned_by_email": admin["email"],
    })
    # Bump session epoch so the target's existing tokens reflect new perms.
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"session_epoch": 1}, "$set": {"updated_at": now}},
    )
    await audit_success(
        admin, "authz.role_assigned", request,
        entity_type="user", entity_id=user_id,
        metadata={"role_key": payload.role_key, "target_email": target["email"],
                  "sessions_revoked": True},
    )
    return {"message": "Role assigned", "sessions_revoked": True}


@router.delete("/users/{user_id}/roles/{role_key}")
async def unassign_role(
    user_id: str,
    role_key: str,
    request: Request,
    admin: dict = Depends(require_permission("role", "assign")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    result = await db.user_roles.update_many(
        {"user_id": user_id, "role_key": role_key, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by_id": admin["id"]}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"session_epoch": 1}, "$set": {"updated_at": now}},
    )
    await audit_success(
        admin, "authz.role_revoked", request,
        entity_type="user", entity_id=user_id,
        metadata={"role_key": role_key, "sessions_revoked": True},
    )
    return {"message": "Role revoked"}


# ---------------------------------------------------------------------------
# Locations + location assignment
# ---------------------------------------------------------------------------

@router.get("/locations")
async def list_locations(_user: dict = Depends(get_current_user)):
    db = get_db_read()
    return [r async for r in db.locations.find({}, {"_id": 0}).sort("name", 1)]


@router.post("/locations", status_code=201)
async def create_location(
    payload: LocationCreate,
    request: Request,
    admin: dict = Depends(require_permission("clinic_settings", "update")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "name": payload.name,
        "code": payload.code,
        "timezone": payload.timezone,
        "status": "active",
        "created_at": now,
    }
    await db.locations.insert_one(doc)
    await audit_success(
        admin, "location.created", request,
        entity_type="location", entity_id=doc["id"],
        metadata={"name": payload.name, "code": payload.code},
    )
    return doc


@router.post("/users/{user_id}/locations")
async def assign_location(
    user_id: str,
    payload: UserLocationAssign,
    request: Request,
    admin: dict = Depends(require_permission("user", "invite")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    existing = await db.user_location_assignments.find_one(
        {"user_id": user_id, "location_id": payload.location_id, "status": "active"},
        {"_id": 0, "id": 1},
    )
    if existing:
        return {"message": "Already assigned"}
    await db.user_location_assignments.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "location_id": payload.location_id,
        "status": "active",
        "assigned_at": now,
        "assigned_by_id": admin["id"],
    })
    await db.users.update_one(
        {"id": user_id}, {"$inc": {"session_epoch": 1}},
    )
    await audit_success(
        admin, "authz.location_assigned", request,
        entity_type="user", entity_id=user_id,
        metadata={"location_id": payload.location_id},
    )
    return {"message": "Location assigned"}


@router.delete("/users/{user_id}/locations/{location_id}")
async def unassign_location(
    user_id: str,
    location_id: str,
    request: Request,
    admin: dict = Depends(require_permission("user", "invite")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    result = await db.user_location_assignments.update_many(
        {"user_id": user_id, "location_id": location_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    await db.users.update_one(
        {"id": user_id}, {"$inc": {"session_epoch": 1}},
    )
    await audit_success(
        admin, "authz.location_unassigned", request,
        entity_type="user", entity_id=user_id,
        metadata={"location_id": location_id},
    )
    return {"message": "Location unassigned"}


# ---------------------------------------------------------------------------
# Patient assignment (provider ↔ patient)
# ---------------------------------------------------------------------------

@router.post("/patient-assignments", status_code=201)
async def assign_patient(
    payload: PatientAssignmentCreate,
    request: Request,
    admin: dict = Depends(require_permission("role", "assign")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    existing = await db.patient_assignments.find_one(
        {"provider_id": payload.provider_id, "patient_id": payload.patient_id,
         "status": "active"}, {"_id": 0, "id": 1},
    )
    if existing:
        return {"message": "Already assigned"}
    doc = {
        "id": str(uuid.uuid4()),
        "provider_id": payload.provider_id,
        "patient_id": payload.patient_id,
        "location_id": payload.location_id,
        "status": "active",
        "assigned_at": now,
        "assigned_by_id": admin["id"],
    }
    await db.patient_assignments.insert_one(doc)
    await audit_success(
        admin, "authz.patient_assigned", request,
        entity_type="patient", entity_id=payload.patient_id,
        metadata={"provider_id": payload.provider_id},
    )
    return doc


@router.delete("/patient-assignments/{assignment_id}")
async def unassign_patient(
    assignment_id: str,
    request: Request,
    admin: dict = Depends(require_permission("role", "assign")),
):
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()
    result = await db.patient_assignments.update_one(
        {"id": assignment_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    await audit_success(
        admin, "authz.patient_unassigned", request,
        entity_type="patient_assignment", entity_id=assignment_id,
    )
    return {"message": "Assignment revoked"}


# ---------------------------------------------------------------------------
# Elevation requests (time-bound, approval-gated elevated access)
# ---------------------------------------------------------------------------

@router.post("/elevation/request", status_code=201, response_model=ElevationOut)
async def create_elevation(
    payload: ElevationRequestCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    # Verify the requested permission exists
    perm = await db.permissions.find_one({"key": payload.permission_key}, {"_id": 0})
    if not perm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown permission")
    now = datetime.now(timezone.utc)
    doc = {
        "id": str(uuid.uuid4()),
        "requester_id": user["id"],
        "requester_email": user["email"],
        "requester_role": user.get("role"),
        "permission_key": payload.permission_key,
        "reason": payload.reason.strip(),
        "status": "pending",
        "ttl_minutes": payload.ttl_minutes,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "scope": "all_org",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=payload.ttl_minutes)).isoformat(),
        "used_at": None,
    }
    await db.elevation_requests.insert_one(doc)
    try:
        metrics.elevation_requests_total.labels(status="pending").inc()
    except Exception:
        pass
    await log_audit(
        action="elevation.requested",
        actor_id=user["id"], actor_email=user["email"], actor_role=user.get("role"),
        entity_type="elevation", entity_id=doc["id"],
        request=request,
        metadata={"permission_key": payload.permission_key,
                  "ttl_minutes": payload.ttl_minutes, "reason": doc["reason"]},
    )
    return doc


@router.get("/elevation")
async def list_elevations(
    status_filter: str | None = None,
    user: dict = Depends(get_current_user),
):
    db = get_db_read()
    q: dict = {}
    # Non-admins see only their own requests
    is_admin_reviewer = False
    if user.get("role") == "admin":
        is_admin_reviewer = True
    else:
        # Check new permission: if user has role.assign they can approve
        from services.authz.policy import evaluate as _eval
        d = await _eval(user, "role", "assign")
        is_admin_reviewer = d.allow
    if not is_admin_reviewer:
        q["requester_id"] = user["id"]
    if status_filter:
        q["status"] = status_filter
    rows = [
        r async for r in db.elevation_requests.find(q, {"_id": 0}).sort("created_at", -1).limit(200)
    ]
    return rows


@router.post("/elevation/{elevation_id}/decision")
async def decide_elevation(
    elevation_id: str,
    payload: ElevationApprove,
    request: Request,
    admin: dict = Depends(require_permission("role", "assign")),
):
    db = get_db_write()
    doc = await db.elevation_requests.find_one({"id": elevation_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if doc["status"] != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Request already {doc['status']}")
    if doc["requester_id"] == admin["id"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Separation of duties — approver cannot be the requester.",
        )
    new_status = "approved" if payload.decision == "approve" else "rejected"
    now = datetime.now(timezone.utc).isoformat()
    await db.elevation_requests.update_one(
        {"id": elevation_id},
        {"$set": {
            "status": new_status,
            "approved_by_id": admin["id"],
            "approved_by_email": admin["email"],
            "approval_reason": payload.reason,
            "decided_at": now,
        }},
    )
    try:
        metrics.elevation_requests_total.labels(status=new_status).inc()
    except Exception:
        pass
    await log_audit(
        action=f"elevation.{new_status}",
        actor_id=admin["id"], actor_email=admin["email"], actor_role=admin.get("role"),
        entity_type="elevation", entity_id=elevation_id,
        request=request,
        metadata={"target_user": doc["requester_email"],
                  "permission_key": doc["permission_key"]},
    )
    return {"message": f"Elevation {new_status}"}


@router.delete("/elevation/{elevation_id}")
async def cancel_elevation(
    elevation_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    doc = await db.elevation_requests.find_one({"id": elevation_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Request not found")
    if doc["requester_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your request")
    if doc["status"] not in ("pending", "approved"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel {doc['status']} request")
    now = datetime.now(timezone.utc).isoformat()
    await db.elevation_requests.update_one(
        {"id": elevation_id}, {"$set": {"status": "revoked", "revoked_at": now}},
    )
    await log_audit(
        action="elevation.revoked",
        actor_id=user["id"], actor_email=user["email"], actor_role=user.get("role"),
        entity_type="elevation", entity_id=elevation_id,
        request=request,
    )
    return {"message": "Elevation revoked"}


# ---------------------------------------------------------------------------
# Per-user permission overrides (exceptions)
#
# Use sparingly — every override weakens the auditability of "who has what".
# Ideal for temporary vendor access, clinician covering an out-of-scope
# patient panel, or an auditor needing a one-off view. Every override is
# fully audited and tied to a written reason; `expires_at` is strongly
# recommended (null = permanent; revoked_at is the kill switch).
# ---------------------------------------------------------------------------

@router.post(
    "/users/{user_id}/overrides",
    status_code=201,
    response_model=PermissionOverrideOut,
)
async def grant_override(
    user_id: str,
    payload: PermissionOverrideCreate,
    request: Request,
    admin: dict = Depends(require_permission("permission", "update")),
):
    db = get_db_write()
    perm = await db.permissions.find_one({"key": payload.permission_key}, {"_id": 0})
    if not perm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown permission")
    target = await db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "email": 1})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "target_email": target["email"],
        "permission_key": payload.permission_key,
        "scope": payload.scope,
        "requires_mfa": payload.requires_mfa,
        "requires_approval": payload.requires_approval,
        "break_glass_allowed": payload.break_glass_allowed,
        "reason": payload.reason.strip(),
        "status": "active",
        "granted_by_id": admin["id"],
        "granted_by_email": admin["email"],
        "created_at": now,
        "expires_at": payload.expires_at,
        "revoked_at": None,
    }
    await db.permission_scopes.insert_one(doc)
    # Bump session epoch so old tokens pick up the new grant.
    await db.users.update_one(
        {"id": user_id}, {"$inc": {"session_epoch": 1}, "$set": {"updated_at": now}},
    )
    await log_audit(
        action="authz.override_granted",
        actor_id=admin["id"], actor_email=admin["email"], actor_role=admin.get("role"),
        entity_type="user", entity_id=user_id,
        request=request,
        metadata={
            "permission_key": payload.permission_key,
            "scope": payload.scope,
            "expires_at": payload.expires_at,
            "reason": doc["reason"],
            "target_email": target["email"],
            "sessions_revoked": True,
        },
    )
    return doc


@router.get("/users/{user_id}/overrides")
async def list_overrides(
    user_id: str,
    include_revoked: bool = False,
    admin: dict = Depends(require_permission("permission", "read")),
):
    db = get_db_read()
    q: dict = {"user_id": user_id}
    if not include_revoked:
        q["status"] = "active"
    rows = [
        r async for r in db.permission_scopes.find(q, {"_id": 0}).sort(
            "created_at", -1,
        )
    ]
    return rows


@router.delete("/users/{user_id}/overrides/{override_id}")
async def revoke_override(
    user_id: str,
    override_id: str,
    request: Request,
    admin: dict = Depends(require_permission("permission", "update")),
):
    db = get_db_write()
    doc = await db.permission_scopes.find_one(
        {"id": override_id, "user_id": user_id}, {"_id": 0},
    )
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Override not found")
    if doc["status"] != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Already revoked")
    now = datetime.now(timezone.utc).isoformat()
    await db.permission_scopes.update_one(
        {"id": override_id},
        {"$set": {"status": "revoked", "revoked_at": now}},
    )
    await db.users.update_one(
        {"id": user_id}, {"$inc": {"session_epoch": 1}},
    )
    await log_audit(
        action="authz.override_revoked",
        actor_id=admin["id"], actor_email=admin["email"], actor_role=admin.get("role"),
        entity_type="user", entity_id=user_id,
        request=request,
        metadata={"permission_key": doc["permission_key"],
                  "override_id": override_id, "sessions_revoked": True},
    )
    return {"message": "Override revoked"}
