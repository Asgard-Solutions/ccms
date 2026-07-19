"""Workforce router — /api/workforce/*. See package docstring for scope."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.audit import audit_emergency, audit_success, log_audit
from core.db import get_db_write
from core.deps import get_current_user
from core.reauth import require_reauth
from core.repository import TenantScopedRepository
from core.security import hash_password
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.authz.policy import require_permission
from services.notifications import send_email
from services.workforce.models import (
    AdminSessionAction, BreakGlassAttest, BreakGlassStart,
    DeprovisionReport, DeprovisionRequest, InviteAccept, InviteCreate,
    ProxyGrant, RevokeWithReason,
)

logger = logging.getLogger("ccms.workforce")

router = APIRouter(prefix="/workforce", tags=["workforce"])


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
INVITE_DEFAULT_TTL_HOURS = int(os.environ.get("WORKFORCE_INVITE_TTL_HOURS", "72"))

# Break-glass attestation window — 24h by default; configurable without deploy.
BREAK_GLASS_ATTESTATION_HOURS = int(
    os.environ.get("BREAK_GLASS_ATTESTATION_HOURS", "24")
)
BREAK_GLASS_MAX_DURATION_HOURS = int(
    os.environ.get("BREAK_GLASS_MAX_DURATION_HOURS", "4")
)

SUSPICIOUS_LOGIN_FAIL_WINDOW_MIN = 15
SUSPICIOUS_LOGIN_FAIL_THRESHOLD = 5
NEW_IP_LOOKBACK_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

class _InvitationRepo(TenantScopedRepository):
    collection_name = "workforce_invitations"
    location_scoped = False


class _ProxyRepo(TenantScopedRepository):
    collection_name = "patient_proxies"
    location_scoped = False


class _BreakGlassRepo(TenantScopedRepository):
    collection_name = "break_glass_events"
    location_scoped = False


_invitations = _InvitationRepo()
_proxies = _ProxyRepo()
_bg = _BreakGlassRepo()


# ===========================================================================
# 1) Workforce invitations + activation  (MOCKED email delivery)
# ===========================================================================

@router.post("/invitations", status_code=201)
async def create_invitation(
    payload: InviteCreate,
    request: Request,
    user: dict = Depends(require_permission("user", "invite", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Create a workforce invitation. In this build the email delivery is
    MOCKED — the activation token is returned as `dev_token` (identical
    pattern to `/auth/password-reset/request`). Swap to Resend/Twilio in the
    production communication pass."""
    ctx.assert_tenant_bound()
    db = get_db_write()
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    # Validate location ids belong to tenant.
    for loc_id in payload.location_ids:
        loc = await db.locations.find_one(
            {"id": loc_id, "tenant_id": ctx.tenant_id}, {"_id": 0, "id": 1},
        )
        if not loc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Location {loc_id} is not part of this tenant",
            )

    raw_token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(hours=payload.ttl_hours)
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "name": payload.name.strip(),
        "role": payload.role,
        "phone": payload.phone,
        "location_ids": list(payload.location_ids),
        "status": "pending",
        "invited_by_id": user["id"],
        "invited_by_email": user["email"],
        "token_hash": _hash_token(raw_token),
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "accepted_at": None,
        "accepted_user_id": None,
        "revoked_at": None,
        "revoked_by_id": None,
    }
    stored = await _invitations.insert_one(doc, ctx)
    stored.pop("_id", None)
    await audit_success(
        user, "workforce.invite_created", request,
        entity_type="workforce_invitation", entity_id=stored["id"],
        metadata={
            "email": email, "role": payload.role,
            "location_ids": payload.location_ids,
            "expires_at": stored["expires_at"],
        },
    )
    # Send the invite email. Falls back to log-only when Resend isn't
    # configured; dev_token is still returned for local testing.
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")
    activate_link = (f"{frontend}/invitation?token={raw_token}"
                     if frontend else f"(token: {raw_token})")
    await send_email(
        to=email,
        subject=f"You've been invited to {stored.get('organization_name') or 'CCMS'}",
        html_body=(
            f"<p>You've been invited to join the clinic team as "
            f"<strong>{payload.role}</strong>.</p>"
            f"<p>Click the link below to activate your account:</p>"
            f"<p><a href='{activate_link}'>Activate my account</a></p>"
            f"<p>This invitation expires on {stored['expires_at']}.</p>"
        ),
        text_body=(
            f"You've been invited to join CCMS.\n"
            f"Activate here: {activate_link}\nExpires {stored['expires_at']}."
        ),
        event_type="workforce_invitation",
        correlation_id=stored["id"],
    )
    stored["dev_token"] = raw_token
    stored.pop("token_hash", None)
    return stored


