"""Reports API router — `/api/reports/*`.

Endpoints:
  GET    /reports/catalog                         List reports visible to caller
  GET    /reports/{name}                          Report metadata
  POST   /reports/{name}/run                      Execute report (paged)
  GET    /reports/{name}/views                    Saved views for a report
  POST   /reports/{name}/views                    Create saved view
  PATCH  /reports/views/{view_id}                 Update saved view
  DELETE /reports/views/{view_id}                 Delete saved view
  POST   /reports/{name}/export                   Request an export (CSV/Excel/PDF)

Exports route through the existing `services.exports` background job
pipeline. Status is polled at GET `/api/exports/{id}`, download uses the
signed-token `GET /api/exports/{id}/download?token=...` flow.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_failure, audit_success
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_cache import TenantCache, filters_hash, key_for
from core.tenant_jobs import enqueue
from services.authz.policy import evaluate, require_permission
from services.reports.definitions import (
    QueryContext,
    all_definitions,
    get_definition,
    resolve_columns,
    resolve_sort,
)
from services.reports.views import (
    SavedViewCreate,
    SavedViewUpdate,
    create_view as create_saved_view,
    delete_view as delete_saved_view,
    list_views as list_saved_views,
    update_view as update_saved_view,
)

router = APIRouter(prefix="/reports", tags=["reports"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _definitions_visible(user: dict) -> list[dict]:
    """Return catalog entries whose required permission the caller holds."""
    out: list[dict] = []
    for d in all_definitions():
        resource, action = d.required_permission
        decision = await evaluate(user, resource, action)
        if decision.allow:
            out.append(d.to_public())
    return out


def _require_def(name: str):
    d = get_definition(name)
    if not d:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown report: {name}")
    return d


async def _assert_report_permission(user: dict, definition) -> None:
    resource, action = definition.required_permission
    decision = await evaluate(user, resource, action)
    if not decision.allow:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission {resource}.{action}")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = None
    sort_dir: str = Field(default="desc", pattern=r"^(asc|desc)$")
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=50, ge=1, le=500)
    columns: list[str] | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: str = Field(pattern=r"^(csv|excel|pdf)$")
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = None
    sort_dir: str = Field(default="desc", pattern=r"^(asc|desc)$")
    columns: list[str] | None = None
    # Optional purpose-of-export statement. Recorded against the
    # export/audit row to satisfy HIPAA minimum-necessary reviews.
    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Catalog + metadata
# ---------------------------------------------------------------------------

@router.get("/catalog")
async def catalog(
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
):
    defs = await _definitions_visible(user)
    # Group by category for the UI.
    by_cat: dict[str, list[dict]] = {}
    for d in defs:
        by_cat.setdefault(d["category"], []).append(d)
    categories = [{"category": cat, "reports": sorted(rs, key=lambda r: r["title"])}
                  for cat, rs in sorted(by_cat.items())]
    return {"categories": categories, "total": len(defs)}


@router.get("/{name}")
async def report_meta(
    name: str,
    request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
):
    d = _require_def(name)
    await _assert_report_permission(user, d)
    return d.to_public()


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

@router.post("/{name}/run")
async def run(
    name: str,
    payload: RunRequest = Body(default_factory=RunRequest),
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    d = _require_def(name)
    await _assert_report_permission(user, d)
    ctx.assert_tenant_bound()

    qc = QueryContext(
        tenant=ctx,
        filters=dict(payload.filters or {}),
        sort=resolve_sort(d, payload.sort),
        sort_dir=payload.sort_dir,
        page=payload.page,
        page_size=payload.page_size,
        selected_columns=payload.columns,
    )
    cache_key = key_for(
        ctx.tenant_id, "report", name,
        filters_hash({"f": qc.filters, "s": qc.sort, "d": qc.sort_dir,
                      "p": qc.page, "ps": qc.page_size,
                      "u": user["id"] if not ctx.tenant_scope_all else None}),
    )

    async def _load() -> dict:
        result = await d.runner(qc)
        cols = resolve_columns(d, payload.columns)
        return {
            "report": name,
            "title": d.title,
            "rows": result.rows,
            "total": result.total,
            "aggregates": result.aggregates,
            "columns": [
                {"key": c.key, "label": c.label, "type": c.type,
                 "phi": c.phi, "align": c.align, "sortable": c.sortable}
                for c in cols
            ],
            "page": qc.page,
            "page_size": qc.page_size,
            "sort": qc.sort,
            "sort_dir": qc.sort_dir,
            "contains_phi": d.contains_phi,
        }

    try:
        result = await TenantCache.get_or_set(cache_key, d.cache_ttl_seconds, _load)
    except Exception as exc:  # noqa: BLE001
        await audit_failure(
            action="report.failed", request=request,
            actor_email=user.get("email"),
            reason=str(exc)[:200],
            metadata={"report": name},
        )
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Report execution failed")

    await audit_success(
        user, "report.generated", request,
        entity_type="report", entity_id=name,
        metadata={
            "filters": qc.filters, "page": qc.page, "page_size": qc.page_size,
            "rows_returned": len(result.get("rows", [])),
            "total": result.get("total", 0),
            "contains_phi": d.contains_phi,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------

@router.get("/{name}/views")
async def list_views_route(
    name: str, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    _require_def(name)
    rows = await list_saved_views(ctx, name)
    return {"views": rows}


@router.post("/{name}/views", status_code=201)
async def create_view_route(
    name: str, payload: SavedViewCreate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    _require_def(name)
    view = await create_saved_view(ctx, name, payload)
    await audit_success(user, "report.view_created", request,
                        entity_type="report_view", entity_id=view["id"],
                        metadata={"report": name, "is_shared": payload.is_shared})
    return view


@router.patch("/views/{view_id}")
async def update_view_route(
    view_id: str, payload: SavedViewUpdate, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    view = await update_saved_view(ctx, view_id, payload)
    await audit_success(user, "report.view_updated", request,
                        entity_type="report_view", entity_id=view_id,
                        metadata={"fields": list(payload.model_dump(exclude_unset=True).keys())})
    return view


@router.delete("/views/{view_id}", status_code=204)
async def delete_view_route(
    view_id: str, request: Request,
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    await delete_saved_view(ctx, view_id)
    await audit_success(user, "report.view_deleted", request,
                        entity_type="report_view", entity_id=view_id)
    return None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.post("/{name}/export", status_code=202)
async def export_report(
    name: str,
    payload: ExportRequest,
    request: Request,
    user: dict = Depends(require_permission("reporting", "export", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    d = _require_def(name)
    # Extra gate: exporting PHI reports requires a stronger permission.
    if d.contains_phi:
        decision = await evaluate(user, "reporting", "export_phi")
        if not decision.allow:
            await audit_failure(action="report.export_denied", request=request,
                                actor_email=user.get("email"),
                                reason="missing_export_phi",
                                metadata={"report": name})
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Exports of PHI reports require reporting.export_phi permission.",
            )
    ctx.assert_tenant_bound()

    # Create the export row — worker picks it up
    from services.exports import create_report_export
    export_id, password = await create_report_export(
        ctx, user, report_name=name,
        fmt=payload.format,
        filters=payload.filters,
        sort=resolve_sort(d, payload.sort),
        sort_dir=payload.sort_dir,
        columns=payload.columns,
        reason=payload.reason,
    )

    await audit_success(
        user, "report.export_requested", request,
        entity_type="report_export", entity_id=export_id,
        metadata={
            "report": name, "format": payload.format,
            "password_protected": d.contains_phi,
            "filters": payload.filters,
            "reason": payload.reason,
        },
    )
    # Enqueue the background job (reuses existing `export.generate_report` handler)
    await enqueue(
        "export.generate_report",
        tenant_id=ctx.tenant_id,
        payload={"export_id": export_id},
        actor_user_id=user["id"],
    )
    return {
        "export_id": export_id,
        "status": "pending",
        "password_protected": d.contains_phi,
        # Password is surfaced only in status polling once ready, not here.
    }
