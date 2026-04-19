"""
FastAPI dependencies: current user, RBAC guards, disabled-user rejection.
"""
import jwt
from fastapi import HTTPException, Request, status

from core.db import get_db
from core.security import decode_token


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

    db = get_db()
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    if user.get("status") == "disabled":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
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
