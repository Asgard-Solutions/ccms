"""Workforce & patient identity workflows — iteration 19.

One router covers several related identity flows that all share the same
security primitives (reauth, audit, tenant_id, session_epoch bump):

- Workforce invitations           → `POST /api/workforce/invitations`
- Accept invite (public)          → `POST /api/workforce/invitations/accept`
- Active sessions                 → `GET/DELETE /api/workforce/sessions`
- One-shot deprovision            → `POST /api/workforce/users/{id}/deprovision`
- Patient proxy relationships     → `POST/GET/DELETE /api/workforce/proxies`
- Formal break-glass              → `POST /api/workforce/break-glass/start|end|attest`
- Suspicious login events         → emitted from identity.login (hook in this module)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from core.audit import audit_emergency, audit_failure, audit_success, log_audit
from core.db import get_db_write
from core.deps import get_current_user
from core.reauth import require_reauth
from core.repository import TenantScopedRepository
from core.security import hash_password
from core.tenancy import TenantContext, get_tenant_context
from services.authz.policy import require_permission

router = APIRouter(prefix="/workforce", tags=["workforce"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


# ---------------------------------------------------------------------------
# 1. Workforce invitations
# ---------------------------------------------------------------------------
INVITE_TTL_HOURS = 72


class InviteCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "doctor", "staff", "front_desk", "clinic_manager",
                  "financial_admin"] = "staff"
    location_ids: list[str] = Field(default_factory=list)


def _sign_invite(invite_id: str, tenant_id: str, email: str) -> str:
    payload = {
        "sub": email, "tid": tenant_id, "iid": invite_id, "typ": "invite",
        "exp": int(_now().timestamp()) + INVITE_TTL_HOURS * 3600,
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")


@router.post("/invitations", status_code=201)
async def create_invitation(
    payload: InviteCreate,
    request: Request,
    user: dict = Depends(require_permission("user", "create", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    require_reauth(request, user)
    db = get_db_write()
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "User already exists")

    # Validate every location_id belongs to the caller's tenant.
    for loc_id in payload.location_ids:
        loc = await db.locations.find_one(
            {"id": loc_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1},
        )
        if not loc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Location {loc_id} is not in this tenant")

    invite_id = str(uuid.uuid4())
    now = _iso()
    doc = {
        "id": invite_id,
        "tenant_id": ctx.tenant_id,
        "email": email, "name": payload.name.strip(),
        "role": payload.role, "location_ids": payload.location_ids,
        "status": "invited",                # invited → accepted → cancelled / expired
        "invited_by": user["id"],
        "created_at": now, "updated_at": now,
        "expires_at": (_now() + timedelta(hours=INVITE_TTL_HOURS)).isoformat(),
        "accepted_at": None,
    }
    await db.invitations.insert_one(doc)
    token = _sign_invite(invite_id, ctx.tenant_id, email)
    await audit_success(user, "workforce.invite_created", request,
                        entity_type="invitation", entity_id=invite_id,
                        metadata={"role": payload.role, "email": email,
                                  "location_ids": payload.location_ids})
    return {"id": invite_id, "token": token,
            "expires_at": doc["expires_at"], "status": "invited"}


class InviteAccept(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=128)
    phone: str | None = None


@router.post("/invitations/accept", status_code=200)
async def accept_invitation(payload: InviteAccept, request: Request, response: Response):
    """Public endpoint — the invitee has the signed token from their email."""
    try:
        claims = jwt.decode(payload.token, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.PyJWTError:
        await log_audit(action="workforce.invite_accept_failed",
                        actor_id=None, outcome="failure",
                        reason="invalid_or_expired_token", request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired invitation")

    if claims.get("typ") != "invite":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid invitation")

    db = get_db_write()
    inv = await db.invitations.find_one({"id": claims["iid"]}, {"_id": 0})
    if not inv or inv["status"] != "invited":
        raise HTTPException(status.HTTP_410_GONE, "Invitation no longer valid")
    if inv["tenant_id"] != claims["tid"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invitation tenant mismatch")

    # Create user.
    hashed = hash_password(payload.password)
    now = _iso()
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "email": inv["email"], "name": inv["name"],
        "password_hash": hashed,
        "password_history": [hashed],
        "password_changed_at": now,
        "role": inv["role"],
        "phone": payload.phone,
        "status": "active",
        "tenant_id": inv["tenant_id"],
        "tenant_scope_all": inv["role"] in ("admin", "clinic_manager", "super_admin"),
        "mfa_enabled": False,
        "mfa_policy_required": inv["role"] in ("admin", "doctor", "staff",
                                               "clinic_manager", "financial_admin"),
        "session_epoch": 0,
        "created_at": now, "updated_at": now,
    }
    await db.users.insert_one(user_doc)
    # Wire location assignments.
    for loc_id in inv.get("location_ids", []):
        await db.user_location_assignments.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": inv["tenant_id"],
            "user_id": user_id, "location_id": loc_id,
            "status": "active", "assigned_at": now,
            "assigned_by_id": inv.get("invited_by"),
        })
    await db.invitations.update_one(
        {"id": inv["id"]},
        {"$set": {"status": "accepted", "accepted_at": now, "updated_at": now,
                  "user_id": user_id}},
    )
    await log_audit(action="workforce.invite_accepted",
                    actor_id=user_id, actor_email=inv["email"],
                    actor_role=inv["role"], tenant_id=inv["tenant_id"],
                    entity_type="invitation", entity_id=inv["id"],
                    request=request, metadata={"role": inv["role"]})
    return {"user_id": user_id, "email": inv["email"],
            "mfa_required": user_doc["mfa_policy_required"]}


@router.post("/invitations/{invite_id}/cancel")
async def cancel_invitation(
    invite_id: str, request: Request,
    user: dict = Depends(require_permission("user", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    result = await db.invitations.update_one(
        {"id": invite_id, "tenant_id": ctx.tenant_id, "status": "invited"},
        {"$set": {"status": "cancelled", "updated_at": _iso()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    await audit_success(user, "workforce.invite_cancelled", request,
                        entity_type="invitation", entity_id=invite_id)
    return {"ok": True}


@router.get("/invitations")
async def list_invitations(
    status_filter: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("user", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    q: dict = {"tenant_id": ctx.tenant_id}
    if status_filter:
        q["status"] = status_filter
    rows = [r async for r in db.invitations.find(q, {"_id": 0}).sort("created_at", -1)]
    return rows


# ---------------------------------------------------------------------------
# 2. Active sessions (revocation)
# ---------------------------------------------------------------------------

@router.get("/sessions")
async def my_sessions(
    user: dict = Depends(get_current_user),
):
    """Return the user's current session_epoch so they can 'revoke all' by
    bumping it. We don't track a full session list (stateless JWT) but the
    epoch mechanism gives us one-shot revoke. Enterprise grade multi-device
    listing would require stateful session records — flagged in docs."""
    return {
        "user_id": user["id"],
        "current_epoch": int(user.get("session_epoch", 0)),
        "revoke_all_url": "/api/workforce/sessions/revoke-all",
    }


@router.post("/sessions/revoke-all")
async def revoke_all_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
):
    require_reauth(request, user)
    db = get_db_write()
    await db.users.update_one(
        {"id": user["id"]},
        {"$inc": {"session_epoch": 1},
         "$set": {"updated_at": _iso()}},
    )
    await audit_success(user, "workforce.sessions_revoked_all", request,
                        entity_type="user", entity_id=user["id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# 3. One-shot deprovisioning
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/deprovision")
async def deprovision_user(
    user_id: str, request: Request, reason: str = Body(embed=True),
    admin: dict = Depends(require_permission("user", "disable", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, admin)
    if len(reason or "") < 10:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "reason required (≥10 chars) for deprovisioning")
    db = get_db_write()
    target = await db.users.find_one(
        {"id": user_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not target and not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not target:
        target = await db.users.find_one({"id": user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target["id"] == admin["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot deprovision yourself")

    now = _iso()
    # 1. Disable the user (status=disabled) + mark terminated.
    # 2. Bump session_epoch (invalidates JWTs).
    # 3. Mark active location assignments inactive.
    # 4. Mark pending invitations cancelled (shouldn't exist for an active user, but just in case).
    # 5. Mark active user_roles inactive.
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"session_epoch": 1},
         "$set": {"status": "terminated",
                  "terminated_at": now, "terminated_by": admin["id"],
                  "terminated_reason": reason, "updated_at": now}},
    )
    await db.user_location_assignments.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "inactive", "deactivated_at": now,
                  "deactivated_by": admin["id"]}},
    )
    await db.user_roles.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by": admin["id"], "revoke_reason": reason}},
    )
    await db.invitations.update_many(
        {"tenant_id": target["tenant_id"], "email": target["email"], "status": "invited"},
        {"$set": {"status": "cancelled", "updated_at": now}},
    )
    await audit_success(admin, "workforce.user_deprovisioned", request,
                        entity_type="user", entity_id=user_id,
                        reason=reason, metadata={"target_email": target["email"]})
    return {"ok": True, "user_id": user_id, "status": "terminated"}


# ---------------------------------------------------------------------------
# 4. Patient proxy / personal-representative relationships
# ---------------------------------------------------------------------------

class ProxyRepo(TenantScopedRepository):
    collection_name = "patient_proxies"
    location_scoped = False


_proxies = ProxyRepo()


class ProxyGrant(BaseModel):
    patient_id: str
    proxy_user_id: str
    basis: Literal["guardian", "healthcare_poa", "personal_representative",
                   "court_order", "clinic_authorized"] = "guardian"
    scope: Literal["read", "read_manage"] = "read"
    effective_date: str
    expires_at: str | None = None
    notes: str | None = None


@router.post("/proxies", status_code=201)
async def grant_proxy(
    payload: ProxyGrant, request: Request,
    user: dict = Depends(require_permission("patient", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    require_reauth(request, user)
    db = get_db_write()
    # Validate the patient and proxy user both belong to the tenant.
    patient = await db.patients.find_one(
        {"id": payload.patient_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1, "location_id": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    proxy = await db.users.find_one(
        {"id": payload.proxy_user_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1, "role": 1},
    )
    if not proxy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proxy user not in this tenant")

    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": payload.patient_id,
        "proxy_user_id": payload.proxy_user_id,
        "basis": payload.basis, "scope": payload.scope,
        "effective_date": payload.effective_date,
        "expires_at": payload.expires_at,
        "status": "active",
        "granted_by": user["id"], "granted_at": _iso(),
        "revoked_at": None, "revoked_by": None, "revoke_reason": None,
        "notes": payload.notes,
        "history": [{"at": _iso(), "actor_id": user["id"], "action": "granted"}],
    }
    stored = await _proxies.insert_one(doc, ctx)
    await audit_success(user, "workforce.proxy_granted", request,
                        entity_type="patient_proxy", entity_id=stored["id"],
                        metadata={"patient_id": payload.patient_id,
                                  "proxy_user_id": payload.proxy_user_id,
                                  "basis": payload.basis, "scope": payload.scope})
    return stored


@router.get("/proxies")
async def list_proxies(
    patient_id: str | None = None,
    proxy_user_id: str | None = None,
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("patient", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if patient_id:
        q["patient_id"] = patient_id
    if proxy_user_id:
        q["proxy_user_id"] = proxy_user_id
    return await _proxies.find(q, ctx, sort=[("granted_at", -1)])


@router.post("/proxies/{proxy_id}/revoke")
async def revoke_proxy(
    proxy_id: str, request: Request, reason: str = Body(embed=True),
    user: dict = Depends(require_permission("patient", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    now = _iso()
    matched = await _proxies.update_one(
        {"id": proxy_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by": user["id"], "revoke_reason": reason,
                  "updated_at": now},
         "$push": {"history": {"at": now, "actor_id": user["id"],
                               "action": "revoked", "note": reason}}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proxy not found or already revoked")
    await audit_success(user, "workforce.proxy_revoked", request,
                        entity_type="patient_proxy", entity_id=proxy_id,
                        reason=reason)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 5. Formal break-glass workflow
# ---------------------------------------------------------------------------
BREAK_GLASS_MAX_HOURS = 4


class BreakGlassStart(BaseModel):
    scope_resource: Literal["patient_chart", "audit_log", "billing"] = "patient_chart"
    scope_entity_id: str | None = None
    reason: str = Field(min_length=20, max_length=1000)
    duration_minutes: int = Field(default=60, ge=5, le=BREAK_GLASS_MAX_HOURS * 60)


@router.post("/break-glass/start", status_code=201)
async def start_break_glass(
    payload: BreakGlassStart, request: Request,
    user: dict = Depends(require_permission("emergency_access", "activate", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    require_reauth(request, user)
    now = _now()
    expires = now + timedelta(minutes=payload.duration_minutes)
    db = get_db_write()
    bg_id = str(uuid.uuid4())
    await db.break_glass_sessions.insert_one({
        "id": bg_id,
        "tenant_id": ctx.tenant_id,
        "actor_id": user["id"], "actor_email": user["email"],
        "scope_resource": payload.scope_resource,
        "scope_entity_id": payload.scope_entity_id,
        "reason": payload.reason,
        "started_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "ended_at": None,
        "attested_at": None, "attestation_note": None,
        "attestation_by": None,
        "status": "active",
    })
    await audit_emergency(
        user, action="emergency_access.started", request=request,
        entity_type=payload.scope_resource,
        entity_id=payload.scope_entity_id or "*",
        reason=payload.reason,
        metadata={"break_glass_id": bg_id,
                  "duration_minutes": payload.duration_minutes,
                  "expires_at": expires.isoformat()},
    )
    return {"id": bg_id, "expires_at": expires.isoformat()}


@router.post("/break-glass/{bg_id}/end")
async def end_break_glass(
    bg_id: str, request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    row = await db.break_glass_sessions.find_one(
        {"id": bg_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Break-glass session not found")
    if row["actor_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your break-glass session")
    now = _iso()
    await db.break_glass_sessions.update_one(
        {"id": bg_id},
        {"$set": {"ended_at": now, "status": "ended"}},
    )
    await log_audit(action="emergency_access.ended",
                    actor_id=user["id"], actor_email=user["email"],
                    actor_role=user["role"], tenant_id=ctx.tenant_id,
                    entity_type="break_glass", entity_id=bg_id,
                    request=request)
    return {"ok": True}


class BreakGlassAttest(BaseModel):
    note: str = Field(min_length=20, max_length=1000,
                      description="Reviewer's after-action attestation")
    action_required: bool = False


@router.post("/break-glass/{bg_id}/attest")
async def attest_break_glass(
    bg_id: str, payload: BreakGlassAttest, request: Request,
    reviewer: dict = Depends(require_permission("emergency_access", "review", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Required post-event review: another admin confirms the break-glass was appropriate."""
    require_reauth(request, reviewer)
    db = get_db_write()
    row = await db.break_glass_sessions.find_one(
        {"id": bg_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Break-glass session not found")
    if row["actor_id"] == reviewer["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Actor cannot attest their own break-glass event")
    now = _iso()
    await db.break_glass_sessions.update_one(
        {"id": bg_id},
        {"$set": {"attested_at": now, "attestation_note": payload.note,
                  "attestation_by": reviewer["id"],
                  "status": "attested",
                  "action_required": payload.action_required}},
    )
    await audit_success(reviewer, "emergency_access.attested", request,
                        entity_type="break_glass", entity_id=bg_id,
                        metadata={"action_required": payload.action_required})
    return {"ok": True}


@router.get("/break-glass")
async def list_break_glass(
    include_ended: bool = False, request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("emergency_access", "review", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    q: dict = {"tenant_id": ctx.tenant_id}
    if not include_ended:
        q["status"] = {"$in": ["active"]}
    rows = [r async for r in db.break_glass_sessions.find(q, {"_id": 0}).sort("started_at", -1).limit(200)]
    return rows


# ---------------------------------------------------------------------------
# 6. Suspicious-login detection hook (called from identity.login)
# ---------------------------------------------------------------------------

async def record_login_signal(user: dict, request: Request, outcome: str) -> None:
    """Compute a simple suspicion signal and emit an audit row when triggered.

    Signals:
      - first-ever login from a new IP for this user in 30 days  → `new_ip`
      - ≥ 5 failed logins in last 15 min for this identifier     → `brute_force_pattern`
    """
    db = get_db_write()
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = xff or (request.client.host if request.client else "unknown")
    now = _now()

    # New-IP heuristic.
    if outcome == "success":
        threshold = (now - timedelta(days=30)).isoformat()
        prior = await db.audit_logs.find_one(
            {"actor_id": user["id"], "action": "auth.login_success",
             "ip": ip, "created_at": {"$gte": threshold}},
            {"_id": 0, "id": 1},
        )
        if not prior:
            await log_audit(action="security.suspicious_login",
                            actor_id=user["id"], actor_email=user["email"],
                            actor_role=user.get("role"),
                            tenant_id=user.get("tenant_id"),
                            entity_type="user", entity_id=user["id"],
                            request=request,
                            metadata={"signal": "new_ip", "ip": ip})

    # Brute-force pattern.
    if outcome == "failure":
        since = (now - timedelta(minutes=15)).isoformat()
        fails = await db.audit_logs.count_documents({
            "action": "auth.login_failed", "ip": ip,
            "created_at": {"$gte": since},
        })
        if fails >= 5:
            await log_audit(action="security.suspicious_login",
                            actor_id=None, actor_email=user.get("email"),
                            tenant_id=user.get("tenant_id"),
                            entity_type="ip", entity_id=ip,
                            outcome="failure", request=request,
                            metadata={"signal": "brute_force_pattern",
                                      "failures_15m": fails, "ip": ip})
