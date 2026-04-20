"""
TOTP (RFC-6238) multi-factor helpers.

Phase 1 flow:
  1. POST /api/auth/mfa/setup  → returns a freshly generated TOTP secret +
     otpauth:// provisioning URL for Google/Authy/1Password. The secret is
     kept in `mfa_pending_secret` until verified.
  2. POST /api/auth/mfa/verify with first TOTP code promotes the pending
     secret to `mfa_secret` and sets `mfa_enabled=true`.
  3. On login, if `mfa_enabled`, the user receives a short-lived `mfa_ticket`
     JWT (type="mfa") instead of full access cookies. The client then calls
     POST /api/auth/mfa/challenge with the ticket + code to complete login.

MFA is REQUIRED for admin/doctor/staff (enforced at login). Patients may opt
in but it is not required.
"""
import os
from datetime import datetime, timezone, timedelta

import jwt
import pyotp

from core.security import JWT_ALGORITHM, _secret  # re-use JWT secret helper

MFA_REQUIRED_ROLES = {"admin", "doctor", "staff"}
MFA_TICKET_MINUTES = 5
BACKUP_CODE_COUNT = 8


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_email: str) -> str:
    issuer = os.environ.get("MFA_ISSUER", "CCMS Clinic")
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=account_email, issuer_name=issuer
    )


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def verify_backup_code(stored_codes: list[str], code: str) -> str | None:
    """Returns the consumed code (caller should remove it) or None."""
    if not stored_codes or not code:
        return None
    norm = code.strip().replace("-", "").replace(" ", "").lower()
    for c in stored_codes:
        if c.lower() == norm:
            return c
    return None


def generate_backup_codes(n: int = BACKUP_CODE_COUNT) -> list[str]:
    import secrets as _s

    return [_s.token_hex(4) for _ in range(n)]


def create_mfa_ticket(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "mfa",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=MFA_TICKET_MINUTES),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_mfa_ticket(token: str) -> dict:
    payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "mfa":
        raise jwt.InvalidTokenError("Not an MFA ticket")
    return payload
