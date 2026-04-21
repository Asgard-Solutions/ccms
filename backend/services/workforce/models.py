"""Pydantic models for the workforce service."""
from __future__ import annotations

from typing import Literal, Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


def _normalize_phone_field(value: str | None) -> str | None:
    from core.phone import normalize_us_phone

    if value in (None, ""):
        return None
    try:
        return normalize_us_phone(value)
    except ValueError as exc:
        raise ValueError(f"Phone: {exc}") from exc


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

class InviteCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=200)
    role: Literal["admin", "doctor", "staff", "clinic_manager",
                  "front_desk", "billing_specialist"]
    location_ids: list[str] = Field(default_factory=list)
    phone: str | None = None
    ttl_hours: int = Field(default=72, ge=1, le=168)

    @model_validator(mode="after")
    def _normalize_phone(self):
        self.phone = _normalize_phone_field(self.phone)
        return self


class InviteAccept(BaseModel):
    token: str = Field(min_length=20)
    password: str = Field(min_length=12, max_length=128)
    phone: str | None = None

    @model_validator(mode="after")
    def _normalize_phone(self):
        self.phone = _normalize_phone_field(self.phone)
        return self


# ---------------------------------------------------------------------------
# Proxy relationships
# ---------------------------------------------------------------------------

class ProxyGrant(BaseModel):
    patient_id: str
    proxy_user_id: str
    relationship: Literal["parent", "legal_guardian", "spouse", "adult_child",
                          "power_of_attorney", "authorised_representative"] = "legal_guardian"
    scope: Literal["read", "read_manage"] = "read"
    effective_date: str
    expires_at: str | None = None
    reason: str = Field(min_length=5, max_length=500)


class RevokeWithReason(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


# ---------------------------------------------------------------------------
# Deprovisioning
# ---------------------------------------------------------------------------

class DeprovisionRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    reassign_future_to_user_id: str | None = Field(
        default=None,
        description="Optional provider to reassign future appointments to. "
                    "When omitted, appointments are flagged `needs_reassignment=True` "
                    "and their provider_id is cleared.",
    )


class DeprovisionReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    user_id: str
    email: str
    status_after: str
    session_epoch: int
    role_grants_revoked: int
    permission_overrides_revoked: int
    location_assignments_revoked: int
    patient_assignments_revoked: int
    future_appointments_flagged: int
    future_appointments_reassigned: int
    invitations_cancelled: int
    break_glass_expired: int
    proxies_revoked: int


# ---------------------------------------------------------------------------
# Break-glass
# ---------------------------------------------------------------------------

class BreakGlassStart(BaseModel):
    scope_resource: Literal["patient_chart", "audit_log", "billing"] = "patient_chart"
    scope_entity_id: str | None = None
    ticket_reference: str | None = None
    reason: str = Field(min_length=20, max_length=1000)
    duration_minutes: int = Field(default=60, ge=5, le=240)


class BreakGlassAttest(BaseModel):
    summary: str = Field(min_length=20, max_length=2000,
                         description="What was accessed and why it was justified")
    phi_accessed: bool = True
    action_required: bool = False


# ---------------------------------------------------------------------------
# Admin session revocation
# ---------------------------------------------------------------------------

class AdminSessionAction(BaseModel):
    user_id: str
    reason: str = Field(min_length=5, max_length=500)
