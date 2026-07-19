"""SMS one-time-password (OTP) helpers — used by the patient-portal
phone-first login flow.

Design:
  * 6-digit numeric code, cryptographically random.
  * Stored hashed (SHA-256 hex — cheap, fixed-length) so a DB leak can't
    replay the code. 10-minute TTL, 5 attempts max.
  * Single row per ``(tenant_id, to_e164)`` pair — re-requesting OTP
    overwrites the prior code; this also throttles abuse (the latest
    request window resets attempt counters).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from core.tenancy import tenant_db
from services.sms import now_iso
from services.sms.client import _to_e164, send_sms

COLLECTION = "sms_otp_codes"

OTP_TTL_SECONDS = 10 * 60  # 10 minutes
MAX_ATTEMPTS = 5


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def request_otp(
    *, tenant_id: str, phone: str, purpose: str = "portal_login",
) -> dict:
    """Send a fresh 6-digit code. Returns ``{sent, to, expires_at}`` on
    success or raises ``ValueError`` for an invalid phone number.
    """
    e164 = _to_e164(phone)
    if not e164:
        raise ValueError("Invalid phone number.")
    code = _generate_code()
    expires = datetime.now(timezone.utc) + timedelta(seconds=OTP_TTL_SECONDS)
    await tenant_db(tenant_id)[COLLECTION].update_one(
        {"tenant_id": tenant_id, "to": e164, "purpose": purpose},
        {"$set": {
            "tenant_id": tenant_id,
            "to": e164,
            "purpose": purpose,
            "code_hash": _hash(code),
            "attempts": 0,
            "expires_at": expires.isoformat(),
            "updated_at": now_iso(),
        }},
        upsert=True,
    )
    body = f"Your clinic login code is {code}. It expires in 10 minutes."
    result = await send_sms(
        tenant_id=tenant_id, to=e164, body=body,
        category="otp", related_id=None,
    )
    return {
        "sent": True,
        "to": e164,
        "expires_at": expires.isoformat(),
        "provider": result.get("provider"),
        # `dev_code` is surfaced only when running in log-only mode so
        # developers and automated tests can sign in without a real
        # Twilio account. In production (provider=twilio) the code is
        # never returned to the client.
        "dev_code": code if result.get("provider") == "log-only" else None,
    }


async def verify_otp(
    *, tenant_id: str, phone: str, code: str,
    purpose: str = "portal_login",
) -> bool:
    e164 = _to_e164(phone)
    if not e164 or not code or len(code) != 6:
        return False
    db = tenant_db(tenant_id)
    row = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "to": e164, "purpose": purpose},
        {"_id": 0},
    )
    if not row:
        return False
    # Expiry check.
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (KeyError, ValueError):
        return False
    if datetime.now(timezone.utc) >= expires:
        await db[COLLECTION].delete_one({"tenant_id": tenant_id, "to": e164, "purpose": purpose})
        return False
    # Attempt cap.
    attempts = int(row.get("attempts") or 0)
    if attempts >= MAX_ATTEMPTS:
        return False
    match = _hash(code) == row.get("code_hash")
    if match:
        # Burn the code once used.
        await db[COLLECTION].delete_one({"tenant_id": tenant_id, "to": e164, "purpose": purpose})
        return True
    # Wrong code — increment.
    await db[COLLECTION].update_one(
        {"tenant_id": tenant_id, "to": e164, "purpose": purpose},
        {"$inc": {"attempts": 1}, "$set": {"updated_at": now_iso()}},
    )
    return False
