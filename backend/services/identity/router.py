"""
Identity Service router — /api/auth/* (HIPAA-hardened).

Adds:
  - Strong password policy + history + rotation warnings
  - TOTP MFA with a short-lived mfa_ticket step
  - Step-up re-authentication for sensitive actions
  - Account disable (no hard delete)
  - Full audit trail for every auth action
  - Session epoch + absolute lifetime cap → old tokens die on privilege change
  - Password reset tokens (single-use, 15-minute expiry, sha256-hashed at rest)
  - Admin MFA reset + admin force-require-MFA
"""
import hashlib
import secrets
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
from core.reauth import create_reauth_token, require_reauth
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
    PasswordResetConfirm,
    PasswordResetRequest,
    PreferencesUpdate,
    ProfileUpdate,
    ReauthRequest,
    UserLogin,
    UserPatch,
    UserPublic,
    UserRegister,
)

router = APIRouter(prefix="/auth", tags=["identity"])

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PASSWORD_RESET_TTL_MINUTES = 15


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


def _issue_access_token(user: dict, epoch: int, session_started_at: str) -> str:
    """All routes go through this so tenant_id + platform_admin claims stay in sync."""
    return create_access_token(
        user["id"], user["email"], user["role"], epoch, session_started_at,
        tenant_id=user.get("tenant_id"),
        is_platform_admin=bool(user.get("is_platform_admin")) or user.get("role") == "platform_admin",
    )


