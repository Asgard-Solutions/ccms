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
    npi_number: str | None = None
    dea_number: str | None = None
    dea_expires_at: str | None = None
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

    @model_validator(mode="after")
    def _normalize_phone(self):
        from core.phone import normalize_us_phone

        if self.phone in (None, ""):
            self.phone = None
        else:
            try:
                self.phone = normalize_us_phone(self.phone)
            except ValueError as exc:
                raise ValueError(f"Phone: {exc}") from exc
        return self


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    role: Role = "staff"
    tenant_id: str | None = None  # platform_admin may override; otherwise inherit from creator

    @model_validator(mode="after")
    def _normalize_phone(self):
        from core.phone import normalize_us_phone

        if self.phone in (None, ""):
            self.phone = None
        else:
            try:
                self.phone = normalize_us_phone(self.phone)
            except ValueError as exc:
                raise ValueError(f"Phone: {exc}") from exc
        return self


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
    npi_number: str | None = Field(
        default=None, max_length=10,
        description="CMS 10-digit National Provider Identifier (clinicians only).",
    )
    dea_number: str | None = Field(
        default=None, max_length=9,
        description="DEA registration number — 9 chars, normalised upper-case. Clinicians only.",
    )
    dea_expires_at: str | None = Field(
        default=None, max_length=10,
        description="ISO YYYY-MM-DD expiry date for the DEA registration.",
    )

    @model_validator(mode="after")
    def _validate_phones(self):
        # Normalise US phone numbers to 10-digit canonical form.
        # Empty string → None (clears). Non-empty must be a valid
        # 10-digit US number or we raise 422. See `core/phone.py`.
        #
        # We only touch fields that were actually supplied in the
        # request body (`__pydantic_fields_set__`) so an empty PATCH
        # remains an empty PATCH — otherwise the router would see
        # spurious `phone=None` entries and treat the call as a
        # no-op update instead of rejecting it.
        from core.phone import normalize_us_phone

        supplied = self.__pydantic_fields_set__
        for attr in ("phone", "mobile_phone", "work_phone"):
            if attr not in supplied:
                continue
            value = getattr(self, attr)
            if value in (None, ""):
                setattr(self, attr, None)
                continue
            try:
                setattr(self, attr, normalize_us_phone(value))
            except ValueError as exc:
                raise ValueError(
                    f"{attr.replace('_', ' ').title()}: {exc}",
                ) from exc
        return self

    @model_validator(mode="after")
    def _validate_npi(self):
        # NPI must be exactly 10 digits AND pass the CMS/NPI Luhn
        # checksum (validated as if the implicit 80840 prefix were
        # present). Empty string is treated as a "clear this field"
        # signal and passes through unchanged. See `core/npi.py`.
        from core.npi import NpiValidationError, validate_npi_or_raise

        if self.npi_number not in (None, ""):
            try:
                self.npi_number = validate_npi_or_raise(self.npi_number)
            except NpiValidationError as exc:
                raise ValueError(str(exc)) from exc
        return self

    @model_validator(mode="after")
    def _validate_dea(self):
        # DEA number: 2 letters + 6 digits + 1 check digit (9 chars).
        # Trim + upper-case before validation; empty string clears.
        # See `core/dea.py` for the checksum spec. Checksum validation
        # is structural only — it does NOT prove federal registration.
        from core.dea import DeaValidationError, validate_dea_or_raise

        if self.dea_number not in (None, ""):
            try:
                self.dea_number = validate_dea_or_raise(self.dea_number)
            except DeaValidationError as exc:
                raise ValueError(str(exc)) from exc

        # Optional expiry date — enforce ISO YYYY-MM-DD shape if present.
        if self.dea_expires_at not in (None, ""):
            from datetime import date as _date
            v = self.dea_expires_at.strip()
            try:
                _date.fromisoformat(v)
            except ValueError as exc:
                raise ValueError(
                    "DEA expiry must be an ISO date (YYYY-MM-DD).",
                ) from exc
            self.dea_expires_at = v
        return self


# ---------------------------------------------------------------------------
# Professional license — multi-license clinicians (DC in CA, DC in NV, etc.)
# ---------------------------------------------------------------------------
LICENSE_TYPES = Literal[
    "DC", "MD", "DO", "PT", "DPT", "RN", "NP", "PA", "LMT", "ATC",
    "DACBR", "DACNB", "CCSP", "other",
]


def _upper_strip(v: str | None) -> str | None:
    if v is None:
        return None
    return v.strip().upper() or None


class LicenseBase(BaseModel):
    """Shared fields between create + update. `issuing_state` is a
    two-letter USPS code, uppercased on the fly."""
    model_config = ConfigDict(extra="forbid")
    license_type: LICENSE_TYPES = "DC"
    license_number: str = Field(min_length=2, max_length=40)
    issuing_state: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    expiration_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    specialty: str | None = Field(default=None, max_length=120)
    board_notes: str | None = Field(default=None, max_length=500)


class LicenseCreate(LicenseBase):
    pass


class LicenseUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    model_config = ConfigDict(extra="forbid")
    license_type: LICENSE_TYPES | None = None
    license_number: str | None = Field(default=None, min_length=2, max_length=40)
    issuing_state: str | None = Field(
        default=None, min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$",
    )
    expiration_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    specialty: str | None = Field(default=None, max_length=120)
    board_notes: str | None = Field(default=None, max_length=500)


class LicensePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    license_type: str
    license_number: str
    issuing_state: str
    expiration_date: str
    specialty: str | None = None
    board_notes: str | None = None
    created_at: str
    updated_at: str
