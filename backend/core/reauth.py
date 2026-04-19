"""
Step-up re-authentication tokens.

Some sensitive actions (delete patient, add a medical record, disable a user)
require the caller to prove possession of their current password within the
last 5 minutes, regardless of their JWT session validity. The caller calls
POST /api/auth/reauth with {password} to receive a short-lived cookie/header
that the protected endpoints check via `require_reauth`.
"""
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import HTTPException, Request, status

from core.security import JWT_ALGORITHM, _secret

REAUTH_TOKEN_MINUTES = 5


def create_reauth_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "reauth",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=REAUTH_TOKEN_MINUTES),
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def require_reauth(request: Request, user: dict) -> None:
    token = request.headers.get("x-reauth-token") or request.cookies.get("reauth_token")
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Re-authentication required for this action.",
        )
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Re-authentication expired. Please confirm your password again.",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid re-auth token")
    if payload.get("type") != "reauth" or payload.get("sub") != user["id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Re-auth mismatch")
