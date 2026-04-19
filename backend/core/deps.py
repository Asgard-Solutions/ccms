"""
FastAPI dependencies: current user, RBAC guards, disabled-user rejection,
session-epoch enforcement, absolute-session-lifetime enforcement.
"""
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import HTTPException, Request, status

from core.db import get_db
from core.security import ABSOLUTE_SESSION_HOURS, decode_token


async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")

    # Absolute session lifetime cap (regardless of refreshes).
    sst = payload.get("sst")
    if sst:
        try:
            started = datetime.fromisoformat(sst)
            if datetime.now(timezone.utc) - started > timedelta(hours=ABSOLUTE_SESSION_HOURS):
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Session exceeded absolute lifetime",
                )
        except ValueError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session marker")

    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if user.get("status") == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    # Session epoch: bumped on any credential / privilege change. Old tokens die.
    token_epoch = payload.get("epoch", 0)
    user_epoch = user.get("session_epoch", 0)
    if token_epoch != user_epoch:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Session invalidated — please sign in again",
        )

    user.pop("password_hash", None)
    user.pop("password_history", None)
    return user


def require_role(*roles: str):
    allowed = set(roles)

    async def _guard(request: Request) -> dict:
        user = await get_current_user(request)
        if user.get("role") not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires one of roles: {sorted(allowed)}",
            )
        return user

    return _guard
