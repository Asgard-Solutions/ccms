"""Pydantic models for the authorization service."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PermissionOut(BaseModel):
    id: str
    key: str              # "resource.action"
    resource: str
    action: str
    sensitivity: str
    phi: bool = False
    clinical: bool = False
    financial: bool = False
    export: bool = False
    destructive: bool = False


class GrantOut(BaseModel):
    permission_key: str
    scope: str
    requires_mfa: bool = False
    requires_approval: bool = False
    break_glass_allowed: bool = False


class RoleOut(BaseModel):
    id: str
    key: str
    name: str
    abbr: str
    description: str
    is_system: bool = False
    privileged: bool = False
    service_account: bool = False
    grants: list[GrantOut] = []


class RoleAssign(BaseModel):
    role_key: str
    location_ids: list[str] | None = None


class RoleUnassign(BaseModel):
    role_key: str


class LocationCreate(BaseModel):
    name: str
    code: str | None = None
    timezone: str | None = None


class LocationOut(BaseModel):
    id: str
    name: str
    code: str | None = None
    timezone: str | None = None
    created_at: str


class UserLocationAssign(BaseModel):
    location_id: str


class PatientAssignmentCreate(BaseModel):
    patient_id: str
    provider_id: str
    location_id: str | None = None


class ElevationRequestCreate(BaseModel):
    permission_key: str
    reason: str = Field(..., min_length=10, max_length=500)
    ttl_minutes: int = Field(default=30, ge=5, le=240)
    entity_type: str | None = None
    entity_id: str | None = None


class ElevationApprove(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None


class ElevationOut(BaseModel):
    id: str
    requester_id: str
    requester_email: str
    permission_key: str
    reason: str
    status: Literal["pending", "approved", "rejected", "expired", "used", "revoked"]
    ttl_minutes: int
    entity_type: str | None = None
    entity_id: str | None = None
    approved_by_id: str | None = None
    approved_by_email: str | None = None
    approval_reason: str | None = None
    created_at: str
    expires_at: str | None = None
    used_at: str | None = None


class PermissionOverrideCreate(BaseModel):
    permission_key: str
    scope: str = "all_org"
    requires_mfa: bool = False
    requires_approval: bool = False
    break_glass_allowed: bool = False
    reason: str = Field(..., min_length=10, max_length=500)
    expires_at: str | None = None  # ISO datetime; null = permanent


class PermissionOverrideOut(BaseModel):
    id: str
    user_id: str
    permission_key: str
    scope: str
    requires_mfa: bool
    requires_approval: bool
    break_glass_allowed: bool
    reason: str
    status: str
    granted_by_id: str
    granted_by_email: str
    created_at: str
    expires_at: str | None = None
    revoked_at: str | None = None


class PermissionCheckResult(BaseModel):
    allow: bool
    scope: str | None = None
    requires_mfa: bool = False
    requires_approval: bool = False
    break_glass_allowed: bool = False
    reason: str | None = None
    via_elevation: bool = False


class EffectivePermissionsOut(BaseModel):
    user_id: str
    role_keys: list[str]
    legacy_role: str | None = None
    location_ids: list[str] = []
    permissions: list[dict] = []   # list of {key, scope, flags...}
    updated_at: str
