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
from pydantic import BaseModel, EmailStr, Field, ConfigDict, model_validator

Role = Literal["admin", "doctor", "staff", "patient", "platform_admin", "super_admin"]
UserStatus = Literal["active", "disabled"]
Theme = Literal["light", "dark", "system"]


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
    pin_configured: bool = False
    theme: Theme = "system"
    # Self-service profile fields (editable via PATCH /auth/me/profile).
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    mobile_phone: str | None = None
    work_phone: str | None = None
    job_title: str | None = None
    credentials_suffix: str | None = None
    preferred_signature_name: str | None = None
    time_zone: str | None = None
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
    """Step-up re-authentication payload.

    Exactly one of `password` or `pin` must be supplied. `pin` is only
    accepted for users who have configured a Security PIN; it reuses
    the same server-side rate-limit / lockout machinery as
    `/auth/me/pin/verify` so brute-force protections can't be bypassed
    via this endpoint.

    `reason` is an optional free-text audit note (e.g. a break-glass
    justification). It's recorded alongside the `auth.reauth` audit
    row so reviewers can see *why* a step-up happened.
    """
    model_config = ConfigDict(extra="forbid")
    password: str | None = None
    pin: str | None = Field(
        default=None, min_length=6, max_length=6, pattern=r"^\d{6}$",
    )
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_factor(self):
        if not self.password and not self.pin:
            raise ValueError("Either password or pin is required")
        if self.password and self.pin:
            raise ValueError("Provide password or pin, not both")
        return self


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


class PreferencesUpdate(BaseModel):
    """Partial update for lightweight user preferences (theme, locale, …).
    All fields optional; only keys that are present are written."""
    model_config = ConfigDict(extra="forbid")
    theme: Theme | None = None


_PIN_FIELD = Field(
    min_length=6, max_length=6, pattern=r"^\d{6}$",
    description="Exactly 6 digits. Stored as a bcrypt hash, never returned.",
)


class PinCreate(BaseModel):
    """Set a brand-new PIN. Requires the caller's current password as
    proof-of-presence — the same gate as `/change-password`."""
    model_config = ConfigDict(extra="forbid")
    current_password: str
    pin: str = _PIN_FIELD


class PinChange(BaseModel):
    """Rotate an existing PIN. Requires both the current password AND
    the current PIN (defence-in-depth: if the password is somehow leaked
    the PIN still holds; if the PIN is compromised a password check
    stops silent rotation)."""
    model_config = ConfigDict(extra="forbid")
    current_password: str
    current_pin: str = _PIN_FIELD
    new_pin: str = _PIN_FIELD


class PinReset(BaseModel):
    """Wipe the existing PIN and replace it. Path used when the user
    forgot their PIN. Relies on a fresh re-auth token (enforced at the
    route layer) rather than `current_pin`."""
    model_config = ConfigDict(extra="forbid")
    new_pin: str = _PIN_FIELD


class PinVerify(BaseModel):
    """Verify the PIN for a short-lived elevated session — same shape
    as `/reauth` but digit-only."""
    model_config = ConfigDict(extra="forbid")
    pin: str = _PIN_FIELD


class PinStatus(BaseModel):
    """Response for `GET /auth/me/pin/status` — surfaces only whether
    a PIN exists and when it was last rotated; never the PIN itself."""
    configured: bool
    created_at: str | None = None
    updated_at: str | None = None
    locked_until: str | None = None
    failed_attempts: int = 0


class ProfileUpdate(BaseModel):
    """Self-service update for the logged-in user's own profile.

    All fields are optional — only present fields are written. Email
    changes require a valid re-auth token because email is the login
    identifier. Name fields stay in sync: whenever first_name/last_name
    or display_name is written, the legacy `name` column is recomputed
    as display_name if present, else "first_name last_name".
    """
    model_config = ConfigDict(extra="forbid")
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=160)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    mobile_phone: str | None = Field(default=None, max_length=40)
    work_phone: str | None = Field(default=None, max_length=40)
    job_title: str | None = Field(default=None, max_length=120)
    credentials_suffix: str | None = Field(default=None, max_length=40)
    preferred_signature_name: str | None = Field(default=None, max_length=160)
    time_zone: str | None = Field(default=None, max_length=64)
