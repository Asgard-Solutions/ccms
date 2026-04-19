"""
Identity Service router — /api/auth/*
"""
import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.db import get_db
from core.deps import get_current_user, require_role
from core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    ACCESS_TOKEN_MINUTES,
    REFRESH_TOKEN_DAYS,
)
from services.identity.models import (
    UserRegister,
    UserLogin,
    UserPublic,
)

router = APIRouter(prefix="/auth", tags=["identity"])

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    # samesite=none + secure required for cross-site cookies (frontend -> preview host).
    response.set_cookie(
        "access_token",
        access,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=ACCESS_TOKEN_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        "refresh_token",
        refresh,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=REFRESH_TOKEN_DAYS * 86400,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


def _to_public(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "phone": user.get("phone"),
        "created_at": user["created_at"],
    }


@router.post("/register", response_model=UserPublic)
async def register(payload: UserRegister, response: Response):
    db = get_db()
    email = payload.email.lower().strip()
    existing = await db.users.find_one({"email": email}, {"_id": 0, "id": 1})
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    # Public self-registration is always `patient`. Elevated roles can only be
    # created by an admin through the /auth/users endpoint.
    role = "patient"

    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())
    doc = {
        "id": user_id,
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name.strip(),
        "role": role,
        "phone": payload.phone,
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(doc)

    access = create_access_token(user_id, email, role)
    refresh = create_refresh_token(user_id)
    _set_auth_cookies(response, access, refresh)
    return _to_public(doc)


@router.post("/login", response_model=UserPublic)
async def login(payload: UserLogin, request: Request, response: Response):
    db = get_db()
    email = payload.email.lower().strip()
    ip = request.client.host if request.client else "unknown"
    identifier = f"{ip}:{email}"

    # Brute force lockout check
    attempt = await db.login_attempts.find_one({"identifier": identifier}, {"_id": 0})
    now = datetime.now(timezone.utc)
    if attempt and attempt.get("count", 0) >= MAX_FAILED_ATTEMPTS:
        locked_until_str = attempt.get("locked_until")
        if locked_until_str:
            locked_until = datetime.fromisoformat(locked_until_str)
            if locked_until > now:
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    "Too many failed attempts. Try again later.",
                )

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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    await db.login_attempts.delete_one({"identifier": identifier})
    access = create_access_token(user["id"], user["email"], user["role"])
    refresh = create_refresh_token(user["id"])
    _set_auth_cookies(response, access, refresh)
    return _to_public(user)


@router.post("/logout")
async def logout(response: Response):
    _clear_auth_cookies(response)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserPublic)
async def me(user: dict = Depends(get_current_user)):
    return _to_public(user)


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
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")

    access = create_access_token(user["id"], user["email"], user["role"])
    response.set_cookie(
        "access_token",
        access,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=ACCESS_TOKEN_MINUTES * 60,
        path="/",
    )
    return {"message": "Refreshed"}


# ----- Admin-only user management -----

@router.get("/users", response_model=list[UserPublic])
async def list_users(
    role: str | None = None,
    _admin: dict = Depends(require_role("admin")),
):
    db = get_db()
    q: dict = {}
    if role:
        q["role"] = role
    cursor = db.users.find(q, {"_id": 0, "password_hash": 0}).sort("created_at", -1)
    return [_to_public(u) async for u in cursor]


@router.post("/users", response_model=UserPublic, status_code=201)
async def create_user(
    payload: UserRegister,
    _admin: dict = Depends(require_role("admin")),
):
    db = get_db()
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}, {"_id": 0, "id": 1}):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    role = payload.role or "staff"
    if role not in {"admin", "doctor", "staff", "patient"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid role")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": hash_password(payload.password),
        "name": payload.name.strip(),
        "role": role,
        "phone": payload.phone,
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(doc)
    return _to_public(doc)


@router.get("/providers", response_model=list[UserPublic])
async def list_providers(_user: dict = Depends(get_current_user)):
    """Anyone authenticated can see the list of doctors (providers)."""
    db = get_db()
    cursor = db.users.find(
        {"role": "doctor"}, {"_id": 0, "password_hash": 0}
    ).sort("name", 1)
    return [_to_public(u) async for u in cursor]
