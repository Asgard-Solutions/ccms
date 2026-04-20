"""
Identity Service — User domain model (HIPAA-hardened).

Future relational schema (delta from Phase 1):
  users + status VARCHAR(20) NOT NULL DEFAULT 'active'
        + password_changed_at TIMESTAMPTZ
        + password_history JSONB  -- list of last 5 bcrypt hashes
        + mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE
        + mfa_secret VARCHAR(64)
        + mfa_pending_secret VARCHAR(64)
        + mfa_backup_codes JSONB
        + last_login_at TIMESTAMPTZ
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict

Role = Literal["admin", "doctor", "staff", "patient", "platform_admin", "super_admin"]
UserStatus = Literal["active", "disabled"]


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    email: EmailStr
    name: str
    role: Role
    phone: str | None = None
    status: UserStatus = "active"
    tenant_id: str | None = None
    tenant_scope_all: bool = False
    is_platform_admin: bool = False
    mfa_enabled: bool = False
    mfa_policy_required: bool = False
    password_changed_at: str | None = None
    created_at: datetime


class LoginResult(BaseModel):
    """Either a full user (MFA not required) or an MFA challenge ticket."""
    model_config = ConfigDict(extra="ignore")
    user: UserPublic | None = None
    mfa_required: bool = False
    mfa_ticket: str | None = None
    password_rotation_due: bool = False


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    role: Role = "staff"
    tenant_id: str | None = None  # platform_admin may override; otherwise inherit from creator


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserPatch(BaseModel):
    """Admin-only partial update for a user (role + status only)."""
    model_config = ConfigDict(extra="forbid")
    role: Role | None = None
    status: UserStatus | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ReauthRequest(BaseModel):
    password: str


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_url: str
    backup_codes: list[str]


class MfaVerify(BaseModel):
    code: str


class MfaChallenge(BaseModel):
    mfa_ticket: str
    code: str


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=16, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)
