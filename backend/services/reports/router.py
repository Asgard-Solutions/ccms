"""Reports API — `/api/reports/*`.

Every report:
  1. Requires `reporting.read` permission
  2. Goes through `run_report()` which validates location scope
  3. Result cached per-tenant with a filters-hash key for 300s
  4. Audit row emitted with requested filters + outcome
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from core.audit import audit_failure, audit_success
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_cache import TenantCache, filters_hash, key_for
from services.authz.policy import require_permission
from services.reports import (
    UnauthorizedReportScopeError,
    UnknownReportError,
    registered_reports,
    run_report,
)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def list_reports(
    request: Request,
    _user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
):
    return {"reports": registered_reports()}


@router.post("/{name}/run")
async def run(
    name: str,
    filters: dict = Body(default_factory=dict),
    request: Request = None,  # type: ignore[assignment]
    user: dict = Depends(require_permission("reporting", "read", audit_allow=False)),
    ctx: TenantContext = Depends(get_tenant_context),
):
    cache_key = key_for(
        ctx.tenant_id,
        "report", name, filters_hash(filters or {}),
    )

    async def _load() -> dict:
        try:
            return await run_report(ctx, name, filters)
        except UnknownReportError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
        except UnauthorizedReportScopeError as exc:
            # Mark denial and bubble up as 403.
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    try:
        result = await TenantCache.get_or_set(cache_key, 300, _load)
    except HTTPException:
        await audit_failure(
            action="report.denied", request=request,
            actor_email=user.get("email"),
            reason="scope_or_unknown",
            metadata={"report": name, "filters": filters},
        )
        raise

    await audit_success(
        user, "report.generated", request,
        entity_type="report", entity_id=name,
        metadata={
            "filters": filters,
            "location_ids": result.get("location_ids"),
            "rows": len(result.get("rows", result.get("buckets", []) or [])),
        },
    )
    return result
