"""Email API router — admin settings, send-test, outbound log.

Mounts at `/api/email/*`.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context
from services.email import (
    EmailCredentialsCreate, EmailCredentialsPublic,
    delete_credentials, get_credentials, to_public,
    update_test_outcome, upsert_credentials,
)
from services.email.client import list_outbound, send_email

router = APIRouter(prefix="/email", tags=["email"])


@router.get("/settings", response_model=EmailCredentialsPublic)
async def settings_get(
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return to_public(await get_credentials(ctx.tenant_id), ctx.tenant_id)


@router.put("/settings", response_model=EmailCredentialsPublic)
async def settings_put(
    request: Request,
    payload: EmailCredentialsCreate = Body(...),
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await upsert_credentials(ctx.tenant_id, payload, actor=user)
    await audit_success(
        user, "email.settings.updated", request,
        entity_type="email_credentials", entity_id=ctx.tenant_id,
        metadata={"enabled": payload.enabled,
                  "from_email": payload.from_email},
    )
    return to_public(doc, ctx.tenant_id)


@router.delete("/settings")
async def settings_delete(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    deleted = await delete_credentials(ctx.tenant_id)
    await audit_success(
        user, "email.settings.deleted", request,
        entity_type="email_credentials", entity_id=ctx.tenant_id,
    )
    return {"deleted": deleted}


class _TestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: EmailStr
    subject: str = Field(default="Test email", max_length=120)
    body: str = Field(default="This is a CCMS test email.", max_length=2000)


@router.post("/settings/test")
async def settings_test(
    request: Request,
    payload: _TestPayload,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    res = await send_email(
        tenant_id=ctx.tenant_id, to=payload.to,
        subject=payload.subject, text=payload.body,
        category="test", related_id=None,
    )
    await update_test_outcome(
        ctx.tenant_id, outcome=res.get("status") or "unknown",
    )
    await audit_success(
        user, "email.settings.test", request,
        entity_type="email_credentials", entity_id=ctx.tenant_id,
        metadata={"status": res.get("status"),
                  "provider": res.get("provider")},
    )
    return res


class _SendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: EmailStr
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    patient_id: str | None = Field(default=None, max_length=64)
    html: bool = False


@router.post("/send")
async def send(
    request: Request,
    payload: _SendPayload,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    res = await send_email(
        tenant_id=ctx.tenant_id, to=payload.to,
        subject=payload.subject,
        html=payload.body if payload.html else None,
        text=None if payload.html else payload.body,
        category="staff_outbound", related_id=payload.patient_id,
    )
    await audit_success(
        user, "email.sent", request,
        entity_type="email_message", entity_id=res.get("id"),
        metadata={"status": res.get("status"),
                  "to_domain": payload.to.split("@", 1)[-1],
                  "patient_id": payload.patient_id},
    )
    return res


@router.get("/outbound-log")
async def outbound_log(
    patient_id: str | None = None,
    limit: int = 100,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    return await list_outbound(
        ctx.tenant_id, patient_id=patient_id, limit=limit,
    )