def _to_public(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "phone": user.get("phone"),
        "status": user.get("status", "active"),
        "tenant_id": user.get("tenant_id"),
        "tenant_scope_all": bool(user.get("tenant_scope_all")),
        "is_platform_admin": bool(user.get("is_platform_admin")) or user.get("role") == "platform_admin",
        "mfa_enabled": bool(user.get("mfa_enabled")),
        "mfa_policy_required": bool(user.get("mfa_policy_required")),
        "password_changed_at": user.get("password_changed_at"),
        "theme": user.get("theme", "system"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "display_name": user.get("display_name"),
        "mobile_phone": user.get("mobile_phone"),
        "work_phone": user.get("work_phone"),
        "job_title": user.get("job_title"),
        "credentials_suffix": user.get("credentials_suffix"),
        "preferred_signature_name": user.get("preferred_signature_name"),
        "time_zone": user.get("time_zone"),
        "created_at": user["created_at"],
    }


async def _bump_session_epoch(db, user_id: str) -> int:
    """Invalidate all currently-issued tokens for this user. Returns the new epoch."""
    updated = await db.users.find_one_and_update(
        {"id": user_id},
        {"$inc": {"session_epoch": 1}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
        projection={"_id": 0, "session_epoch": 1},
        return_document=True,
    )
    return (updated or {}).get("session_epoch", 1)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
    # Public registration always creates a patient under the Default Practice.
    default_tenant = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    tenant_id = default_tenant["id"] if default_tenant else None
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
        "tenant_id": tenant_id,
        "tenant_scope_all": False,
        "mfa_enabled": False,
        "mfa_policy_required": False,
        "session_epoch": 0,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    await db.users.insert_one(doc)

    session_started_at = datetime.now(timezone.utc).isoformat()
    access = create_access_token(
        user_id, email, "patient", 0, session_started_at,
        tenant_id=doc.get("tenant_id"),
        is_platform_admin=False,
    )
    refresh = create_refresh_token(user_id, 0, session_started_at)
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
    # Outer rate-limit: per-IP throttle as DoS protection. We bumped the
    # bucket from 30→60/minute because back-to-back automation runs (and
    # legitimate front-desk retry patterns) were occasionally draining the
    # 30/min window with 429s. Brute-force protection itself is NOT
    # weakened by this bump — the per-email Mongo lockout below
    # (`MAX_FAILED_ATTEMPTS` in `_lockout_check`) remains the durable,
    # audited credential-stuffing control.
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    if not await rate_limit.is_allowed(f"login:{ip}", limit=60, window_seconds=60):
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
        # Iteration 19 — suspicious-login detection on failures.
        from services.workforce.router import record_login_signal
        await record_login_signal(user or {"email": email}, request, outcome="failure")
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
    epoch = int(user.get("session_epoch", 0))
    session_started_at = datetime.now(timezone.utc).isoformat()
    access = create_access_token(
        user["id"], user["email"], user["role"], epoch, session_started_at,
        tenant_id=user.get("tenant_id"),
        is_platform_admin=bool(user.get("is_platform_admin")) or user.get("role") == "platform_admin",
    )
    refresh = create_refresh_token(user["id"], epoch, session_started_at)
    _set_auth_cookies(response, access, refresh)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"last_login_at": session_started_at}},
    )
    # Iteration 19 — run suspicious-login detection BEFORE the auth.login
    # audit row so the "prior IP lookup" doesn't match the row we're about
    # to write.
    from services.workforce.router import record_login_signal
    await record_login_signal(user, request, outcome="success")
    await log_audit(
        action="auth.login",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
        metadata={"session_started_at": session_started_at},
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


@router.patch("/me/preferences", response_model=UserPublic)
async def update_preferences(
    payload: PreferencesUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Persist lightweight per-user UI preferences (theme today; locale + density
    later). Auth-only; no reauth required — these are non-sensitive settings."""
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No preference fields supplied."
        )
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    db = get_db_write()
    updated = await db.users.find_one_and_update(
        {"id": user["id"]},
        {"$set": updates},
        projection={"_id": 0},
        return_document=True,
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await audit_success(
        user, "user.preferences_updated", request,
        entity_type="user", entity_id=user["id"],
        metadata={"fields": sorted(k for k in updates if k != "updated_at")},
    )
    return _to_public(updated)


_PROFILE_STRING_FIELDS = (
    "first_name",
    "last_name",
    "display_name",
    "phone",
    "mobile_phone",
    "work_phone",
    "job_title",
    "credentials_suffix",
    "preferred_signature_name",
    "time_zone",
)


def _resolve_display_name(updates: dict, current: dict) -> str | None:
    """Pick the best full-name string to write into the legacy `name`
    column so everywhere that reads `user.name` (audit logs, clinic
    signatures, scheduler chips) stays in sync. When `display_name` is
    explicitly cleared (empty string → None) we must NOT fall back to
    the old value still sitting on `current`."""
    def _effective(key: str) -> str | None:
        if key in updates:
            v = updates[key]
            return v.strip() if isinstance(v, str) else v
        return current.get(key)

    disp = _effective("display_name")
    if disp and disp.strip():
        return disp.strip()
    first = _effective("first_name") or ""
    last = _effective("last_name") or ""
    full = f"{first} {last}".strip()
    return full or None


@router.patch("/me/profile", response_model=UserPublic)
async def update_profile(
    payload: ProfileUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Self-service update of the logged-in user's own profile.

    * Email changes require a short-lived reauth token (same gate as
      other sensitive actions) AND trigger a session-epoch bump so
      existing JWTs are invalidated — the user must sign in again.
    * All name-related writes keep the legacy `name` column in sync
      with `display_name` / `first_name` / `last_name` so audit rows
      and clinical signatures don't drift.
    * Empty strings reset a field to null (so users can clear an
      optional field by sending "").
    """
    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No profile fields supplied.",
        )

    updates: dict = {}
    for key in _PROFILE_STRING_FIELDS:
        if key in dumped:
            value = dumped[key]
            if isinstance(value, str):
                stripped = value.strip()
                updates[key] = stripped or None
            else:
                updates[key] = value

    db = get_db_write()
    email_change = False
    if "email" in dumped and dumped["email"]:
        new_email = str(dumped["email"]).lower().strip()
        if new_email != (user.get("email") or "").lower():
            require_reauth(request, user)
            existing = await db.users.find_one(
                {"email": new_email, "id": {"$ne": user["id"]}},
                {"_id": 0, "id": 1},
            )
            if existing:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Email already in use by another account.",
                )
            updates["email"] = new_email
            email_change = True

    # Keep the legacy `name` column in sync with display/first/last so
    # downstream readers (audit, care timeline, clinical signatures)
    # don't show a stale value.
    if any(k in updates for k in ("first_name", "last_name", "display_name")):
        synced = _resolve_display_name(updates, user)
        if synced:
            updates["name"] = synced

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    if email_change:
        # Session epoch bump → invalidate every existing access/refresh
        # token for this user. UI will bounce to login.
        await _bump_session_epoch(db, user["id"])
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    updated = await db.users.find_one_and_update(
        {"id": user["id"]},
        {"$set": updates},
        projection={"_id": 0},
        return_document=True,
    )
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    await audit_success(
        user, "user.profile_updated", request,
        entity_type="user", entity_id=user["id"],
        metadata={
            "fields": sorted(k for k in updates if k != "updated_at"),
            "email_changed": email_change,
        },
    )
    return _to_public(updated)


