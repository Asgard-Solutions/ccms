"""Tenancy service — tenants and locations."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


TenantType = Literal["single", "group"]
TenantStatus = Literal["active", "suspended", "closed"]
TenantDbTier = Literal["shared", "dedicated"]


class TenantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    slug: str = Field(..., min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9\-]*$")
    type: TenantType = "single"
    primary_location_name: str = Field(..., min_length=2, max_length=120)
    primary_location_code: str | None = None
    timezone: str = "America/Los_Angeles"


class TenantPublic(BaseModel):
    id: str
    name: str
    slug: str
    type: TenantType
    status: TenantStatus
    db_tier: TenantDbTier
    created_at: str

    model_config = {"extra": "ignore"}


class LocationCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    code: str | None = None
    timezone: str = "America/Los_Angeles"
    address: str | None = None


class LocationPublic(BaseModel):
    id: str
    tenant_id: str
    name: str
    code: str | None = None
    timezone: str = "America/Los_Angeles"
    status: str = "active"
    address: str | None = None
    created_at: str

    model_config = {"extra": "ignore"}


class TenantContextResponse(BaseModel):
    """What the frontend receives to render a tenant switcher / banner."""
    tenant: TenantPublic | None = None
    locations: list[LocationPublic] = []
    allowed_location_ids: list[str] = []
    tenant_scope_all: bool = False
    is_platform_admin: bool = False
