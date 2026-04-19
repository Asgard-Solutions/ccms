"""
Password hashing + JWT helpers.
Follows the integration playbook: bcrypt for hashing, PyJWT HS256 for tokens.

Access tokens additionally carry:
  - `epoch`   monotonically increasing integer per user. Incremented on any
              privilege / credential change so old tokens stop working at
              the next request (see `core/deps.py::get_current_user`).
  - `sst`     absolute session start timestamp (ISO-8601 UTC). Enforced at
              ABSOLUTE_SESSION_HOURS to cap long-lived sessions regardless
              of refreshes.
"""
import os
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 60  # reasonable for a clinic staff workflow
REFRESH_TOKEN_DAYS = 7
ABSOLUTE_SESSION_HOURS = 12  # hard cap between first login and forced re-login


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    epoch: int,
    session_started_at: str,
) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "epoch": epoch,
        "sst": session_started_at,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_MINUTES),
        "type": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, epoch: int, session_started_at: str) -> str:
    payload = {
        "sub": user_id,
        "epoch": epoch,
        "sst": session_started_at,
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