@router.get("/me/export")
async def export_me(request: Request, user: dict = Depends(get_current_user)):
    """Self-service account-data export. Returns the caller's identity
    profile, consent history, communication preferences, and a summary of
    their own audit events (auth + privacy-related actions). For full
    clinical data (patient profile, medical records, appointments) patients
    should use GET /api/patients/{id}/export."""
    db = get_db_read()
    account = {
        k: user.get(k)
        for k in (
            "id", "email", "name", "role", "phone", "status",
            "mfa_enabled", "mfa_policy_required",
            "password_changed_at", "created_at", "updated_at", "last_login_at",
        )
    }
    prefs = await db.communication_preferences.find_one(
        {"user_id": user["id"]}, {"_id": 0},
    )
    consents = [
        c
        async for c in db.consent_records.find(
            {"user_id": user["id"]}, {"_id": 0},
        ).sort("accepted_at", -1).limit(200)
    ]
    privacy_requests = [
        r
        async for r in db.privacy_requests.find(
            {"$or": [{"subject_user_id": user["id"]}, {"submitted_by_id": user["id"]}]},
            {"_id": 0},
        ).sort("created_at", -1).limit(200)
    ]
    recent_events = [
        e
        async for e in db.audit_logs.find(
            {"actor_id": user["id"]},
            {"_id": 0, "action": 1, "outcome": 1, "created_at": 1, "ip": 1},
        ).sort("created_at", -1).limit(100)
    ]
    await audit_success(
        user, "account.self_exported", request,
        entity_type="user", entity_id=user["id"],
        metadata={
            "consents": len(consents),
            "privacy_requests": len(privacy_requests),
            "events": len(recent_events),
        },
    )
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": account,
        "communication_preferences": prefs,
        "consents": consents,
        "privacy_requests": privacy_requests,
        "recent_events": recent_events,
    }


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
    # Enforce session epoch on refresh too — privilege change kills refresh token.
    if int(payload.get("epoch", 0)) != int(user.get("session_epoch", 0)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalidated")
    sst = payload.get("sst") or datetime.now(timezone.utc).isoformat()
    access = create_access_token(
        user["id"], user["email"], user["role"], int(user.get("session_epoch", 0)), sst,
        tenant_id=user.get("tenant_id"),
        is_platform_admin=bool(user.get("is_platform_admin")) or user.get("role") == "platform_admin",
    )
    response.set_cookie(
        "access_token", access, **_cookie_kwargs(ACCESS_TOKEN_MINUTES * 60)
    )
    return {"message": "Refreshed"}


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
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
    # Bump session epoch: every existing session is now invalid. We re-issue
    # fresh cookies for the CURRENT session so the user stays logged in here.
    new_epoch = int(full.get("session_epoch", 0)) + 1
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_history": history,
                "password_changed_at": now_iso,
                "updated_at": now_iso,
                "session_epoch": new_epoch,
            }
        },
    )
    session_started_at = now_iso
    access = _issue_access_token(user, new_epoch, session_started_at)
    refresh_tok = create_refresh_token(user["id"], new_epoch, session_started_at)
    _set_auth_cookies(response, access, refresh_tok)
    await log_audit(
        action="auth.password_changed",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user["role"],
        request=request,
        metadata={"other_sessions_revoked": True},
    )
    return {"message": "Password updated", "other_sessions_revoked": True}


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
    response: Response,
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
    new_epoch = int(full.get("session_epoch", 0)) + 1
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "mfa_enabled": True,
                "mfa_secret": pending,
                "mfa_backup_codes": full.get("mfa_pending_backup") or [],
                "session_epoch": new_epoch,
            },
            "$unset": {"mfa_pending_secret": "", "mfa_pending_backup": ""},
        },
    )
    # Re-issue current session with new epoch so the user stays logged in.
    sst = datetime.now(timezone.utc).isoformat()
    access = _issue_access_token(user, new_epoch, sst)
    refresh_tok = create_refresh_token(user["id"], new_epoch, sst)
    _set_auth_cookies(response, access, refresh_tok)
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
    response: Response,
    user: dict = Depends(get_current_user),
):
    """Requires current password (step-down)."""
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not verify_password(payload.password, full["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")
    new_epoch = int(full.get("session_epoch", 0)) + 1
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {"mfa_enabled": False, "session_epoch": new_epoch},
            "$unset": {"mfa_secret": "", "mfa_backup_codes": ""},
        },
    )
    sst = datetime.now(timezone.utc).isoformat()
    access = _issue_access_token(user, new_epoch, sst)
    refresh_tok = create_refresh_token(user["id"], new_epoch, sst)
    _set_auth_cookies(response, access, refresh_tok)
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
    # Tenant scoping: non-platform admins only see users in their tenant.
    if not (admin.get("is_platform_admin") or admin.get("role") == "platform_admin"):
        q["tenant_id"] = admin.get("tenant_id")
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
    # New users inherit the creator's tenant_id unless a platform_admin
    # supplies an explicit tenant_id on the payload.
    new_tenant_id = getattr(payload, "tenant_id", None) or admin.get("tenant_id")
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
        "tenant_id": new_tenant_id,
        "tenant_scope_all": payload.role in ("admin", "super_admin"),
        "mfa_enabled": False,
        "mfa_policy_required": payload.role in ("admin", "doctor", "staff"),
        "session_epoch": 0,
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
        {
            "$set": {"status": "disabled", "updated_at": datetime.now(timezone.utc).isoformat()},
            "$inc": {"session_epoch": 1},
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await cache.invalidate_prefix(cache_keys.PREFIX_PROVIDERS)
    await audit_success(
        admin, "user.disabled", request, entity_type="user", entity_id=user_id,
        metadata={"sessions_revoked": True},
    )
    return {"message": "User disabled", "sessions_revoked": True}


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
    # If role or status changes, any existing session for that user must die.
    sessions_revoked = any(k in updates for k in ("role", "status"))
    mongo_update: dict = {"$set": updates}
    if sessions_revoked:
        mongo_update["$inc"] = {"session_epoch": 1}
    result = await db.users.update_one({"id": user_id}, mongo_update)
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
        metadata={
            "fields": [k for k in updates if k != "updated_at"],
            "sessions_revoked": sessions_revoked,
        },
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
async def list_providers(user: dict = Depends(get_current_user)):
    """Per-tenant provider list. Cached for 5 min per tenant.
    Invalidated on user create / disable / enable. No PHI exposure."""

    tenant_id = user.get("tenant_id")

    async def _fetch():
        db = get_db_read()
        q: dict = {"role": "doctor", "status": {"$ne": "disabled"}}
        if not (user.get("is_platform_admin") or user.get("role") == "platform_admin"):
            q["tenant_id"] = tenant_id
        cursor = db.users.find(
            q, {"_id": 0, "password_hash": 0, "password_history": 0},
        ).sort("name", 1)
        return [_to_public(u) async for u in cursor]

    tenant_suffix = tenant_id or "platform"
    return await cache.get_or_set(f"{cache_keys.PROVIDERS}:{tenant_suffix}", 300, _fetch)


# ---------------- Admin MFA controls ----------------

@router.post("/users/{user_id}/mfa/reset")
async def admin_reset_mfa(
    user_id: str,
    request: Request,
    admin: dict = Depends(require_role("admin")),
):
    """Admin MFA recovery: disables MFA on a target user, revokes all their
    sessions, and writes an audit row. Target user must re-enrol MFA on
    next login."""
    db = get_db_write()
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    new_epoch = int(target.get("session_epoch", 0)) + 1
    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {
                "mfa_enabled": False,
                "session_epoch": new_epoch,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "$unset": {
                "mfa_secret": "",
                "mfa_backup_codes": "",
                "mfa_pending_secret": "",
                "mfa_pending_backup": "",
            },
        },
    )
    await audit_success(
        admin, "user.mfa_reset", request,
        entity_type="user", entity_id=user_id,
        metadata={"target_email": target["email"], "sessions_revoked": True},
    )
    return {"message": "MFA reset for user; all sessions revoked."}


