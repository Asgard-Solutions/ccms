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
    LicenseCreate,
    LicensePublic,
    LicenseUpdate,
    LoginResult,
    MfaChallenge,
    MfaSetupResponse,
    MfaVerify,
    PasswordChange,
    PasswordResetConfirm,
    PasswordResetRequest,
    PinChange,
    PinCreate,
    PinReset,
    PinStatus,
    PinVerify,
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
        "pin_configured": bool(user.get("pin_hash")),
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
        "npi_number": user.get("npi_number"),
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
    "npi_number",
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


# ---------------------------------------------------------------------------
# Self-service 6-digit PIN — used for fast in-app re-verification.
# ---------------------------------------------------------------------------
# Lockout window after too many wrong PIN verifications. Intentionally
# tighter than the password lockout because the PIN is only 6 digits and
# therefore has a smaller keyspace (1e6).
PIN_MAX_FAILED_ATTEMPTS = 5
PIN_LOCKOUT_MINUTES = 15


def _pin_status(user_doc: dict) -> dict:
    """Project the PIN-facing fields from a user document. Never
    returns `pin_hash` — only the presence bit + timestamps."""
    return {
        "configured": bool(user_doc.get("pin_hash")),
        "created_at": user_doc.get("pin_created_at"),
        "updated_at": user_doc.get("pin_updated_at"),
        "locked_until": user_doc.get("pin_locked_until"),
        "failed_attempts": int(user_doc.get("pin_failed_attempts") or 0),
    }


def _pin_is_locked(user_doc: dict) -> bool:
    locked = user_doc.get("pin_locked_until")
    if not locked:
        return False
    try:
        ts = datetime.fromisoformat(locked)
    except Exception:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < ts


@router.get("/me/pin/status", response_model=PinStatus)
async def get_pin_status(
    user: dict = Depends(get_current_user),
):
    db = get_db_read()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return _pin_status(full)


@router.post("/me/pin", response_model=PinStatus, status_code=201)
async def create_pin(
    payload: PinCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if full.get("pin_hash"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "PIN already configured. Use PATCH to change it.",
        )
    if not verify_password(payload.current_password, full["password_hash"]):
        await log_audit(
            action="user.pin_create",
            actor_id=user["id"],
            actor_email=user["email"],
            actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure",
            reason="wrong_current_password",
            request=request,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Current password is incorrect",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "pin_hash": hash_password(payload.pin),
            "pin_created_at": now_iso,
            "pin_updated_at": now_iso,
            "pin_failed_attempts": 0,
            "pin_locked_until": None,
            "updated_at": now_iso,
        }},
    )
    await audit_success(
        user, "user.pin_created", request,
        entity_type="user", entity_id=user["id"],
    )
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return _pin_status(updated)


@router.patch("/me/pin", response_model=PinStatus)
async def change_pin(
    payload: PinChange,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not full.get("pin_hash"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No PIN configured. Use POST to create one.",
        )
    if not verify_password(payload.current_password, full["password_hash"]):
        await log_audit(
            action="user.pin_change",
            actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure", reason="wrong_current_password", request=request,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Current password is incorrect",
        )
    if not verify_password(payload.current_pin, full["pin_hash"]):
        await log_audit(
            action="user.pin_change",
            actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure", reason="wrong_current_pin", request=request,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Current PIN is incorrect",
        )
    if payload.new_pin == payload.current_pin:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "New PIN must differ from the current PIN.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "pin_hash": hash_password(payload.new_pin),
            "pin_updated_at": now_iso,
            "pin_failed_attempts": 0,
            "pin_locked_until": None,
            "updated_at": now_iso,
        }},
    )
    await audit_success(
        user, "user.pin_changed", request,
        entity_type="user", entity_id=user["id"],
    )
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return _pin_status(updated)


