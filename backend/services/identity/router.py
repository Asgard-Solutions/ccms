"""
Identity Service router — /api/auth/* (HIPAA-hardened).

Adds:
  - Strong password policy + history + rotation warnings
  - TOTP MFA with a short-lived mfa_ticket step
  - Step-up re-authentication for sensitive actions
  - Account disable (no hard delete)
  - Full audit trail for every auth action
"""
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.audit import audit_failure, audit_success, log_audit
from core import cache, cache_keys
from core.db import get_db, get_db_read, get_db_write, read_after_write_db
from core.deps import get_current_user, require_role
from core import rate_limit
from core.mfa import (
    MFA_REQUIRED_ROLES,
    create_mfa_ticket,
    decode_mfa_ticket,
    generate_backup_codes,
    generate_secret,
    provisioning_uri,
    verify_backup_code,
    verify_code,
)
from core.password_policy import (
    PASSWORD_HISTORY,
    PasswordPolicyError,
    password_expiry_status,
    reject_password_reuse,
    validate_strength,
)
from core.reauth import create_reauth_token
from core.security import (
    ACCESS_TOKEN_MINUTES,
    REFRESH_TOKEN_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from services.identity.models import (
    AdminUserCreate,
    LoginResult,
    MfaChallenge,
    MfaSetupResponse,
    MfaVerify,
    PasswordChange,
    ReauthRequest,
    UserLogin,
    UserPatch,
    UserPublic,
    UserRegister,
)

router = APIRouter(prefix="/auth", tags=["identity"])

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _cookie_kwargs(max_age: int) -> dict:
    return dict(
        httponly=True,
        secure=True,
        samesite="none",
        max_age=max_age,
        path="/",
    )


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    response.set_cookie("access_token", access, **_cookie_kwargs(ACCESS_TOKEN_MINUTES * 60))
    response.set_cookie(
        "refresh_token", refresh, **_cookie_kwargs(REFRESH_TOKEN_DAYS * 86400)
    )


def _clear_auth_cookies(response: Response) -> None:
    for c in ("access_token", "refresh_token", "reauth_token"):
        response.delete_cookie(c, path="/")


def _to_public(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "phone": user.get("phone"),
        "status": user.get("status", "active"),
        "mfa_enabled": bool(user.get("mfa_enabled")),
        "password_changed_at": user.get("password_changed_at"),
        "created_at": user["created_at"],
    }


# ---------------- Register (public → patient role only) ----------------

@router.post("/register", response_model=UserPublic)
async def register(payload: UserRegister, request: Request, response: Response):
    db = get_db()
    email = payload.email.lower().strip()
    try:
        validate_strength(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if await db.users.find_one({"email": email}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    now_iso = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())
    hashed = hash_password(payload.password)
    doc = {
        "id": user_id,
        "email": email,
        "password_hash": hashed,
        "password_history": [hashed],
        "password_changed_at": now_iso,
        "name": payload.name.strip(),
        "role": "patient",
        "phone": payload.phone,
        "status": "active",
        "mfa_enabled": False,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    await db.users.insert_one(doc)

    access = create_access_token(user_id, email, "patient")
    refresh = create_refresh_token(user_id)
    _set_auth_cookies(response, access, refresh)
    await log_audit(
        action="auth.registered",
        actor_id=user_id,
        actor_email=email,
        actor_role="patient",
        request=request,
    )
    return _to_public(doc)


# ---------------- Login + MFA ----------------

async def _lockout_check(db, identifier: str) -> None:
    attempt = await db.login_attempts.find_one({"identifier": identifier}, {"_id": 0})
    if not attempt:
        return
    if attempt.get("count", 0) < MAX_FAILED_ATTEMPTS:
        return
    locked_until_str = attempt.get("locked_until")
    if not locked_until_str:
        return
    try:
        locked_until = datetime.fromisoformat(locked_until_str)
    except Exception:
        return
    if locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Try again later.",
        )


@router.post("/login", response_model=LoginResult)
async def login(payload: UserLogin, request: Request, response: Response):
    # Outer rate-limit: 30 attempts per IP per minute. Per-email lockout in Mongo
    # remains the durable, audited brute-force control beneath this.
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    if not await rate_limit.is_allowed(f"login:{ip}", limit=30, window_seconds=60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests from this address. Please slow down.",
        )

    db = get_db_write()
    email = payload.email.lower().strip()
    identifier = email
    now = datetime.now(timezone.utc)

    await _lockout_check(db, identifier)
    user = await db.users.find_one({"email": email}, {"_id": 0})

    if not user or not verify_password(payload.password, user["password_hash"]):
        await db.login_attempts.update_one(
            {"identifier": identifier},
            {
                "$inc": {"count": 1},
                "$set": {
                    "locked_until": (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
                },
            },
            upsert=True,
        )
        await audit_failure(
            action="auth.login", request=request, actor_email=email,
            reason="invalid_credentials",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if user.get("status") == "disabled":
        await audit_failure(
            action="auth.login", request=request, actor_email=email,
            reason="account_disabled",
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    # Hard expiry — force a password change before issuing session cookies.
    exp = password_expiry_status(user.get("password_changed_at"))
    if exp["expired"]:
        await audit_failure(
            action="auth.login", request=request, actor_email=email,
            reason="password_expired",
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Password has expired. Please reset your password to continue.",
        )

    # MFA: required for staff-ish roles, optional for patients
    mfa_required = user.get("mfa_enabled") or user["role"] in MFA_REQUIRED_ROLES
    if mfa_required and user.get("mfa_enabled"):
        ticket = create_mfa_ticket(user["id"])
        await log_audit(
            action="auth.mfa_challenge_issued",
            actor_id=user["id"],
            actor_email=user["email"],
            actor_role=user["role"],
            request=request,
        )
        return {
            "user": None,
            "mfa_required": True,
            "mfa_ticket": ticket,
            "password_rotation_due": exp["rotation_due"],
        }

    # If MFA is REQUIRED by role but the user hasn't enrolled yet, issue a session
    # but the frontend will redirect to the MFA setup page. This keeps admins from
    # being locked out during initial rollout.
    await _finalise_login(db, user, response, request)
    return {
        "user": _to_public(user),
        "mfa_required": False,
        "password_rotation_due": exp["rotation_due"],
    }


async def _finalise_login(db, user: dict, response: Response, request: Request) -> None:
    await db.login_attempts.delete_one({"identifier": user["email"]})
    access = create_access_token(user["id"], user["email"], user["role"])
    refresh = create_refresh_token(user["id"])
    _set_auth_cookies(response, access, refresh)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"last_login_at": datetime.now(timezone.utc).isoformat()}},
    )
    await log_audit(
        action="auth.login",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
    )


@router.post("/mfa/challenge", response_model=UserPublic)
async def mfa_challenge(payload: MfaChallenge, request: Request, response: Response):
    db = get_db()
    try:
        ticket = decode_mfa_ticket(payload.mfa_ticket)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired MFA ticket")
    user = await db.users.find_one({"id": ticket["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")

    ok = verify_code(user.get("mfa_secret", ""), payload.code)
    if not ok:
        # fallback to a backup code
        consumed = verify_backup_code(user.get("mfa_backup_codes") or [], payload.code)
        if consumed:
            await db.users.update_one(
                {"id": user["id"]}, {"$pull": {"mfa_backup_codes": consumed}}
            )
            ok = True
    if not ok:
        await audit_failure(
            action="auth.mfa_verify", request=request, actor_email=user["email"],
            reason="bad_code",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    await _finalise_login(db, user, response, request)
    await log_audit(
        action="auth.mfa_verified",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
    )
    return _to_public(user)


# ---------------- Other auth endpoints ----------------

@router.post("/logout")
async def logout(request: Request, response: Response):
    # best-effort: figure out who is logging out
    user_id = None
    token = request.cookies.get("access_token")
    if token:
        try:
            user_id = decode_token(token).get("sub")
        except Exception:
            pass
    _clear_auth_cookies(response)
    await log_audit(
        action="auth.logout",
        actor_id=user_id,
        actor_email=None,
        actor_role=None,
        request=request,
    )
    return {"message": "Logged out"}


@router.get("/me", response_model=UserPublic)
async def me(user: dict = Depends(get_current_user)):
    return _to_public(user)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")

    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user or user.get("status") == "disabled":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not available")
    access = create_access_token(user["id"], user["email"], user["role"])
    response.set_cookie(
        "access_token", access, **_cookie_kwargs(ACCESS_TOKEN_MINUTES * 60)
    )
    return {"message": "Refreshed"}


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not verify_password(payload.current_password, full["password_hash"]):
        await audit_failure(
            action="auth.password_change", request=request,
            actor_email=user["email"], reason="wrong_current_password",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    try:
        validate_strength(payload.new_password)
        reject_password_reuse(payload.new_password, full.get("password_history") or [])
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    new_hash = hash_password(payload.new_password)
    history = (full.get("password_history") or [])[-PASSWORD_HISTORY + 1:] + [new_hash]
    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_history": history,
                "password_changed_at": now_iso,
                "updated_at": now_iso,
            }
        },
    )
    await log_audit(
        action="auth.password_changed",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
    )
    return {"message": "Password updated"}


@router.post("/reauth")
async def reauth(
    payload: ReauthRequest,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not verify_password(payload.password, full["password_hash"]):
        await audit_failure(
            action="auth.reauth", request=request, actor_email=user["email"],
            reason="wrong_password",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    token = create_reauth_token(user["id"])
    response.set_cookie("reauth_token", token, **_cookie_kwargs(5 * 60))
    await log_audit(
        action="auth.reauth",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
    )
    return {"reauth_token": token}


# ---------------- MFA enrolment ----------------

@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(request: Request, user: dict = Depends(get_current_user)):
    db = get_db()
    secret = generate_secret()
    codes = generate_backup_codes()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"mfa_pending_secret": secret, "mfa_pending_backup": codes}},
    )
    await log_audit(
        action="auth.mfa_setup_started",
        actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
        request=request,
    )
    return {
        "secret": secret,
        "otpauth_url": provisioning_uri(secret, user["email"]),
        "backup_codes": codes,
    }


@router.post("/mfa/verify")
async def mfa_verify(
    payload: MfaVerify,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    pending = full.get("mfa_pending_secret") if full else None
    if not pending:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start MFA setup first")
    if not verify_code(pending, payload.code):
        await audit_failure(
            action="auth.mfa_enable", request=request, actor_email=user["email"],
            reason="bad_code",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "mfa_enabled": True,
                "mfa_secret": pending,
                "mfa_backup_codes": full.get("mfa_pending_backup") or [],
            },
            "$unset": {"mfa_pending_secret": "", "mfa_pending_backup": ""},
        },
    )
    await log_audit(
        action="auth.mfa_enabled",
        actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
        request=request,
    )
    return {"message": "MFA enabled"}


@router.post("/mfa/disable")
async def mfa_disable(
    payload: ReauthRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Requires current password (step-down)."""
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not verify_password(payload.password, full["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {"mfa_enabled": False},
            "$unset": {"mfa_secret": "", "mfa_backup_codes": ""},
        },
    )
    await log_audit(
        action="auth.mfa_disabled",
        actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
        request=request,
    )
    return {"message": "MFA disabled"}


# ---------------- Admin user management ----------------

@router.get("/users", response_model=list[UserPublic])
async def list_users(
    request: Request,
    role: str | None = None,
    include_disabled: bool = False,
    admin: dict = Depends(require_role("admin")),
):
    db = get_db()
    q: dict = {}
    if role:
        q["role"] = role
    if not include_disabled:
        q["status"] = {"$ne": "disabled"}
    cursor = db.users.find(q, {"_id": 0, "password_hash": 0, "password_history": 0}).sort(
        "created_at", -1
    )
    rows = [_to_public(u) async for u in cursor]
    await audit_success(admin, "user.list_viewed", request, metadata={"count": len(rows)})
    return rows


@router.post("/users", response_model=UserPublic, status_code=201)
async def create_user(
    payload: AdminUserCreate,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    db = get_db()
    email = payload.email.lower().strip()
    try:
        validate_strength(payload.password)
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    if await db.users.find_one({"email": email}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    now = datetime.now(timezone.utc).isoformat()
    hashed = hash_password(payload.password)
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": hashed,
        "password_history": [hashed],
        "password_changed_at": now,
        "name": payload.name.strip(),
        "role": payload.role,
        "phone": payload.phone,
        "status": "active",
        "mfa_enabled": False,
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(doc)
    # Provider list cache may be stale if the new user is a doctor.
    await cache.invalidate_prefix(cache_keys.PREFIX_PROVIDERS)
    await audit_success(
        admin, "user.created", request,
        entity_type="user", entity_id=doc["id"],
        metadata={"email": email, "role": payload.role},
    )
    return _to_public(doc)


@router.post("/users/{user_id}/disable")
async def disable_user(
    user_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    if user_id == admin["id"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot disable yourself")
    db = get_db_write()
    result = await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": "disabled", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await cache.invalidate_prefix(cache_keys.PREFIX_PROVIDERS)
    await audit_success(
        admin, "user.disabled", request, entity_type="user", entity_id=user_id,
    )
    return {"message": "User disabled"}


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    payload: UserPatch,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Admin-only partial update for a user's role and/or status.

    Any change to `role` or `status` invalidates the providers cache so that
    a user entering or leaving the `doctor` role — or being disabled — is
    reflected immediately on the scheduling page."""
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
    if user_id == admin["id"] and "role" in updates and updates["role"] != "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot demote yourself")
    if user_id == admin["id"] and updates.get("status") == "disabled":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot disable yourself")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    db = get_db_write()
    result = await db.users.update_one({"id": user_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    # Any role or status change can affect the provider list — invalidate.
    await cache.invalidate_prefix(cache_keys.PREFIX_PROVIDERS)

    updated = await read_after_write_db().users.find_one({"id": user_id}, {"_id": 0})
    await audit_success(
        admin,
        "user.updated",
        request,
        entity_type="user",
        entity_id=user_id,
        metadata={"fields": [k for k in updates if k != "updated_at"]},
    )
    return _to_public(updated)


@router.post("/users/{user_id}/enable")
async def enable_user(
    user_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    db = get_db_write()
    result = await db.users.update_one(
        {"id": user_id},
        {"$set": {"status": "active", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await cache.invalidate_prefix(cache_keys.PREFIX_PROVIDERS)
    await audit_success(
        admin, "user.enabled", request, entity_type="user", entity_id=user_id,
    )
    return {"message": "User enabled"}


@router.get("/providers", response_model=list[UserPublic])
async def list_providers(_user: dict = Depends(get_current_user)):
    """Cached for 5 min. Provider list rarely changes; invalidated on user
    create / disable / enable. No PHI exposure (provider names + roles only)."""

    async def _fetch():
        db = get_db_read()
        cursor = db.users.find(
            {"role": "doctor", "status": {"$ne": "disabled"}},
            {"_id": 0, "password_hash": 0, "password_history": 0},
        ).sort("name", 1)
        return [_to_public(u) async for u in cursor]

    return await cache.get_or_set(cache_keys.PROVIDERS, 300, _fetch)