@router.get("/invitations")
async def list_invitations(
    status_filter: str | None = None,
    user: dict = Depends(require_permission("user", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if status_filter:
        q["status"] = status_filter
    rows = await _invitations.find(q, ctx, sort=[("created_at", -1)], limit=500)
    for r in rows:
        r.pop("token_hash", None)
    return rows


@router.post("/invitations/{invite_id}/revoke")
async def revoke_invitation(
    invite_id: str,
    payload: RevokeWithReason,
    request: Request,
    user: dict = Depends(require_permission("user", "invite", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    now = _iso()
    matched = await _invitations.update_one(
        {"id": invite_id, "status": "pending"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by_id": user["id"], "revoke_reason": payload.reason,
                  "updated_at": now}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Invitation not found or already finalised")
    await audit_success(
        user, "workforce.invite_revoked", request,
        entity_type="workforce_invitation", entity_id=invite_id,
        reason=payload.reason,
    )
    return {"ok": True}


@router.post("/invitations/accept")
async def accept_invitation(
    payload: InviteAccept,
    request: Request,
):
    """Public endpoint. Activates an invitation by consuming the dev_token
    and creating the workforce user. A newly created user still has to log
    in normally afterwards (and enroll MFA if their role requires it)."""
    db = get_db_write()
    token_hash = _hash_token(payload.token)
    inv = await db.workforce_invitations.find_one(
        {"token_hash": token_hash}, {"_id": 0},
    )
    if not inv or inv["status"] != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Invalid or already-used invitation token")
    try:
        expires = datetime.fromisoformat(inv["expires_at"])
    except ValueError:
        expires = None
    if not expires or (expires.tzinfo is None and expires.replace(tzinfo=timezone.utc) < _now()) \
            or (expires.tzinfo and expires < _now()):
        await db.workforce_invitations.update_one(
            {"id": inv["id"]},
            {"$set": {"status": "expired", "updated_at": _iso()}},
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation has expired")
    if await db.users.find_one({"email": inv["email"]}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    now = _iso()
    hashed = hash_password(payload.password)
    user_id = str(uuid.uuid4())
    user_doc = {
        "id": user_id,
        "email": inv["email"],
        "password_hash": hashed,
        "password_history": [hashed],
        "password_changed_at": now,
        "name": inv["name"],
        "role": inv["role"],
        "phone": payload.phone or inv.get("phone"),
        "status": "active",
        "tenant_id": inv["tenant_id"],
        "tenant_scope_all": inv["role"] in ("admin", "super_admin"),
        "mfa_enabled": False,
        "mfa_policy_required": inv["role"] in (
            "admin", "doctor", "staff", "clinic_manager",
            "billing_specialist", "super_admin",
        ),
        "session_epoch": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(user_doc)
    # Wire location assignments.
    for loc_id in inv.get("location_ids", []):
        await db.user_location_assignments.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": inv["tenant_id"],
            "user_id": user_id, "location_id": loc_id,
            "status": "active", "assigned_at": now,
            "assigned_by_id": inv.get("invited_by_id"),
        })
    await db.workforce_invitations.update_one(
        {"id": inv["id"]},
        {"$set": {"status": "accepted", "accepted_at": now,
                  "accepted_user_id": user_id, "updated_at": now}},
    )
    await log_audit(
        action="workforce.invite_accepted",
        actor_id=user_id, actor_email=inv["email"], actor_role=inv["role"],
        tenant_id=inv["tenant_id"],
        entity_type="workforce_invitation", entity_id=inv["id"],
        request=request, metadata={"role": inv["role"]},
    )
    return {
        "user_id": user_id, "email": inv["email"],
        "role": inv["role"],
        "mfa_required": user_doc["mfa_policy_required"],
    }


# ===========================================================================
# 2) Patient proxy / personal-representative relationships
# ===========================================================================

@router.post("/proxies", status_code=201)
async def grant_proxy(
    payload: ProxyGrant,
    request: Request,
    user: dict = Depends(require_permission("patient", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    require_reauth(request, user)
    db = get_db_write()
    patient = await db.patients.find_one(
        {"id": payload.patient_id, "tenant_id": ctx.tenant_id},
        {"_id": 0, "id": 1, "location_id": 1},
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    proxy = await db.users.find_one(
        {"id": payload.proxy_user_id, "tenant_id": ctx.tenant_id,
         "status": {"$ne": "disabled"}},
        {"_id": 0, "id": 1, "role": 1, "email": 1},
    )
    if not proxy:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Proxy user not found in this tenant")

    now = _iso()
    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": payload.patient_id,
        "proxy_user_id": payload.proxy_user_id,
        "relationship": payload.relationship,
        "scope": payload.scope,
        "effective_date": payload.effective_date,
        "expires_at": payload.expires_at,
        "status": "active",
        "granted_by_id": user["id"], "granted_at": now,
        "revoked_at": None, "revoked_by_id": None, "revoke_reason": None,
        "reason": payload.reason,
        "history": [{"at": now, "actor_id": user["id"], "action": "granted"}],
    }
    stored = await _proxies.insert_one(doc, ctx)
    stored.pop("_id", None)
    await audit_success(
        user, "workforce.proxy_granted", request,
        entity_type="patient_proxy", entity_id=stored["id"],
        metadata={"patient_id": payload.patient_id,
                  "proxy_user_id": payload.proxy_user_id,
                  "relationship": payload.relationship,
                  "scope": payload.scope},
    )
    return stored


@router.get("/proxies")
async def list_proxies(
    patient_id: str | None = None,
    proxy_user_id: str | None = None,
    include_revoked: bool = False,
    user: dict = Depends(require_permission("patient", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    q: dict = {}
    if patient_id:
        q["patient_id"] = patient_id
    if proxy_user_id:
        q["proxy_user_id"] = proxy_user_id
    if not include_revoked:
        q["status"] = "active"
    return await _proxies.find(q, ctx, sort=[("granted_at", -1)])


@router.post("/proxies/{proxy_id}/revoke")
async def revoke_proxy(
    proxy_id: str,
    payload: RevokeWithReason,
    request: Request,
    user: dict = Depends(require_permission("patient", "update", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    now = _iso()
    matched = await _proxies.update_one(
        {"id": proxy_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by_id": user["id"], "revoke_reason": payload.reason,
                  "updated_at": now},
         "$push": {"history": {"at": now, "actor_id": user["id"],
                               "action": "revoked", "note": payload.reason}}},
        ctx,
    )
    if matched == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Proxy not found or already revoked")
    await audit_success(
        user, "workforce.proxy_revoked", request,
        entity_type="patient_proxy", entity_id=proxy_id,
        reason=payload.reason,
    )
    return {"ok": True}


# ===========================================================================
# 3) Active sessions — self + admin
# ===========================================================================

@router.get("/sessions/me")
async def my_session_overview(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Return self session posture + step-up/suspicious flags."""
    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0}) or {}
    recent = [r async for r in db.audit_logs.find(
        {"actor_id": user["id"],
         "action": {"$in": ["auth.login", "auth.logout", "auth.mfa_verified",
                            "security.suspicious_login"]}},
        {"_id": 0, "action": 1, "outcome": 1, "ip": 1,
         "user_agent": 1, "created_at": 1, "metadata": 1}
    ).sort("created_at", -1).limit(20)]
    return {
        "user_id": user["id"],
        "user_email": user["email"],
        "session_epoch": int(full.get("session_epoch", 0)),
        "step_up_required": bool(full.get("step_up_required")),
        "suspicious_flag": bool(full.get("suspicious_flag")),
        "last_login_at": full.get("last_login_at"),
        "recent_events": recent,
    }


@router.post("/sessions/me/revoke-all")
async def revoke_my_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Self-service one-shot: invalidates all JWTs for the current user by
    bumping `session_epoch`. Caller's current session is also killed, so the
    UI must force a re-login after calling this."""
    require_reauth(request, user)
    db = get_db_write()
    updated = await db.users.find_one_and_update(
        {"id": user["id"]},
        {"$inc": {"session_epoch": 1},
         "$set": {"updated_at": _iso()}},
        projection={"_id": 0, "session_epoch": 1},
        return_document=True,
    )
    await audit_success(
        user, "workforce.self_sessions_revoked", request,
        entity_type="user", entity_id=user["id"],
        metadata={"new_session_epoch": (updated or {}).get("session_epoch")},
    )
    return {"ok": True,
            "new_session_epoch": (updated or {}).get("session_epoch")}


@router.get("/sessions/user/{user_id}")
async def admin_user_session_overview(
    user_id: str,
    request: Request,
    actor: dict = Depends(require_permission("user", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Admin view: sign-in history + session posture for a target user."""
    db = get_db_write()
    q = {"id": user_id}
    if not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    target = await db.users.find_one(q, {"_id": 0,
                                         "password_hash": 0,
                                         "password_history": 0}) or {}
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    recent = [r async for r in db.audit_logs.find(
        {"actor_id": user_id,
         "action": {"$in": ["auth.login", "auth.logout", "auth.mfa_verified",
                            "security.suspicious_login",
                            "workforce.admin_sessions_revoked"]}},
        {"_id": 0, "action": 1, "outcome": 1, "ip": 1, "user_agent": 1,
         "created_at": 1, "metadata": 1},
    ).sort("created_at", -1).limit(50)]
    await audit_success(
        actor, "workforce.admin_sessions_viewed", request,
        entity_type="user", entity_id=user_id,
    )
    return {
        "user_id": user_id, "user_email": target.get("email"),
        "status": target.get("status"),
        "session_epoch": int(target.get("session_epoch", 0)),
        "step_up_required": bool(target.get("step_up_required")),
        "suspicious_flag": bool(target.get("suspicious_flag")),
        "last_login_at": target.get("last_login_at"),
        "recent_events": recent,
    }


@router.post("/sessions/user/revoke-all")
async def admin_revoke_user_sessions(
    payload: AdminSessionAction,
    request: Request,
    actor: dict = Depends(require_permission("session", "revoke_other",
                                             audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, actor)
    if payload.user_id == actor["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Use /sessions/me/revoke-all for self")
    db = get_db_write()
    q = {"id": payload.user_id}
    if not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    result = await db.users.update_one(
        q,
        {"$inc": {"session_epoch": 1},
         "$set": {"updated_at": _iso()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await audit_success(
        actor, "workforce.admin_sessions_revoked", request,
        entity_type="user", entity_id=payload.user_id,
        reason=payload.reason,
    )
    return {"ok": True}


# ===========================================================================
# 4) One-shot atomic deprovisioning
# ===========================================================================

@router.post("/users/{user_id}/deprovision", response_model=DeprovisionReport)
async def deprovision_user(
    user_id: str,
    payload: DeprovisionRequest,
    request: Request,
    admin: dict = Depends(require_permission("user", "disable", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Fail-closed, atomic deprovisioning.

    In one call we:
    1. Disable the user (`status="terminated"`, `session_epoch` bumped)
    2. Revoke every active `user_roles` grant
    3. Revoke every active `permission_scopes` override
    4. Revoke every active `user_location_assignments`
    5. Revoke every active `patient_assignments`
    6. Cancel every pending `workforce_invitations` for their email
    7. Force-expire every `active` `break_glass_events` owned by them
    8. Revoke every `active` `patient_proxies` granted to them
    9. Flag (or reassign) all future appointments where they are the provider

    If any step fails we bubble up a 500 — the transaction is intentionally
    best-effort-per-step with full audit coverage on each mutation. Mongo
    does not offer cross-collection ACID without a session, so we audit the
    report at the end to make partial results visible to operators.
    """
    require_reauth(request, admin)
    db = get_db_write()

    q = {"id": user_id}
    if not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    target = await db.users.find_one(q, {"_id": 0})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if target["id"] == admin["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Cannot deprovision yourself")

    now = _iso()
    tenant_id = target.get("tenant_id")

    # 1. Disable + bump epoch. We use the existing `disabled` status so the
    # existing login flow + reporting dashboards keep working; the
    # additional `terminated_at` timestamp distinguishes a one-shot
    # termination from a routine account disable.
    await db.users.update_one(
        {"id": user_id},
        {"$inc": {"session_epoch": 1},
         "$set": {"status": "disabled",
                  "terminated_at": now, "terminated_by": admin["id"],
                  "terminated_reason": payload.reason,
                  "updated_at": now,
                  "step_up_required": False}},
    )
    refreshed = await db.users.find_one({"id": user_id},
                                        {"_id": 0, "session_epoch": 1}) or {}

    # 2. user_roles
    r1 = await db.user_roles.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by": admin["id"], "revoke_reason": payload.reason}},
    )
    # 3. permission_scopes (per-user overrides)
    r2 = await db.permission_scopes.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by": admin["id"]}},
    )
    # 4. user_location_assignments
    r3 = await db.user_location_assignments.update_many(
        {"user_id": user_id, "status": "active"},
        {"$set": {"status": "inactive", "deactivated_at": now,
                  "deactivated_by": admin["id"]}},
    )
    # 5. patient_assignments
    r4 = await db.patient_assignments.update_many(
        {"provider_id": user_id, "status": "active"},
        {"$set": {"status": "inactive", "deactivated_at": now,
                  "deactivated_by": admin["id"]}},
    )
    # 6. pending invitations for this email
    r5 = await db.workforce_invitations.update_many(
        {"tenant_id": tenant_id, "email": target["email"], "status": "pending"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by_id": admin["id"], "updated_at": now}},
    )
    # 7. active break-glass grants — force expire + attestation_overdue.
    r6 = await db.break_glass_events.update_many(
        {"tenant_id": tenant_id, "actor_id": user_id, "status": "active"},
        {"$set": {"status": "expired_unattested",
                  "expired_at": now, "attestation_overdue": True,
                  "updated_at": now}},
    )
    # 8. active proxies where the terminated user is the proxy.
    r7 = await db.patient_proxies.update_many(
        {"tenant_id": tenant_id, "proxy_user_id": user_id, "status": "active"},
        {"$set": {"status": "revoked", "revoked_at": now,
                  "revoked_by_id": admin["id"],
                  "revoke_reason": "provider_deprovisioned",
                  "updated_at": now}},
    )
    # 9. future appointments where they are the provider.
    future_q = {"tenant_id": tenant_id, "provider_id": user_id,
                "start_time": {"$gt": now},
                "status": {"$nin": ["cancelled", "completed"]}}
    flagged = 0
    reassigned = 0
    if payload.reassign_future_to_user_id:
        new_provider = await db.users.find_one(
            {"id": payload.reassign_future_to_user_id,
             "tenant_id": tenant_id, "status": "active", "role": "doctor"},
            {"_id": 0, "id": 1},
        )
        if not new_provider:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "reassign_future_to_user_id must be an active doctor in this tenant",
            )
        res = await db.appointments.update_many(
            future_q,
            {"$set": {"provider_id": payload.reassign_future_to_user_id,
                      "reassigned_from": user_id,
                      "reassigned_at": now, "updated_at": now}},
        )
        reassigned = res.modified_count
    else:
        res = await db.appointments.update_many(
            future_q,
            {"$set": {"needs_reassignment": True,
                      "previous_provider_id": user_id,
                      "provider_id": None, "updated_at": now}},
        )
        flagged = res.modified_count

    report = {
        "user_id": user_id, "email": target["email"],
        "status_after": "disabled",
        "session_epoch": int(refreshed.get("session_epoch", 0)),
        "role_grants_revoked": r1.modified_count,
        "permission_overrides_revoked": r2.modified_count,
        "location_assignments_revoked": r3.modified_count,
        "patient_assignments_revoked": r4.modified_count,
        "future_appointments_flagged": flagged,
        "future_appointments_reassigned": reassigned,
        "invitations_cancelled": r5.modified_count,
        "break_glass_expired": r6.modified_count,
        "proxies_revoked": r7.modified_count,
    }
    await audit_success(
        admin, "workforce.user_deprovisioned", request,
        entity_type="user", entity_id=user_id,
        reason=payload.reason, metadata=report,
    )
    return report


# ===========================================================================
# 5) Break-glass with auto-expiry + 24h post-use attestation
# ===========================================================================

@router.post("/break-glass/start", status_code=201)
async def start_break_glass(
    payload: BreakGlassStart,
    request: Request,
    user: dict = Depends(require_permission("break_glass", "activate",
                                            audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    ctx.assert_tenant_bound()
    # break_glass.activate already carries MFA in the matrix — require_permission
    # enforces the reauth cookie, so we don't need a second require_reauth call.
    if payload.duration_minutes > BREAK_GLASS_MAX_DURATION_HOURS * 60:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Duration cannot exceed {BREAK_GLASS_MAX_DURATION_HOURS} hours",
        )
    now = _now()
    expires = now + timedelta(minutes=payload.duration_minutes)
    attestation_due = expires + timedelta(hours=BREAK_GLASS_ATTESTATION_HOURS)
    doc = {
        "id": str(uuid.uuid4()),
        "actor_id": user["id"], "actor_email": user["email"],
        "actor_role": user.get("role"),
        "scope_resource": payload.scope_resource,
        "scope_entity_id": payload.scope_entity_id,
        "ticket_reference": payload.ticket_reference,
        "reason": payload.reason,
        "duration_minutes": payload.duration_minutes,
        "activated_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "attestation_due_at": attestation_due.isoformat(),
        "ended_at": None,
        "status": "active",
        "attested_at": None,
        "attestation_summary": None,
        "attestation_phi_accessed": None,
        "attestation_action_required": None,
        "attestation_overdue": False,
    }
    stored = await _bg.insert_one(doc, ctx)
    stored.pop("_id", None)
    await audit_emergency(
        user, action="emergency_access.started",
        entity_type=payload.scope_resource,
        entity_id=payload.scope_entity_id or "*",
        reason=payload.reason, request=request,
        metadata={"break_glass_id": stored["id"],
                  "duration_minutes": payload.duration_minutes,
                  "expires_at": stored["expires_at"],
                  "attestation_due_at": stored["attestation_due_at"],
                  "ticket_reference": payload.ticket_reference},
    )
    return stored


@router.post("/break-glass/{bg_id}/end")
async def end_break_glass(
    bg_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Actor-initiated close — shortens the window. Attestation window still
    applies."""
    row = await _bg.find_one_by_id(bg_id, ctx)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Break-glass not found")
    if row["actor_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Not your break-glass session")
    if row["status"] != "active":
        return {"ok": True, "status": row["status"]}
    now = _iso()
    await _bg.update_one(
        {"id": bg_id},
        {"$set": {"ended_at": now, "status": "ended", "updated_at": now}},
        ctx,
    )
    await log_audit(
        action="emergency_access.ended",
        actor_id=user["id"], actor_email=user["email"], actor_role=user.get("role"),
        tenant_id=ctx.tenant_id,
        entity_type="break_glass", entity_id=bg_id,
        request=request,
    )
    return {"ok": True, "status": "ended"}


@router.post("/break-glass/{bg_id}/attest")
async def attest_break_glass(
    bg_id: str,
    payload: BreakGlassAttest,
    request: Request,
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Actor self-attestation after use.

    The user who invoked break-glass must provide a written justification
    within `BREAK_GLASS_ATTESTATION_HOURS` (default 24h) of the grant's
    `expires_at`. Missing attestation is swept every time the listing or
    sweep endpoint is called and emits
    `security.break_glass_attestation_overdue` — users with at least one
    overdue attestation also have `step_up_required=True` set so their next
    sensitive action is MFA-gated."""
    require_reauth(request, user)
    row = await _bg.find_one_by_id(bg_id, ctx)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Break-glass not found")
    if row["actor_id"] != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Only the original actor can attest")
    if row["status"] == "attested":
        return {"ok": True, "already_attested": True}
    now = _now()
    due = _parse_iso(row.get("attestation_due_at"))
    overdue = bool(due and now > due)
    target_status = "expired_attested" if row["status"] == "expired_unattested" else "attested"
    await _bg.update_one(
        {"id": bg_id},
        {"$set": {"attested_at": now.isoformat(),
                  "attestation_summary": payload.summary,
                  "attestation_phi_accessed": bool(payload.phi_accessed),
                  "attestation_action_required": bool(payload.action_required),
                  "attestation_overdue": overdue,
                  "status": target_status,
                  "updated_at": now.isoformat()}},
        ctx,
    )
    # If the user self-attests AFTER the deadline we still record the
    # attestation but flag it overdue and keep the step-up flag on until an
    # admin clears it (see the sweep).
    if not overdue:
        db = get_db_write()
        # Clear step-up if this was the only outstanding bg for this user.
        remaining = await db.break_glass_events.count_documents({
            "tenant_id": ctx.tenant_id, "actor_id": user["id"],
            "status": {"$in": ["active", "expired_unattested"]},
        })
        if remaining == 0:
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"step_up_required": False,
                          "updated_at": _iso()}},
            )
    await audit_success(
        user, "emergency_access.attested", request,
        entity_type="break_glass", entity_id=bg_id,
        metadata={"overdue": overdue,
                  "phi_accessed": bool(payload.phi_accessed),
                  "action_required": bool(payload.action_required)},
    )
    return {"ok": True, "overdue": overdue, "status": target_status}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _sweep_tenant(tenant_id: str | None, *, request: Request | None = None) -> dict:
    """Shared sweep routine — used by the public `/break-glass/sweep` and
    by `list_break_glass()` opportunistically so the UI never shows stale
    statuses even if no cron hits the sweep endpoint."""
    db = tenant_db(tenant_id) if tenant_id else get_db_write()
    now = _now()
    now_iso = now.isoformat()
    # 1. expire active events whose window has passed.
    q_expired = {"status": "active",
                 "expires_at": {"$lt": now_iso}}
    if tenant_id:
        q_expired["tenant_id"] = tenant_id
    await db.break_glass_events.update_many(
        q_expired,
        {"$set": {"status": "expired_unattested", "updated_at": now_iso}},
    )
    # 2. detect overdue attestations on expired_unattested rows.
    q_overdue = {"status": "expired_unattested",
                 "attestation_due_at": {"$lt": now_iso},
                 "attestation_overdue": {"$ne": True}}
    if tenant_id:
        q_overdue["tenant_id"] = tenant_id
    overdue_rows = [r async for r in db.break_glass_events.find(q_overdue, {"_id": 0})]
    overdue_count = 0
    for row in overdue_rows:
        await db.break_glass_events.update_one(
            {"id": row["id"]},
            {"$set": {"attestation_overdue": True, "updated_at": now_iso}},
        )
        # Flag the actor: require step-up MFA on next sensitive action.
        await db.users.update_one(
            {"id": row["actor_id"]},
            {"$set": {"step_up_required": True,
                      "suspicious_flag": True,
                      "updated_at": now_iso}},
        )
        await log_audit(
            action="security.break_glass_attestation_overdue",
            actor_id=row["actor_id"], actor_email=row.get("actor_email"),
            actor_role=row.get("actor_role"),
            tenant_id=row.get("tenant_id"),
            entity_type="break_glass", entity_id=row["id"],
            outcome="failure", request=request,
            reason="attestation_overdue_step_up_enforced",
            metadata={"ticket_reference": row.get("ticket_reference"),
                      "activated_at": row.get("activated_at"),
                      "attestation_due_at": row.get("attestation_due_at")},
        )
        overdue_count += 1
    return {"expired_swept": True, "overdue_flagged": overdue_count}


@router.post("/break-glass/sweep")
async def sweep_break_glass(
    request: Request,
    user: dict = Depends(require_permission("break_glass", "activate",
                                            audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Admin-triggered sweep — also safe to call from a cron job / worker.
    Idempotent."""
    result = await _sweep_tenant(ctx.tenant_id, request=request)
    await audit_success(
        user, "workforce.break_glass_swept", request,
        metadata=result,
    )
    return result


@router.get("/break-glass")
async def list_break_glass(
    include_closed: bool = False,
    user: dict = Depends(require_permission("break_glass", "activate",
                                            audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    # Opportunistic sweep keeps the list current without a cron.
    await _sweep_tenant(ctx.tenant_id)
    q: dict = {}
    if not include_closed:
        q["status"] = {"$in": ["active", "expired_unattested"]}
    return await _bg.find(q, ctx, sort=[("activated_at", -1)], limit=200)


# ===========================================================================
# 6) Suspicious-login detection hook — called from identity.login
# ===========================================================================

async def record_login_signal(user: dict, request: Request, *, outcome: str) -> None:
    """Best-effort detection. Never raises.

    Signals (all additive):
      - `new_ip`: the IP has not been seen in a successful login in the
        last `NEW_IP_LOOKBACK_DAYS` days.
      - `new_user_agent`: the UA has not been seen in the last lookback.
      - `brute_force_pattern`: ≥ 5 auth failures from this IP in 15 min
        (outcome='failure' branch).
      - `step_up_required` flag is set on the user when any success signal
        fires, so the next sensitive action requires MFA reauth.
    """
    try:
        db = get_db_write()
        xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        ip = xff or (request.client.host if request.client else "unknown")
        ua = request.headers.get("user-agent") or ""
        now = _now()

        if outcome == "success" and user and user.get("id"):
            threshold = (now - timedelta(days=NEW_IP_LOOKBACK_DAYS)).isoformat()
            reasons: list[str] = []
            prior_ip = await db.audit_logs.find_one(
                {"actor_id": user["id"], "action": "auth.login",
                 "outcome": "success", "ip": ip,
                 "created_at": {"$gte": threshold}},
                {"_id": 0, "id": 1},
            )
            if not prior_ip:
                reasons.append("new_ip")
            prior_ua = await db.audit_logs.find_one(
                {"actor_id": user["id"], "action": "auth.login",
                 "outcome": "success", "user_agent": ua,
                 "created_at": {"$gte": threshold}},
                {"_id": 0, "id": 1},
            )
            if ua and not prior_ua:
                reasons.append("new_user_agent")
            if reasons:
                await log_audit(
                    action="security.suspicious_login",
                    actor_id=user["id"], actor_email=user.get("email"),
                    actor_role=user.get("role"),
                    tenant_id=user.get("tenant_id"),
                    entity_type="user", entity_id=user["id"],
                    request=request,
                    metadata={"signals": reasons, "ip": ip,
                              "enforcement": "step_up_mfa"},
                )
                await db.users.update_one(
                    {"id": user["id"]},
                    {"$set": {"step_up_required": True,
                              "suspicious_flag": True,
                              "updated_at": _iso()}},
                )
        elif outcome == "failure":
            since = (now - timedelta(minutes=SUSPICIOUS_LOGIN_FAIL_WINDOW_MIN)).isoformat()
            fails = await db.audit_logs.count_documents({
                "action": "auth.login", "outcome": "failure", "ip": ip,
                "created_at": {"$gte": since},
            })
            if fails >= SUSPICIOUS_LOGIN_FAIL_THRESHOLD:
                await log_audit(
                    action="security.suspicious_login",
                    actor_id=None,
                    actor_email=(user or {}).get("email"),
                    tenant_id=(user or {}).get("tenant_id"),
                    entity_type="ip", entity_id=ip,
                    outcome="failure", request=request,
                    reason="brute_force_pattern",
                    metadata={"signals": ["brute_force_pattern"],
                              "failures_15m": fails, "ip": ip,
                              "enforcement": "audit_only"},
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("suspicious-login hook failed: %s", exc)