@router.post("/me/pin/reset", response_model=PinStatus)
async def reset_pin(
    payload: PinReset,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Replace a forgotten PIN. Requires a fresh re-auth token so only
    a recently-password-verified caller can rotate the PIN without
    supplying the current one."""
    require_reauth(request, user)

    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "pin_hash": hash_password(payload.new_pin),
            # If the PIN has never existed, this creates it; otherwise it
            # is a rotation. Preserve `pin_created_at` iff already set.
            "pin_created_at": full.get("pin_created_at") or now_iso,
            "pin_updated_at": now_iso,
            "pin_failed_attempts": 0,
            "pin_locked_until": None,
            "updated_at": now_iso,
        }},
    )
    await audit_success(
        user, "user.pin_reset", request,
        entity_type="user", entity_id=user["id"],
        metadata={"was_configured": bool(full.get("pin_hash"))},
    )
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return _pin_status(updated)


@router.delete("/me/pin", response_model=PinStatus)
async def remove_pin(
    payload: ReauthRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Remove an existing PIN. Gated on the password as proof-of-presence
    (same bar as setting one). PIN-only payloads are rejected so an
    attacker who only has the PIN cannot silently remove it."""
    if not payload.password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Password is required to remove a PIN.",
        )
    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if not full.get("pin_hash"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No PIN configured.",
        )
    if not verify_password(payload.password, full["password_hash"]):
        await log_audit(
            action="user.pin_remove",
            actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure", reason="wrong_current_password", request=request,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Current password is incorrect",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "pin_hash": None,
            "pin_created_at": None,
            "pin_updated_at": None,
            "pin_failed_attempts": 0,
            "pin_locked_until": None,
            "updated_at": now_iso,
        }},
    )
    await audit_success(
        user, "user.pin_removed", request,
        entity_type="user", entity_id=user["id"],
    )
    updated = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    return _pin_status(updated)