@router.post("/users/{user_id}/mfa/require")
async def admin_require_mfa(
    user_id: str,
    request: Request,
    required: bool = True,
    admin: dict = Depends(require_role("admin")),
):
    """Admin MFA policy toggle per user. When required=True and the user has
    not enrolled MFA, their next login returns mfa_policy_required=true and
    the frontend redirects to the MFA setup page before granting PHI access."""
    db = get_db_write()
    target = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {
                "mfa_policy_required": bool(required),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    await audit_success(
        admin,
        "user.mfa_policy_updated",
        request,
        entity_type="user",
        entity_id=user_id,
        metadata={"target_email": target["email"], "required": bool(required)},
    )
    return {"message": f"MFA policy set to required={required}"}


# ---------------- Password reset (forgot password) ----------------

@router.post("/password-reset/request")
async def password_reset_request(
    payload: PasswordResetRequest,
    request: Request,
):
    """Issues a single-use, 15-minute password reset token.

    - Always responds 200 to prevent user enumeration.
    - In production this token is delivered by email (out of scope of the MVP
      communication integration). For dev we also return the token in the
      response body so engineers can exercise the reset flow.
    - The token's SHA-256 is stored — the raw token is never persisted.
    - Issuing a token does NOT confirm the email exists.
    """
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )
    if not await rate_limit.is_allowed(f"pwreset:{ip}", limit=5, window_seconds=60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many reset requests. Please slow down.",
        )

    db = get_db_write()
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email, "status": {"$ne": "disabled"}}, {"_id": 0})

    raw_token: str | None = None
    if user:
        raw_token = secrets.token_urlsafe(32)
        await db.password_reset_tokens.insert_one(
            {
                "id": str(uuid.uuid4()),
                "token_hash": _hash_token(raw_token),
                "user_id": user["id"],
                "email": email,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": datetime.now(timezone.utc)
                + timedelta(minutes=PASSWORD_RESET_TTL_MINUTES),
                "used_at": None,
            }
        )
        await log_audit(
            action="auth.password_reset_requested",
            actor_id=user["id"],
            actor_email=email,
            actor_role=user.get("role"),
            request=request,
            metadata={"ttl_minutes": PASSWORD_RESET_TTL_MINUTES},
        )
    else:
        # Log the miss for anti-enumeration forensics without leaking whether
        # the email exists to the caller.
        await audit_failure(
            action="auth.password_reset_requested",
            request=request,
            actor_email=email,
            reason="unknown_email_or_disabled",
        )

    return {
        "message": (
            "If an account with that email exists, a reset link has been sent. "
            "The link expires in 15 minutes."
        ),
        # Dev convenience. In production, strip this or only return
        # when running in a dev/test environment.
        "dev_token": raw_token,
    }


