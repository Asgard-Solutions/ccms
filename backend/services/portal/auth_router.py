"""Patient-portal SMS OTP authentication.

Flow:
  1. `POST /api/portal/auth/otp/request {phone, tenant_slug?}` — look up
     a patient by phone inside the target tenant, send a 6-digit OTP via
     Twilio (or log-only). Returns `{sent: true, to, expires_at}` on
     success. Never leaks whether the phone number is known.
  2. `POST /api/portal/auth/otp/verify {phone, code}` — on success sets
     the access + refresh cookies and returns the patient profile. A
     linked `users` row (role=patient) is created on first successful
     verify if one doesn't exist yet.

Design notes:
  * We do not require a password anywhere in the portal flow.
  * The patient's `user` row is stamped with `mfa_enabled=False` and a
    random password hash the user can't use — preventing accidental
    password login by portal-only accounts.
  * `patient_id` is stored on the users row (`linked_patient_id`) so
    every downstream portal endpoint can resolve PHI via one lookup.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.db import get_db
from core.deps import get_current_user
from core.security import create_access_token, create_refresh_token, hash_password
from core.tenancy import tenant_db
from services.sms.otp import request_otp, verify_otp

logger = logging.getLogger("ccms.portal.auth")

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _resolve_tenant_id(slug: str | None) -> str | None:
    db = get_db()
    if slug:
        row = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1})
        return row["id"] if row else None
    default = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    return default["id"] if default else None


def _set_portal_cookies(response: Response, access: str, refresh: str) -> None:
    for name, value, max_age in (
        ("access_token", access, 15 * 60),
        ("refresh_token", refresh, 14 * 86400),
    ):
        response.set_cookie(
            key=name, value=value, httponly=True, secure=True,
            samesite="none", max_age=max_age, path="/",
        )


async def _find_patient_by_phone(tenant_id: str, digits: str) -> dict | None:
    """Best-effort phone lookup.

    Historic seeded patient rows may carry phones in non-canonical
    formats (``+1-503-555-0210``). We prefer a direct match on the
    already-canonicalised digits column; if that misses, we scan rows
    with a phone set and compare the 10-digit suffix of whatever is
    stored.
    """
    db = tenant_db(tenant_id)
    import re as _re
    exact = await db.patients.find_one(
        {"tenant_id": tenant_id, "phone": digits,
         "status": {"$ne": "deleted"}},
        {"_id": 0},
    )
    if exact:
        return exact
    # Fallback: scan and compare digits-only suffix. Cost is bounded —
    # most tenants have <10k patients and this only fires on OTP verify.
    want = _re.sub(r"\D+", "", digits)[-10:]
    if len(want) != 10:
        return None
    cur = db.patients.find(
        {"tenant_id": tenant_id, "phone": {"$ne": None},
         "status": {"$ne": "deleted"}},
        {"_id": 0},
    )
    async for row in cur:
        have = _re.sub(r"\D+", "", str(row.get("phone") or ""))[-10:]
        if have == want:
            return row
    return None


async def _ensure_portal_user(
    *, tenant_id: str, patient: dict,
) -> dict:
    """Return (or create) a `users` row (role=patient) linked to
    ``patient``. The row has no usable password — portal users are OTP-
    only. The caller has already verified the OTP."""
    db = get_db()
    # Try to find an existing linked user.
    existing = await db.users.find_one(
        {"tenant_id": tenant_id, "linked_patient_id": patient["id"]},
        {"_id": 0},
    )
    if existing:
        return existing

    # Fall back: some clinics may have a legacy users row with the same
    # email as the patient. Upgrade that row rather than create a dupe.
    email = (patient.get("email") or "").strip().lower()
    if email:
        existing = await db.users.find_one(
            {"email": email}, {"_id": 0},
        )
        if existing and existing.get("role") == "patient":
            await db.users.update_one(
                {"id": existing["id"]},
                {"$set": {
                    "linked_patient_id": patient["id"],
                    "tenant_id": existing.get("tenant_id") or tenant_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }},
            )
            existing["linked_patient_id"] = patient["id"]
            return existing

    # Create fresh.
    now = datetime.now(timezone.utc).isoformat()
    # Random un-usable password so the row satisfies any policy checks.
    random_secret = secrets.token_urlsafe(24)
    hashed = hash_password(random_secret)
    user_id = str(uuid.uuid4())
    fallback_email = email or (
        f"portal-{patient['id'][:8]}@portal.local"
    )
    doc = {
        "id": user_id,
        "email": fallback_email,
        "password_hash": hashed,
        "password_history": [hashed],
        "password_changed_at": now,
        "name": " ".join(
            filter(None, [patient.get("first_name"),
                          patient.get("last_name")]),
        ) or "Portal User",
        "role": "patient",
        "phone": patient.get("phone"),
        "status": "active",
        "tenant_id": tenant_id,
        "tenant_scope_all": False,
        "mfa_enabled": False,
        "mfa_policy_required": False,
        "session_epoch": 0,
        "linked_patient_id": patient["id"],
        "portal_otp_only": True,
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
class _OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=7, max_length=20)
    tenant_slug: str | None = Field(default=None, max_length=64)


class _OtpVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(min_length=7, max_length=20)
    code: str = Field(min_length=4, max_length=8)
    tenant_slug: str | None = Field(default=None, max_length=64)


@router.post("/otp/request")
async def portal_otp_request(payload: _OtpRequest, request: Request):
    """Send an OTP. Never reveals whether the phone is registered."""
    tenant_id = await _resolve_tenant_id(payload.tenant_slug)
    if not tenant_id:
        raise HTTPException(404, "Tenant not found")
    try:
        result = await request_otp(
            tenant_id=tenant_id, phone=payload.phone,
            purpose="portal_login",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    # Do NOT leak whether the patient exists. We just say "if we have a
    # record, a code was sent". `dev_code` is only set in log-only mode
    # so demo environments can still complete the flow.
    return {
        "sent": True,
        "expires_at": result.get("expires_at"),
        "dev_code": result.get("dev_code"),
    }


@router.post("/otp/verify")
async def portal_otp_verify(
    payload: _OtpVerify, request: Request, response: Response,
):
    tenant_id = await _resolve_tenant_id(payload.tenant_slug)
    if not tenant_id:
        raise HTTPException(404, "Tenant not found")
    ok = await verify_otp(
        tenant_id=tenant_id, phone=payload.phone, code=payload.code,
        purpose="portal_login",
    )
    if not ok:
        raise HTTPException(401, "Invalid or expired code")

    # Must have an actual patient record to log in — we accept the OTP
    # only if we can tie the phone to a patient row.
    from services.sms.client import _to_e164
    e164 = _to_e164(payload.phone) or payload.phone
    digits = e164.lstrip("+").lstrip("1")
    patient = await _find_patient_by_phone(tenant_id, digits)
    if not patient:
        # This should be rare — we burned the code. Fail closed.
        raise HTTPException(404, "No patient record on file")

    user = await _ensure_portal_user(tenant_id=tenant_id, patient=patient)
    session_started = datetime.now(timezone.utc).isoformat()
    access = create_access_token(
        user["id"], user["email"], "patient",
        user.get("session_epoch", 0), session_started,
        tenant_id=tenant_id, is_platform_admin=False,
    )
    refresh = create_refresh_token(
        user["id"], user.get("session_epoch", 0), session_started,
    )
    _set_portal_cookies(response, access, refresh)

    await audit_success(
        user, "portal.auth.otp.verified", request,
        entity_type="patient", entity_id=patient["id"],
        metadata={"tenant_id": tenant_id, "phone_last4": digits[-4:]},
    )
    return {
        "user": {
            "id": user["id"],
            "email": user["email"],
            "role": "patient",
            "name": user.get("name"),
            "tenant_id": tenant_id,
            "linked_patient_id": patient["id"],
        },
        "patient": {
            "id": patient["id"],
            "first_name": patient.get("first_name"),
            "last_name": patient.get("last_name"),
            "phone": patient.get("phone"),
            "email": patient.get("email"),
        },
    }