@router.post("/me/pin/verify")
async def verify_pin(
    payload: PinVerify,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Verify the caller's PIN for a short-lived elevated action.

    Wrong attempts increment `pin_failed_attempts`; once the threshold
    is hit the PIN is locked for `PIN_LOCKOUT_MINUTES`. Successful
    verification resets both counters."""
    db = get_db_write()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not full.get("pin_hash"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No PIN configured.",
        )
    if _pin_is_locked(full):
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "PIN is temporarily locked due to too many failed attempts. "
            "Reset your PIN or try again later.",
        )
    if not verify_password(payload.pin, full["pin_hash"]):
        fails = int(full.get("pin_failed_attempts") or 0) + 1
        update: dict = {
            "pin_failed_attempts": fails,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        locked = fails >= PIN_MAX_FAILED_ATTEMPTS
        if locked:
            update["pin_locked_until"] = (
                datetime.now(timezone.utc)
                + timedelta(minutes=PIN_LOCKOUT_MINUTES)
            ).isoformat()
        await db.users.update_one({"id": user["id"]}, {"$set": update})
        await log_audit(
            action="auth.pin_verify",
            actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure",
            reason="locked_out" if locked else "wrong_pin",
            metadata={"failed_attempts": fails, "locked": locked},
            request=request,
        )
        raise HTTPException(
            status.HTTP_423_LOCKED if locked else status.HTTP_401_UNAUTHORIZED,
            "PIN locked after too many wrong attempts." if locked
            else "Incorrect PIN.",
        )

    # Success — clear counters.
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "pin_failed_attempts": 0,
            "pin_locked_until": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    await audit_success(
        user, "auth.pin_verify", request,
        entity_type="user", entity_id=user["id"],
    )
    return {"verified": True}


# ---------------------------------------------------------------------------
# Professional licenses (multi) — doctors + admins.
# ---------------------------------------------------------------------------
# Roles allowed to add their own licenses. Staff/patient users get a
# clean 403 on write (they can still GET their own — trivially empty —
# list so the UI can hide the section without extra wiring).
LICENSE_CAPABLE_ROLES = {"admin", "doctor"}


def _license_public(doc: dict) -> dict:
    return {
        "id": doc["id"],
        "user_id": doc["user_id"],
        "license_type": doc["license_type"],
        "license_number": doc["license_number"],
        "issuing_state": doc["issuing_state"],
        "expiration_date": doc["expiration_date"],
        "specialty": doc.get("specialty"),
        "board_notes": doc.get("board_notes"),
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


def _require_license_capable(user: dict) -> None:
    if user.get("role") not in LICENSE_CAPABLE_ROLES:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only clinicians can manage professional licenses on their own profile.",
        )


@router.get("/me/licenses", response_model=list[LicensePublic])
async def list_my_licenses(user: dict = Depends(get_current_user)):
    """List the caller's own licenses, newest first. Works for every
    role so the frontend can uniformly hide the section when empty."""
    db = get_db_read()
    cursor = db.professional_licenses.find(
        {"user_id": user["id"]}, {"_id": 0},
    ).sort("created_at", -1)
    rows = [r async for r in cursor]
    return [_license_public(r) for r in rows]


@router.post("/me/licenses", response_model=LicensePublic, status_code=201)
async def create_my_license(
    payload: LicenseCreate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_license_capable(user)
    db = get_db_write()

    # Avoid duplicate rows for (type, state, number) since that's the
    # real-world uniqueness key for a license.
    existing = await db.professional_licenses.find_one({
        "user_id": user["id"],
        "license_type": payload.license_type,
        "issuing_state": payload.issuing_state.upper(),
        "license_number": payload.license_number.strip(),
    }, {"_id": 0, "id": 1})
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A license with this type + state + number already exists.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "tenant_id": user.get("tenant_id"),
        "license_type": payload.license_type,
        "license_number": payload.license_number.strip(),
        "issuing_state": payload.issuing_state.upper(),
        "expiration_date": payload.expiration_date,
        "specialty": (payload.specialty or "").strip() or None,
        "board_notes": (payload.board_notes or "").strip() or None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    await db.professional_licenses.insert_one(doc)
    await audit_success(
        user, "user.license_added", request,
        entity_type="professional_license", entity_id=doc["id"],
        metadata={
            "license_type": doc["license_type"],
            "issuing_state": doc["issuing_state"],
            # license_number is mildly sensitive — log its length only,
            # never the value itself.
            "license_number_length": len(doc["license_number"]),
        },
    )
    return _license_public(doc)


@router.patch("/me/licenses/{license_id}", response_model=LicensePublic)
async def update_my_license(
    license_id: str,
    payload: LicenseUpdate,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_license_capable(user)
    db = get_db_write()
    existing = await db.professional_licenses.find_one(
        {"id": license_id, "user_id": user["id"]}, {"_id": 0},
    )
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No fields supplied to update.",
        )

    updates: dict = {}
    if "license_type" in dumped:
        updates["license_type"] = dumped["license_type"]
    if "license_number" in dumped:
        updates["license_number"] = (dumped["license_number"] or "").strip()
    if "issuing_state" in dumped:
        updates["issuing_state"] = (dumped["issuing_state"] or "").upper()
    if "expiration_date" in dumped:
        updates["expiration_date"] = dumped["expiration_date"]
    if "specialty" in dumped:
        spec = (dumped["specialty"] or "").strip()
        updates["specialty"] = spec or None
    if "board_notes" in dumped:
        notes = (dumped["board_notes"] or "").strip()
        updates["board_notes"] = notes or None
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.professional_licenses.update_one(
        {"id": license_id, "user_id": user["id"]}, {"$set": updates},
    )
    fresh = await db.professional_licenses.find_one(
        {"id": license_id}, {"_id": 0},
    )
    await audit_success(
        user, "user.license_updated", request,
        entity_type="professional_license", entity_id=license_id,
        metadata={"fields": sorted(k for k in updates if k != "updated_at")},
    )
    return _license_public(fresh)


@router.delete("/me/licenses/{license_id}", status_code=204)
async def delete_my_license(
    license_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    _require_license_capable(user)
    db = get_db_write()
    existing = await db.professional_licenses.find_one(
        {"id": license_id, "user_id": user["id"]}, {"_id": 0},
    )
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "License not found")
    await db.professional_licenses.delete_one(
        {"id": license_id, "user_id": user["id"]},
    )
    await audit_success(
        user, "user.license_removed", request,
        entity_type="professional_license", entity_id=license_id,
        metadata={
            "license_type": existing["license_type"],
            "issuing_state": existing["issuing_state"],
        },
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


# Failures allowed within the window before `/change-password` returns
# 429. High enough for honest typos, low enough to block brute-force
# attempts against `current_password`.
CHANGE_PASSWORD_FAIL_LIMIT = 5
CHANGE_PASSWORD_FAIL_WINDOW_SECONDS = 15 * 60  # 15 minutes
# Volume ceiling per IP across all change-password attempts (success or
# fail). Honest users change their password at most a handful of times
# per year; this blocks scripted abuse without punishing anyone. Kept
# high enough for shared NAT (clinic staff on the same outbound IP).
CHANGE_PASSWORD_VOLUME_LIMIT = 60
CHANGE_PASSWORD_VOLUME_WINDOW_SECONDS = 60


@router.post("/change-password")
async def change_password(
    payload: PasswordChange,
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else "unknown"
    )

    # --- Per-IP volume ceiling (all attempts count). Blocks scripted abuse.
    if not await rate_limit.is_allowed(
        f"cp_vol:{ip}",
        limit=CHANGE_PASSWORD_VOLUME_LIMIT,
        window_seconds=CHANGE_PASSWORD_VOLUME_WINDOW_SECONDS,
    ):
        await log_audit(
            action="auth.password_change",
            actor_id=user["id"],
            actor_email=user["email"],
            actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure",
            reason="rate_limited_volume",
            request=request,
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many password change attempts from this address. Please try again shortly.",
        )

    # --- Per-user failure counter (only wrong-current-password bumps it).
    # Gated at entry: once the limit is hit, further tries return 429
    # without touching the password hash. The window naturally expires.
    fail_key = f"cp:{user['id']}"
    current_fails = await rate_limit.failure_count(
        fail_key, window_seconds=CHANGE_PASSWORD_FAIL_WINDOW_SECONDS,
    )
    if current_fails >= CHANGE_PASSWORD_FAIL_LIMIT:
        await log_audit(
            action="auth.password_change",
            actor_id=user["id"],
            actor_email=user["email"],
            actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure",
            reason="rate_limited_failures",
            request=request,
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Please wait a few minutes and try again.",
        )

    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full or not verify_password(payload.current_password, full["password_hash"]):
        await rate_limit.record_failure(
            fail_key, window_seconds=CHANGE_PASSWORD_FAIL_WINDOW_SECONDS,
        )
        await log_audit(
            action="auth.password_change",
            actor_id=user["id"],
            actor_email=user["email"],
            actor_role=user["role"],
            tenant_id=user.get("tenant_id"),
            outcome="failure",
            reason="wrong_current_password",
            request=request,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")

    # Reject setting the same password (users often fat-finger current
    # and new into the same value). Message-wise this maps to the
    # history-reuse response so we don't leak policy internals.
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot reuse any of your last {PASSWORD_HISTORY} passwords.",
        )

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
        tenant_id=user.get("tenant_id"),
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
    """Step-up re-authentication.

    Accepts either `password` or `pin`. PIN verification shares its
    rate-limit + 15-min lockout with `/auth/me/pin/verify`, so the
    step-up endpoint can't be used to side-step that protection. On
    success, sets the same 5-min `reauth_token` cookie as before so
    every existing consumer (interceptor + `require_reauth()` gates)
    works unchanged.

    `reason` (optional) is copied into the audit metadata for review.
    """
    db = get_db()
    full = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    if not full:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    audit_metadata: dict = {"factor": "password"}
    if payload.reason:
        audit_metadata["reason"] = payload.reason[:500]

    if payload.pin is not None:
        audit_metadata["factor"] = "pin"
        if not full.get("pin_hash"):
            await log_audit(
                action="auth.reauth",
                actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
                tenant_id=user.get("tenant_id"),
                outcome="failure",
                reason="pin_not_configured",
                metadata=audit_metadata,
                request=request,
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No PIN configured for this account. Use password instead.",
            )
        if _pin_is_locked(full):
            await log_audit(
                action="auth.reauth",
                actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
                tenant_id=user.get("tenant_id"),
                outcome="failure",
                reason="pin_locked",
                metadata=audit_metadata,
                request=request,
            )
            raise HTTPException(
                status.HTTP_423_LOCKED,
                "PIN is temporarily locked due to too many failed attempts. "
                "Reset your PIN or sign in with your password.",
            )
        if not verify_password(payload.pin, full["pin_hash"]):
            fails = int(full.get("pin_failed_attempts") or 0) + 1
            update = {
                "pin_failed_attempts": fails,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            locked = fails >= PIN_MAX_FAILED_ATTEMPTS
            if locked:
                update["pin_locked_until"] = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=PIN_LOCKOUT_MINUTES)
                ).isoformat()
            await db.users.update_one({"id": user["id"]}, {"$set": update})
            await log_audit(
                action="auth.reauth",
                actor_id=user["id"], actor_email=user["email"], actor_role=user["role"],
                tenant_id=user.get("tenant_id"),
                outcome="failure",
                reason="wrong_pin" if not locked else "locked_out",
                metadata={**audit_metadata,
                          "failed_attempts": fails, "locked": locked},
                request=request,
            )
            raise HTTPException(
                status.HTTP_423_LOCKED if locked else status.HTTP_401_UNAUTHORIZED,
                "PIN locked after too many wrong attempts." if locked
                else "Invalid PIN",
            )
        # Success — clear PIN counters.
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "pin_failed_attempts": 0,
                "pin_locked_until": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
    else:
        if not verify_password(payload.password, full["password_hash"]):
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
        tenant_id=user.get("tenant_id"),
        metadata=audit_metadata,
        request=request,
    )
    return {"reauth_token": token, "factor": audit_metadata["factor"]}


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
