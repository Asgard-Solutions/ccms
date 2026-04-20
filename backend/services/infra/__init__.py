"""Infrastructure health + diagnostics — platform-admin only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from core import secrets
from core.db_routing import probe_replica_once, replica_health
from core.deps import get_current_user
from core.tenancy import TenantContext, get_tenant_context

router = APIRouter(prefix="/infra", tags=["infra"])


def _require_platform_admin(
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    if not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform admin only")
    return user


@router.get("/replica")
async def replica_status(_user: dict = Depends(_require_platform_admin)):
    """Current replica-lag / health snapshot. Runs a fresh probe."""
    await probe_replica_once()
    return replica_health()


@router.get("/secrets")
async def secrets_status(_user: dict = Depends(_require_platform_admin)):
    """Validate that all required secrets are configured.
    Never returns the values themselves — only names and lengths."""
    missing = secrets.validate_startup()
    present: dict[str, int] = {}
    for name in secrets.REQUIRED:
        val = secrets.get(name)
        if val:
            present[name] = len(val)
    return {"ok": not missing, "missing": missing, "present_lengths": present}
