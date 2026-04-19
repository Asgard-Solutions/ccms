"""
Password policy.

Rules:
  - Minimum length: 12
  - Must contain upper, lower, digit, symbol
  - Cannot reuse any of the last 5 password hashes
  - Rotation: 90 days (warn at login; force change at 120 days)
  - Common-password denylist (tiny embedded list — swap for a real one later)
"""
import re
from datetime import datetime, timezone, timedelta

from core.security import verify_password

PASSWORD_MIN_LENGTH = 12
PASSWORD_HISTORY = 5
PASSWORD_ROTATION_DAYS = 90
PASSWORD_HARD_EXPIRY_DAYS = 120

_COMMON = {
    "password", "password123", "admin1234567", "letmein12345",
    "qwerty123456", "welcome12345", "changeme1234", "iloveyou1234",
}


class PasswordPolicyError(ValueError):
    pass


def validate_strength(password: str) -> None:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
        )
    if not re.search(r"[A-Z]", password):
        raise PasswordPolicyError("Password must include an uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise PasswordPolicyError("Password must include a lowercase letter.")
    if not re.search(r"\d", password):
        raise PasswordPolicyError("Password must include a digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise PasswordPolicyError("Password must include a symbol.")
    if password.lower() in _COMMON:
        raise PasswordPolicyError("Password is too common.")


def reject_password_reuse(new_password: str, history_hashes: list[str]) -> None:
    for old_hash in history_hashes[-PASSWORD_HISTORY:]:
        if verify_password(new_password, old_hash):
            raise PasswordPolicyError(
                f"Cannot reuse any of your last {PASSWORD_HISTORY} passwords."
            )


def password_expiry_status(password_changed_at: str | None) -> dict:
    """Returns {'expired': bool, 'rotation_due': bool, 'age_days': int}."""
    if not password_changed_at:
        return {"expired": False, "rotation_due": False, "age_days": 0}
    try:
        changed = datetime.fromisoformat(password_changed_at)
    except Exception:
        return {"expired": False, "rotation_due": False, "age_days": 0}
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - changed
    days = age.days
    return {
        "expired": age > timedelta(days=PASSWORD_HARD_EXPIRY_DAYS),
        "rotation_due": age > timedelta(days=PASSWORD_ROTATION_DAYS),
        "age_days": days,
    }