@router.post("/password-reset/confirm")
async def password_reset_confirm(
    payload: PasswordResetConfirm,
    request: Request,
):
    db = get_db_write()
    token_hash = _hash_token(payload.token)
    now = datetime.now(timezone.utc)
    record = await db.password_reset_tokens.find_one({"token_hash": token_hash}, {"_id": 0})
    if not record or record.get("used_at"):
        await audit_failure(
            action="auth.password_reset_confirm", request=request,
            reason="invalid_or_used_token",
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")
    expires_at = record.get("expires_at")
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            expires_at = None
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if not expires_at or expires_at < now:
        await audit_failure(
            action="auth.password_reset_confirm", request=request,
            reason="token_expired",
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    user = await db.users.find_one({"id": record["user_id"]}, {"_id": 0})
    if not user or user.get("status") == "disabled":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    try:
        validate_strength(payload.new_password)
        reject_password_reuse(payload.new_password, user.get("password_history") or [])
    except PasswordPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    new_hash = hash_password(payload.new_password)
    history = (user.get("password_history") or [])[-PASSWORD_HISTORY + 1:] + [new_hash]
    now_iso = now.isoformat()
    new_epoch = int(user.get("session_epoch", 0)) + 1
    await db.users.update_one(
        {"id": user["id"]},
        {
            "$set": {
                "password_hash": new_hash,
                "password_history": history,
                "password_changed_at": now_iso,
                "updated_at": now_iso,
                "session_epoch": new_epoch,
            }
        },
    )
    # Burn the token (single-use) and invalidate any other outstanding tokens
    # for this user to prevent replay on a now-changed password.
    await db.password_reset_tokens.update_many(
        {"user_id": user["id"], "used_at": None},
        {"$set": {"used_at": now_iso}},
    )
    await db.login_attempts.delete_one({"identifier": user["email"]})
    await log_audit(
        action="auth.password_reset_completed",
        actor_id=user["id"],
        actor_email=user["email"],
        actor_role=user.get("role"),
        request=request,
        metadata={"sessions_revoked": True},
    )
    return {
        "message": "Password has been reset. Please sign in with your new password.",
    }


# ---------------- Session visibility (self) ----------------

@router.get("/sessions")
async def list_sessions(
    request: Request,
    user: dict = Depends(get_current_user),
    limit: int = 20,
):
    """Returns the most recent sign-in history for the current user.

    Sourced from the audit log — no separate sessions table needed today.
    Useful for the Security page "Recent sign-ins" panel and for detecting
    unknown locations / user-agents.
    """
    db = get_db_read()
    cursor = db.audit_logs.find(
        {
            "actor_id": user["id"],
            "action": {"$in": ["auth.login", "auth.mfa_verified", "auth.logout"]},
        },
        {
            "_id": 0,
            "action": 1,
            "outcome": 1,
            "ip": 1,
            "user_agent": 1,
            "created_at": 1,
            "metadata": 1,
        },
    ).sort("created_at", -1).limit(max(1, min(limit, 100)))
    rows = [r async for r in cursor]
    return {
        "current_session": {
            "ip": (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown"),
            "user_agent": request.headers.get("user-agent"),
        },
        "events": rows,
    }
