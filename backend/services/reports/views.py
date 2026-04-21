"""
Saved views — per-user and tenant-shared presets for a report.

Collection: `report_saved_views`
  { id, tenant_id, report_name, owner_user_id, name,
    is_shared, is_default, columns[], filters{}, sort, sort_dir,
    created_at, updated_at }

Rules:
- Owners can read/update/delete their own views.
- Any tenant user can read `is_shared=True` views owned by anyone in the
  tenant; only admins (or the owner) can modify a shared view.
- `is_default` is unique per (owner, report) — setting a new default
  clears any previous one automatically.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from core.tenancy import TenantContext, tenant_db


class SavedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    columns: list[str] = Field(default_factory=list, max_length=60)
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = None
    sort_dir: str = Field(default="desc", pattern=r"^(asc|desc)$")
    is_shared: bool = False
    is_default: bool = False


class SavedViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    columns: list[str] | None = Field(default=None, max_length=60)
    filters: dict[str, Any] | None = None
    sort: str | None = None
    sort_dir: str | None = Field(default=None, pattern=r"^(asc|desc)$")
    is_shared: bool | None = None
    is_default: bool | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_admin(ctx: TenantContext) -> bool:
    return ctx.is_platform_admin or ctx.user.get("role") in ("admin", "super_admin")


async def list_views(ctx: TenantContext, report_name: str) -> list[dict]:
    db = tenant_db(ctx.tenant_id)
    q: dict = {"report_name": report_name}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    q["$or"] = [{"owner_user_id": ctx.user["id"]}, {"is_shared": True}]
    cur = db.report_saved_views.find(q, {"_id": 0}).sort("name", 1)
    return [v async for v in cur]


async def create_view(ctx: TenantContext, report_name: str, payload: SavedViewCreate) -> dict:
    ctx.assert_tenant_bound()
    if payload.is_shared and not _is_admin(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can create shared views.")
    # Column whitelist — prevent saved views from surfacing hidden fields
    # the report definition didn't declare (e.g. via a forged POST body).
    _validate_columns_against_definition(report_name, payload.columns)

    db = tenant_db(ctx.tenant_id)
    vid = str(uuid.uuid4())
    now = _now()
    doc = {
        "id": vid, "tenant_id": ctx.tenant_id, "report_name": report_name,
        "owner_user_id": ctx.user["id"], "owner_user_email": ctx.user.get("email"),
        "name": payload.name, "columns": payload.columns, "filters": payload.filters,
        "sort": payload.sort, "sort_dir": payload.sort_dir,
        "is_shared": payload.is_shared, "is_default": payload.is_default,
        "created_at": now, "updated_at": now,
    }
    await db.report_saved_views.insert_one(doc)
    if payload.is_default:
        await _clear_other_defaults(ctx, report_name, vid)
    doc.pop("_id", None)
    return doc


async def update_view(ctx: TenantContext, view_id: str, payload: SavedViewUpdate) -> dict:
    ctx.assert_tenant_bound()
    db = tenant_db(ctx.tenant_id)
    q = {"id": view_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    view = await db.report_saved_views.find_one(q, {"_id": 0})
    if not view:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "View not found")
    is_owner = view.get("owner_user_id") == ctx.user["id"]
    if not is_owner and not _is_admin(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner or an admin may update this view.")

    data = payload.model_dump(exclude_unset=True)
    if "columns" in data:
        _validate_columns_against_definition(view["report_name"], data.get("columns") or [])
    if "is_shared" in data and data["is_shared"] and not _is_admin(ctx) and not is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can share views.")
    data["updated_at"] = _now()
    await db.report_saved_views.update_one({"id": view_id}, {"$set": data})
    updated = await db.report_saved_views.find_one({"id": view_id}, {"_id": 0})
    if updated.get("is_default"):
        await _clear_other_defaults(ctx, updated["report_name"], view_id)
    return updated


async def delete_view(ctx: TenantContext, view_id: str) -> None:
    db = tenant_db(ctx.tenant_id)
    q = {"id": view_id}
    if ctx.tenant_id and not ctx.is_platform_admin:
        q["tenant_id"] = ctx.tenant_id
    view = await db.report_saved_views.find_one(q, {"_id": 0})
    if not view:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "View not found")
    if view.get("owner_user_id") != ctx.user["id"] and not _is_admin(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owner or admin may delete.")
    await db.report_saved_views.delete_one({"id": view_id})


async def _clear_other_defaults(ctx: TenantContext, report_name: str, keep_id: str) -> None:
    db = tenant_db(ctx.tenant_id)
    q: dict = {"report_name": report_name, "owner_user_id": ctx.user["id"],
               "is_default": True, "id": {"$ne": keep_id}}
    if ctx.tenant_id:
        q["tenant_id"] = ctx.tenant_id
    await db.report_saved_views.update_many(q, {"$set": {"is_default": False}})


def _validate_columns_against_definition(report_name: str, columns: list[str]) -> None:
    """Reject any column key the report definition didn't declare.

    Without this gate a user could POST a saved view containing arbitrary
    column names (e.g. `password_hash`) and then — if a runner ever added
    that field to rows — surface it in the UI or exports. The definition
    is the single source of truth for what a user may select.
    """
    # Local import to avoid a circular dependency at module load time.
    from services.reports import get_definition

    d = get_definition(report_name)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown report: {report_name}")
    allowed = {c.key for c in d.columns}
    bad = [c for c in columns if c not in allowed]
    if bad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Unknown columns for report {report_name!r}: {bad[:5]}",
        )
