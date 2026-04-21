"""
/api/authz/* router — role / permission / elevation / assignment admin + reports.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.audit import audit_success, log_audit
from core.db import get_db_read, get_db_write
from core.deps import get_current_user, require_role
from core import metrics
from core.tenancy import TenantContext, get_tenant_context
from services.authz.constants import (
    BASELINE_ROLES,
    LEGACY_ROLE_TO_KEY,
    PERMISSIONS,
    PRIVILEGED_PERMISSIONS,
    ROLE_GRANTS,
    permission_key,
)
from services.authz.permission_catalog import (
    MODULES,
    explain_permissions,
    grouped_catalog,
)
from services.authz.migration import (
    apply_legacy_backfill,
    dry_run_legacy_backfill,
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
    include_user_counts: bool = False,
    _admin: dict = Depends(require_permission("role", "read")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_read()
    q: dict = {}
    # Tenant-scoped custom roles + all system roles.
    if ctx.tenant_id and not ctx.is_platform_admin:
        q = {"$or": [
            {"is_system": True},
            {"tenant_id": ctx.tenant_id},
        ]}
    rows = [r async for r in db.roles.find(q, {"_id": 0}).sort("name", 1)]
    out = []
    for r in rows:
        grants = [
            g async for g in db.role_permissions.find(
                {"role_key": r["key"]}, {"_id": 0},
            )
        ]
        r["grants"] = grants
        # Normalize flag for frontend (baseline rows predate is_custom).
        r["is_custom"] = bool(r.get("is_custom")) or not r.get("is_system", False)
        if include_user_counts:
            r["user_count"] = await db.user_roles.count_documents(
                {"role_key": r["key"], "status": "active"},
            )
        out.append(r)
    return out


class RoleWriteModel(BaseModel):
    """Admin-facing payload for create/update of a role."""
    name: str = Field(..., min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    permission_keys: list[str] = Field(default_factory=list)
    cloned_from: str | None = None  # role_key the user cloned from (for provenance)
    # Per-permission security policies — {key: {requires_mfa, requires_approval, break_glass_allowed}}.
    # Only applies to keys also listed in permission_keys. Optional.
    permission_policies: dict | None = None


def _slugify_key(name: str, suffix: str = "") -> str:
    safe = "".join(
        ch.lower() if ch.isalnum() else "_" for ch in name.strip()
    ).strip("_")
    safe = "_".join(filter(None, safe.split("_")))
    return f"{safe or 'role'}_{suffix}" if suffix else (safe or "role")


async def _insert_custom_role(
    db, actor: dict, ctx: TenantContext, payload: RoleWriteModel,
    request: Request,
) -> dict:
    """Create a custom role + its role_permissions rows (idempotent on key)."""
    now = datetime.now(timezone.utc).isoformat()
    base_key = _slugify_key(payload.name)
    # Ensure unique key — suffix with a short uuid if collision.
    candidate = f"custom_{base_key}"
    if await db.roles.find_one({"key": candidate}, {"_id": 0, "id": 1}):
        candidate = f"custom_{base_key}_{uuid.uuid4().hex[:6]}"
    doc = {
        "id": str(uuid.uuid4()),
        "key": candidate,
        "name": payload.name.strip(),
        "description": payload.description.strip(),
        "abbr": "".join(w[0] for w in payload.name.split()[:2]).upper()[:2] or "CR",
        "is_system": False,
        "is_custom": True,
        "privileged": False,
        "service_account": False,
        "tenant_id": ctx.tenant_id,
        "cloned_from": payload.cloned_from,
        "created_at": now,
        "created_by_id": actor["id"],
        "created_by_email": actor.get("email"),
        "updated_at": now,
    }
    await db.roles.insert_one(dict(doc))
    grants_docs = _grants_from_keys(
        candidate, payload.permission_keys, now,
        policies=payload.permission_policies,
    )
    if grants_docs:
        await db.role_permissions.insert_many(grants_docs)
    await log_audit(
        action="authz.role.created",
        actor_id=actor["id"], actor_email=actor["email"],
        actor_role=actor.get("role"), entity_type="role",
        entity_id=candidate, request=request,
        tenant_id=ctx.tenant_id,
        metadata={
            "name": doc["name"], "tenant_id": ctx.tenant_id,
            "permission_count": len(grants_docs),
            "cloned_from": payload.cloned_from,
        },
    )
    return doc


def _grants_from_keys(
    role_key: str,
    keys: list[str],
    now: str,
    policies: dict | None = None,
) -> list[dict]:
    """Build role_permissions docs for a list of permission keys.

    Custom-role defaults:
      scope=all_org, no MFA/approval/break-glass flags. Admins can
      now optionally tighten per-permission via the `policies` dict,
      keyed by permission key:
          { "patient.delete": {"requires_mfa": True},
            "payment.refund": {"requires_approval": True, "requires_mfa": True},
            "break_glass.activate": {"break_glass_allowed": True} }
    """
    seen: set[str] = set()
    out: list[dict] = []
    valid_keys = {f"{p['resource']}.{p['action']}" for p in PERMISSIONS}
    policies = policies or {}
    for k in keys:
        if k in seen or k not in valid_keys:
            continue
        seen.add(k)
        pol = policies.get(k) or {}
        out.append({
            "id": str(uuid.uuid4()),
            "role_key": role_key,
            "permission_key": k,
            "scope": "all_org",
            "requires_mfa": bool(pol.get("requires_mfa", False)),
            "requires_approval": bool(pol.get("requires_approval", False)),
            "break_glass_allowed": bool(pol.get("break_glass_allowed", False)),
            "custom": True,
            "created_at": now,
        })
    return out


@router.post("/roles", status_code=201)
async def create_custom_role(
    payload: RoleWriteModel,
    request: Request,
    actor: dict = Depends(require_permission("role", "create")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    if len(payload.permission_keys) == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Role must include at least one permission",
        )
    doc = await _insert_custom_role(db, actor, ctx, payload, request)
    return {**doc, "grants": [], "is_custom": True, "user_count": 0}


@router.post("/roles/{role_key}/clone", status_code=201)
async def clone_role(
    role_key: str,
    payload: dict,
    request: Request,
    actor: dict = Depends(require_permission("role", "create")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Clone any role (system or custom) into a new custom role.

    Payload: { name: str, description?: str }
    The clone copies permission keys from the source; admins can then
    edit via PATCH. Tenant-scoped (a tenant admin can't clone another
    tenant's custom role).
    """
    db = get_db_write()
    name = (payload or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name is required")
    src_q: dict = {"key": role_key}
    if ctx.tenant_id and not ctx.is_platform_admin:
        src_q = {"key": role_key, "$or": [
            {"is_system": True},
            {"tenant_id": ctx.tenant_id},
        ]}
    src = await db.roles.find_one(src_q, {"_id": 0})
    if not src:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source role not found")
    grants = [
        g async for g in db.role_permissions.find(
            {"role_key": role_key}, {"_id": 0, "permission_key": 1},
        )
    ]
    perm_keys = [g["permission_key"] for g in grants]
    write = RoleWriteModel(
        name=name,
        description=(payload.get("description") or src.get("description") or ""),
        permission_keys=perm_keys,
        cloned_from=role_key,
    )
    doc = await _insert_custom_role(db, actor, ctx, write, request)
    return {**doc, "grants": [], "is_custom": True, "user_count": 0}


@router.patch("/roles/{role_key}")
async def update_custom_role(
    role_key: str,
    payload: dict,
    request: Request,
    actor: dict = Depends(require_permission("role", "update")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Update a custom role's name / description / permission keys.

    System roles are read-only (409 on attempt).
    """
    db = get_db_write()
    role_q: dict = {"key": role_key}
    if ctx.tenant_id and not ctx.is_platform_admin:
        role_q["$or"] = [{"is_system": True}, {"tenant_id": ctx.tenant_id}]
    role = await db.roles.find_one(role_q, {"_id": 0})
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if role.get("is_system"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "System roles are read-only. Clone this role to customize it.",
        )
    now = datetime.now(timezone.utc).isoformat()
    set_fields: dict = {"updated_at": now,
                        "updated_by_id": actor["id"],
                        "updated_by_email": actor.get("email")}
    if "name" in payload:
        name = (payload["name"] or "").strip()
        if len(name) < 2:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Name must be at least 2 characters",
            )
        set_fields["name"] = name
    if "description" in payload:
        set_fields["description"] = (payload["description"] or "").strip()[:500]

    await db.roles.update_one({"key": role_key}, {"$set": set_fields})

    if "permission_keys" in payload:
        keys = payload["permission_keys"] or []
        if not isinstance(keys, list) or len(keys) == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "permission_keys must be a non-empty list",
            )
        policies = payload.get("permission_policies") or {}
        if not isinstance(policies, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "permission_policies must be an object",
            )
        # Replace all role_permissions for this role.
        await db.role_permissions.delete_many({"role_key": role_key})
        docs = _grants_from_keys(role_key, keys, now, policies=policies)
        if docs:
            await db.role_permissions.insert_many(docs)
        # Bump session_epoch for every user with this role so their token
        # is re-evaluated on next request.
        user_ids = [
            u["user_id"] async for u in db.user_roles.find(
                {"role_key": role_key, "status": "active"},
                {"_id": 0, "user_id": 1},
            )
        ]
        if user_ids:
            await db.users.update_many(
                {"id": {"$in": user_ids}},
                {"$inc": {"session_epoch": 1}},
            )

    await log_audit(
        action="authz.role.updated",
        actor_id=actor["id"], actor_email=actor["email"],
        actor_role=actor.get("role"), entity_type="role",
        entity_id=role_key, request=request,
        tenant_id=ctx.tenant_id,
        metadata={
            "changed_fields": list(set_fields.keys()),
            "permission_keys_changed": "permission_keys" in payload,
            "tenant_id": ctx.tenant_id,
        },
    )
    updated = await db.roles.find_one({"key": role_key}, {"_id": 0})
    grants = [
        g async for g in db.role_permissions.find(
            {"role_key": role_key}, {"_id": 0},
        )
    ]
    uc = await db.user_roles.count_documents(
        {"role_key": role_key, "status": "active"},
    )
    return {**updated, "grants": grants, "is_custom": True, "user_count": uc}


@router.delete("/roles/{role_key}")
async def delete_custom_role(
    role_key: str,
    request: Request,
    force: bool = False,
    actor: dict = Depends(require_permission("role", "update")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Archive (soft-delete) a custom role.

    If the role is in use (has active user_roles rows), returns 409
    listing the usage count unless `force=true` is passed. On force,
    every user's assignment to this role is revoked and their
    session_epoch bumped.
    """
    db = get_db_write()
    role_q: dict = {"key": role_key}
    if ctx.tenant_id and not ctx.is_platform_admin:
        role_q["$or"] = [{"is_system": True}, {"tenant_id": ctx.tenant_id}]
    role = await db.roles.find_one(role_q, {"_id": 0})
    if not role:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if role.get("is_system"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "System roles cannot be deleted.",
        )
    user_count = await db.user_roles.count_documents(
        {"role_key": role_key, "status": "active"},
    )
    if user_count > 0 and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Role is assigned to {user_count} user(s). "
            f"Reassign them first or call again with ?force=true.",
        )
    now = datetime.now(timezone.utc).isoformat()
    if user_count > 0 and force:
        # Revoke assignments + bump session_epoch for each affected user.
        affected = [
            u["user_id"] async for u in db.user_roles.find(
                {"role_key": role_key, "status": "active"},
                {"_id": 0, "user_id": 1},
            )
        ]
        await db.user_roles.update_many(
            {"role_key": role_key, "status": "active"},
            {"$set": {"status": "revoked", "revoked_at": now,
                      "revoked_by_id": actor["id"]}},
        )
        if affected:
            await db.users.update_many(
                {"id": {"$in": affected}},
                {"$inc": {"session_epoch": 1}},
            )
    await db.role_permissions.delete_many({"role_key": role_key})
    await db.roles.delete_one({"key": role_key})
    await log_audit(
        action="authz.role.deleted",
        actor_id=actor["id"], actor_email=actor["email"],
        actor_role=actor.get("role"), entity_type="role",
        entity_id=role_key, request=request,
        tenant_id=ctx.tenant_id,
        metadata={
            "was_assigned_to": user_count, "forced": force,
            "tenant_id": ctx.tenant_id,
        },
    )
    return {"ok": True, "deleted_role_key": role_key,
            "users_unassigned": user_count if force else 0}


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
# Grouped permission catalog + plain-English access summaries
#
# These endpoints back the redesigned admin UX (Users / Roles / Role
# Editor / "Review access before save"). They NEVER expose per-user
# effective permissions to non-admins — see the guards below.
# ---------------------------------------------------------------------------

@router.get("/permission-catalog")
async def get_permission_catalog(
    _admin: dict = Depends(require_permission("role", "read")),
):
    """Return every permission grouped by product module (Dashboard,
    Scheduling, Patients, Clinical, Billing, Claims, Reports,
    Compliance & Audit, Settings, User Management, Administration),
    with plain-English labels and helper text suitable for the new
    Role Editor accordion.

    Shape:
      {
        "modules": [{key, label, description}, ...],   # order to render
        "groups":  [{module, label, description,
                     permissions: [{key, label, help, sensitivity,
                                    phi, clinical, financial, export,
                                    destructive, privileged,
                                    resource, action}, ...]}, ...]
      }
    """
    return {
        "modules": MODULES,
        "groups": grouped_catalog(),
    }


@router.get("/users/{user_id}/effective-permissions")
async def user_effective_permissions(
    user_id: str,
    explain: bool = False,
    admin: dict = Depends(require_permission("user", "read")),
):
    """Compute a user's effective permissions (role + location + active
    overrides + active elevations). Returns the same shape as
    `/me/permissions` so the Users-admin UI can reuse the renderer.

    When `explain=true`, also returns a plain-English summary via
    `permission_catalog.explain_permissions()` — used by the
    "Review access before save" step of the Create User flow.
    """
    db = get_db_read()
    target = await db.users.find_one(
        {"id": user_id},
        {"_id": 0, "id": 1, "email": 1, "role": 1, "tenant_id": 1,
         "session_epoch": 1, "name": 1},
    )
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Tenant isolation — platform admin sees everything; tenant admin
    # must stay within their own tenant.
    actor_tenant = admin.get("tenant_id")
    target_tenant = target.get("tenant_id")
    if (
        actor_tenant is not None
        and target_tenant is not None
        and actor_tenant != target_tenant
    ):
        # Don't leak existence.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    grants = await effective_grants(target)
    role_rows = [
        r async for r in db.user_roles.find(
            {"user_id": user_id, "status": "active"}, {"_id": 0},
        )
    ]
    role_keys = [r["role_key"] for r in role_rows]
    if not role_keys:
        legacy = (target.get("role") or "").lower()
        mapped = LEGACY_ROLE_TO_KEY.get(legacy)
        if mapped:
            role_keys = [mapped]

    locs = [
        l async for l in db.user_location_assignments.find(
            {"user_id": user_id, "status": "active"}, {"_id": 0},
        )
    ]

    permissions_list = [
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
    ]
    payload = {
        "user_id": target["id"],
        "email": target.get("email"),
        "name": target.get("name"),
        "legacy_role": target.get("role"),
        "role_keys": role_keys,
        "location_ids": [l["location_id"] for l in locs],
        "permissions": permissions_list,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if explain:
        payload["explanation"] = explain_permissions(
            [p["key"] for p in permissions_list],
        )
    return payload


@router.post("/roles/preview-effective-permissions")
async def preview_role_effective_permissions(
    payload: dict,
    _admin: dict = Depends(require_permission("role", "read")),
):
    """Preview what a role would look like if a given set of permission
    keys were granted. Used by the new Role Editor's "review access"
    step before saving a custom role.

    Payload:
      { "permission_keys": ["patient.read", "appointment.create", ...] }
    """
    keys = payload.get("permission_keys") or []
    if not isinstance(keys, list):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "permission_keys must be a list",
        )
    return {"explanation": explain_permissions(keys)}


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



# ---------------------------------------------------------------------------
# Legacy-role migration endpoints (Phase 5)
#
# Every boot runs an idempotent backfill in `seed_authz()`. These admin
# endpoints expose the same logic for on-demand use, with a dry-run
# preview. Tenant admins see only their tenant's users; platform admins
# see everything.
# ---------------------------------------------------------------------------

@router.get("/migration/legacy/dry-run")
async def migration_legacy_dry_run(
    actor: dict = Depends(require_permission("role", "read")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Preview what the legacy-role backfill would write. No mutations."""
    tenant = ctx.tenant_id if (ctx.tenant_id and not ctx.is_platform_admin) else None
    return await dry_run_legacy_backfill(tenant_id=tenant)


@router.post("/migration/legacy/apply")
async def migration_legacy_apply(
    request: Request,
    actor: dict = Depends(require_permission("role", "update")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Run the legacy-role backfill once. Idempotent."""
    tenant = ctx.tenant_id if (ctx.tenant_id and not ctx.is_platform_admin) else None
    result = await apply_legacy_backfill(
        actor_id=actor["id"],
        actor_email=actor.get("email") or "admin",
        tenant_id=tenant,
    )
    await log_audit(
        action="authz.migration.legacy_backfill_applied",
        actor_id=actor["id"], actor_email=actor.get("email"),
        actor_role=actor.get("role"), entity_type="migration",
        entity_id="legacy-role-backfill", request=request,
        tenant_id=ctx.tenant_id,
        metadata={
            "tenant_id": tenant,
            "inserted_count": result["inserted_count"],
            "total_candidates": result["total_candidates"],
        },
    )
    return result


# ---------------------------------------------------------------------------
# Access Change History (Phase 5)
#
# Thin wrapper over the general audit log, prefiltered to
# authz.role.* / authz.override.* / authz.elevation.* /
# authz.migration.* actions so the admin UI doesn't have to compose
# filters.
# ---------------------------------------------------------------------------

@router.get("/access-history")
async def access_history(
    limit: int = 100,
    action_prefix: str | None = None,
    _admin: dict = Depends(require_permission("audit_log", "read")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Return audit-log rows for access-management actions.

    If `action_prefix` is None, includes every authz.* action.
    """
    limit = max(1, min(limit, 500))
    db = get_db_read()
    q: dict = {}
    if action_prefix:
        q["action"] = {"$regex": f"^{action_prefix}"}
    else:
        q["action"] = {"$regex": "^authz\\."}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    rows = [
        r async for r in db.audit_logs.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    ]
    return {"rows": rows, "count": len(rows)}
