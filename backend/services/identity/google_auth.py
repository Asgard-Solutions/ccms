"""Emergent-managed Google OAuth — staff-only sign-in.

Patients stay on phone-first SMS OTP. This module ONLY ever returns
JWTs for staff roles (admin / doctor / staff). New users are auto-
provisioned at `role=staff` (lowest staff privilege) when their email
domain is allowlisted on the tenant; otherwise the exchange endpoint
returns 403 and the front-end surfaces a "contact your administrator"
message.

Flow (mirrors `/app/auth_testing.md`):

  1. Frontend redirects the user to ``https://auth.emergentagent.com/?redirect=<our-callback-url>``.
  2. Emergent returns to ``<callback>#session_id=…``.
  3. Frontend POSTs the ``session_id`` to ``/api/auth/google/exchange``.
  4. We call Emergent's session-data endpoint server-side, then mint
     **our** JWT cookies and write an audit row.

REMINDER (frontend): DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR
REDIRECT URLS — `window.location.origin` is the source of truth.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from core.audit import audit_failure, audit_success
from core.db import get_db
from core.deps import require_role
from core.security import (
    create_access_token, create_refresh_token, hash_password,
)
from core.tenancy import TenantContext, get_tenant_context

logger = logging.getLogger("ccms.identity.google")

EMERGENT_SESSION_DATA_URL = (
    "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data"
)
SETTINGS_COLLECTION = "oauth_settings"
SESSION_COLLECTION = "oauth_emergent_sessions"

router = APIRouter(prefix="/auth/google", tags=["auth-google"])


# ---------------------------------------------------------------------------
# Settings (admin-managed)
# ---------------------------------------------------------------------------
class GoogleOauthSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tenant_id: str
    enabled: bool = True
    # List of email domains that are allowed to auto-provision a
    # `role=staff` user on first Google sign-in. Existing users (matched
    # by email regardless of domain) can always sign in.
    allowed_domains: list[str] = Field(default_factory=list)
    default_role: str = "staff"
    updated_at: str | None = None
    updated_by: str | None = None


class _SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    default_role: str = Field(default="staff", pattern=r"^(staff|doctor|admin)$")


async def get_settings(tenant_id: str) -> GoogleOauthSettings:
    db = get_db()
    doc = await db[SETTINGS_COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )
    if not doc:
        return GoogleOauthSettings(tenant_id=tenant_id)
    return GoogleOauthSettings(**doc)


@router.get("/settings", response_model=GoogleOauthSettings)
async def settings_get(
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await get_settings(ctx.tenant_id)


@router.put("/settings", response_model=GoogleOauthSettings)
async def settings_put(
    request: Request,
    payload: _SettingsPayload = Body(...),
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db()
    # Normalise: lowercase + strip leading @
    domains = sorted({
        d.lower().lstrip("@").strip()
        for d in payload.allowed_domains if d and "." in d
    })
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "tenant_id": ctx.tenant_id,
        "enabled": payload.enabled,
        "allowed_domains": domains,
        "default_role": payload.default_role,
        "updated_at": now,
        "updated_by": user.get("email") or user.get("id"),
    }
    await db[SETTINGS_COLLECTION].update_one(
        {"tenant_id": ctx.tenant_id}, {"$set": doc}, upsert=True,
    )
    await audit_success(
        user, "auth.google.settings.updated", request,
        entity_type="oauth_settings", entity_id=ctx.tenant_id,
        metadata={"domains": domains, "enabled": payload.enabled},
    )
    return GoogleOauthSettings(**doc)


# ---------------------------------------------------------------------------
# Public probe — frontend asks "is Google enabled at all?" pre-login
# ---------------------------------------------------------------------------
@router.get("/availability")
async def availability(request: Request):
    """Public, unauthenticated. Returns whether ANY tenant has Google
    OAuth enabled — the login page uses this to decide whether to show
    the "Sign in with Google" button. We do NOT leak which tenants are
    enabled, just a boolean.
    """
    db = get_db()
    cnt = await db[SETTINGS_COLLECTION].count_documents({"enabled": True})
    return {"enabled": cnt > 0}


# ---------------------------------------------------------------------------
# Exchange — turn an Emergent session_id into our JWT cookies
# ---------------------------------------------------------------------------
class _ExchangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=8, max_length=256)
    tenant_slug: str | None = Field(default=None, max_length=64)


async def _emergent_session_data(session_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            EMERGENT_SESSION_DATA_URL,
            headers={"X-Session-ID": session_id},
        )
    if r.status_code != 200:
        raise HTTPException(401, "Google session is invalid or expired")
    body = r.json()
    if not body.get("email"):
        raise HTTPException(401, "Google response missing email")
    return body


def _domain_of(email: str) -> str:
    return (email.split("@", 1)[-1] if "@" in email else "").lower()


async def _resolve_tenant_id(slug: str | None, email: str) -> Optional[str]:
    db = get_db()
    if slug:
        row = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1})
        if row:
            return row["id"]
    # Match the user's email domain against any tenant's allowed_domains
    domain = _domain_of(email)
    if domain:
        match = await db[SETTINGS_COLLECTION].find_one(
            {"enabled": True, "allowed_domains": domain},
            {"_id": 0, "tenant_id": 1},
        )
        if match:
            return match["tenant_id"]
    # Fallback: default tenant
    default = await db.tenants.find_one(
        {"slug": "default"}, {"_id": 0, "id": 1},
    )
    return default["id"] if default else None


def _set_cookies(response: Response, access: str, refresh: str) -> None:
    for name, value, max_age in (
        ("access_token", access, 15 * 60),
        ("refresh_token", refresh, 14 * 86400),
    ):
        response.set_cookie(
            key=name, value=value, httponly=True, secure=True,
            samesite="none", max_age=max_age, path="/",
        )


async def _track_emergent_session(
    *, tenant_id: str, user_id: str, session_token: str,
) -> None:
    db = get_db()
    expires = datetime.now(timezone.utc).replace(microsecond=0)
    # 7-day TTL per playbook
    expires = expires.replace(tzinfo=timezone.utc)
    await db[SESSION_COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "emergent_session_token": session_token[:200],
        "created_at": expires.isoformat(),
    })


@router.post("/exchange")
async def google_exchange(
    payload: _ExchangePayload,
    request: Request,
    response: Response,
):
    db = get_db()

    # 1) Pull session data from Emergent.
    try:
        data = await _emergent_session_data(payload.session_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Emergent session-data call failed: %s", exc)
        raise HTTPException(502, "Could not contact Google sign-in service")

    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip() or "Google User"
    picture = data.get("picture")
    emergent_session_token = data.get("session_token") or ""

    # 2) Resolve tenant.
    tenant_id = await _resolve_tenant_id(payload.tenant_slug, email)
    if not tenant_id:
        raise HTTPException(404, "Tenant not found")

    settings = await get_settings(tenant_id)
    if not settings.enabled:
        await audit_failure(
            action="auth.google.signin",
            request=request,
            actor_email=email,
            reason="disabled",
            metadata={"tenant_id": tenant_id},
        )
        raise HTTPException(
            403, "Google sign-in is disabled for this clinic",
        )

    # 3) Find or auto-provision the user.
    user = await db.users.find_one({"email": email}, {"_id": 0})

    if user is None:
        # No user yet — only auto-provision if domain is allowlisted.
        domain = _domain_of(email)
        if domain not in (settings.allowed_domains or []):
            await audit_failure(
                action="auth.google.signin",
                request=request,
                actor_email=email,
                reason="domain_not_allowed",
                metadata={"domain": domain, "tenant_id": tenant_id},
            )
            raise HTTPException(
                403,
                "This email is not allowed. Ask your clinic administrator "
                "to add the domain or invite you first.",
            )
        # Create a staff-role user with a random un-usable password.
        import secrets as _sec
        random_secret = _sec.token_urlsafe(24)
        hashed = hash_password(random_secret)
        now = datetime.now(timezone.utc).isoformat()
        user = {
            "id": str(uuid.uuid4()),
            "email": email,
            "password_hash": hashed,
            "password_history": [hashed],
            "password_changed_at": now,
            "name": name,
            "role": settings.default_role,
            "status": "active",
            "tenant_id": tenant_id,
            "tenant_scope_all": False,
            "mfa_enabled": False,
            "mfa_policy_required": False,
            "session_epoch": 0,
            "google_picture": picture,
            "auth_method": "google",
            "created_at": now,
            "updated_at": now,
        }
        await db.users.insert_one(dict(user))
        user.pop("_id", None)
    else:
        # Existing user — refuse to log in patients via Google.
        if user.get("role") == "patient":
            await audit_failure(
                action="auth.google.signin",
                request=request,
                actor_email=email,
                reason="patient_role_blocked",
                metadata={"tenant_id": tenant_id},
            )
            raise HTTPException(
                403,
                "Patient accounts must sign in through the patient portal.",
            )
        # Optional: stamp Google avatar / mark auth_method
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {
                "google_picture": picture,
                "auth_method": "google",
                "last_google_login_at": datetime.now(timezone.utc).isoformat(),
            }},
        )

    # 4) Mint our JWTs.
    session_started = datetime.now(timezone.utc).isoformat()
    access = create_access_token(
        user["id"], user["email"], user.get("role") or "staff",
        user.get("session_epoch", 0), session_started,
        tenant_id=tenant_id, is_platform_admin=False,
    )
    refresh = create_refresh_token(
        user["id"], user.get("session_epoch", 0), session_started,
    )
    _set_cookies(response, access, refresh)

    # 5) Track the Emergent session token (so admins can revoke later).
    if emergent_session_token:
        try:
            await _track_emergent_session(
                tenant_id=tenant_id, user_id=user["id"],
                session_token=emergent_session_token,
            )
        except Exception:  # noqa: BLE001 — best-effort
            pass

    await audit_success(
        user, "auth.google.signin", request,
        entity_type="user", entity_id=user["id"],
        metadata={"tenant_id": tenant_id, "auto_provisioned": user.get("auth_method") == "google"},
    )

    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name"),
            "role": user.get("role"),
            "tenant_id": tenant_id,
            "google_picture": picture,
        },
        "tenant_id": tenant_id,
    }
