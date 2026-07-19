"""SMS API router — settings, send, inbox threads, Twilio inbound webhook.

Mounts at `/api/sms/*`. Tenant-scoped except the webhook, which is keyed
by tenant_id in the URL so Twilio posts directly to the right tenant.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import (
    APIRouter, Body, Depends, Form, HTTPException, Path, Request, Response,
    status,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.sms import now_iso
from services.sms.client import _to_e164, list_outbound, send_sms
from services.sms.credentials import (
    SmsCredentialsCreate, SmsCredentialsPublic,
    delete_credentials, get_credentials, get_decrypted_credentials,
    to_public, update_test_outcome, upsert_credentials,
)
from services.sms.webhook_verify import verify_signature

logger = logging.getLogger("ccms.sms.router")

router = APIRouter(prefix="/sms", tags=["sms"])


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings", response_model=SmsCredentialsPublic)
async def get_settings(
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await get_credentials(ctx.tenant_id)
    return to_public(doc, ctx.tenant_id)


@router.put("/settings", response_model=SmsCredentialsPublic)
async def put_settings(
    request: Request,
    payload: SmsCredentialsCreate = Body(...),
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    try:
        doc = await upsert_credentials(ctx.tenant_id, payload, actor=user)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    await audit_success(
        user, "sms.settings.updated", request,
        entity_type="sms_credentials", entity_id=ctx.tenant_id,
        metadata={"enabled": payload.enabled},
    )
    return to_public(doc, ctx.tenant_id)


@router.delete("/settings")
async def remove_settings(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    deleted = await delete_credentials(ctx.tenant_id)
    await audit_success(
        user, "sms.settings.deleted", request,
        entity_type="sms_credentials", entity_id=ctx.tenant_id,
    )
    return {"deleted": deleted}


class _SendTestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=7, max_length=20)
    body: str = Field(min_length=1, max_length=1600)


@router.post("/settings/test")
async def send_test_message(
    request: Request,
    payload: _SendTestPayload,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    result = await send_sms(
        tenant_id=ctx.tenant_id, to=payload.to, body=payload.body,
        category="test", related_id=None,
    )
    await update_test_outcome(
        ctx.tenant_id,
        outcome=result.get("status") or "unknown",
    )
    await audit_success(
        user, "sms.settings.test", request,
        entity_type="sms_credentials", entity_id=ctx.tenant_id,
        metadata={"status": result.get("status"),
                  "provider": result.get("provider")},
    )
    return result


# ---------------------------------------------------------------------------
# Send (staff)
# ---------------------------------------------------------------------------
class _SendPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=7, max_length=20)
    body: str = Field(min_length=1, max_length=1600)
    patient_id: str | None = Field(default=None, max_length=64)


@router.post("/send")
async def staff_send(
    request: Request,
    payload: _SendPayload,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    result = await send_sms(
        tenant_id=ctx.tenant_id, to=payload.to, body=payload.body,
        category="staff_outbound", related_id=payload.patient_id,
    )
    # Also reflect the outbound row into the thread so the inbox shows
    # both sides of the conversation.
    await _append_thread_message(
        tenant_id=ctx.tenant_id,
        to=_to_e164(payload.to) or payload.to,
        direction="outbound",
        body=payload.body,
        patient_id=payload.patient_id,
        outbound_id=result.get("id"),
    )
    await audit_success(
        user, "sms.sent", request,
        entity_type="sms_message", entity_id=result.get("id"),
        metadata={"to_last4": (payload.to or "")[-4:],
                  "patient_id": payload.patient_id,
                  "status": result.get("status")},
    )
    return result


# ---------------------------------------------------------------------------
# Threads / inbox
# ---------------------------------------------------------------------------
THREAD_COLL = "sms_threads"
MSG_COLL = "sms_messages"


async def _append_thread_message(
    *, tenant_id: str, to: str, direction: str, body: str,
    patient_id: str | None, outbound_id: str | None = None,
    provider_sid: str | None = None,
) -> None:
    db = tenant_db(tenant_id)
    now = now_iso()
    peer = to  # thread key = E.164 peer phone
    preview = body[:140]
    inc_unread = 1 if direction == "inbound" else 0
    # Upsert the thread row. Careful: Mongo forbids touching the same
    # field in $setOnInsert and $inc/$set at the same time. We only
    # $inc unread_count when we actually need to bump it; otherwise
    # we keep the $setOnInsert=0 initializer so new threads start clean.
    update: dict = {
        "$set": {
            "tenant_id": tenant_id,
            "peer": peer,
            "patient_id": patient_id,
            "last_message_at": now,
            "last_message_preview": preview,
            "last_direction": direction,
            "updated_at": now,
        },
    }
    if inc_unread:
        update["$inc"] = {"unread_count": inc_unread}
        update["$setOnInsert"] = {
            "id": str(uuid.uuid4()),
            "created_at": now,
        }
    else:
        update["$setOnInsert"] = {
            "id": str(uuid.uuid4()),
            "created_at": now,
            "unread_count": 0,
        }
    await db[THREAD_COLL].update_one(
        {"tenant_id": tenant_id, "peer": peer}, update, upsert=True,
    )
    await db[MSG_COLL].insert_one({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "peer": peer,
        "direction": direction,
        "body": body,
        "patient_id": patient_id,
        "outbound_id": outbound_id,
        "provider_sid": provider_sid,
        "created_at": now,
    })


@router.get("/threads")
async def list_threads(
    limit: int = 50,
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    cur = db[THREAD_COLL].find(
        {"tenant_id": ctx.tenant_id}, {"_id": 0},
    ).sort("last_message_at", -1).limit(limit)
    return [t async for t in cur]


@router.get("/threads/{thread_id}/messages")
async def list_thread_messages(
    thread_id: str = Path(...),
    user: dict = Depends(require_role("admin", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    thread = await db[THREAD_COLL].find_one(
        {"tenant_id": ctx.tenant_id, "id": thread_id}, {"_id": 0},
    )
    if not thread:
        raise HTTPException(404, "Thread not found")
    # Clear unread counter when staff opens the thread.
    await db[THREAD_COLL].update_one(
        {"tenant_id": ctx.tenant_id, "id": thread_id},
        {"$set": {"unread_count": 0}},
    )
    cur = db[MSG_COLL].find(
        {"tenant_id": ctx.tenant_id, "peer": thread["peer"]}, {"_id": 0},
    ).sort("created_at", 1)
    return {"thread": thread, "messages": [m async for m in cur]}


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


# ---------------------------------------------------------------------------
# Inbound webhook — Twilio POSTs here
# ---------------------------------------------------------------------------
@router.post("/webhook/{tenant_id}")
async def twilio_webhook(
    tenant_id: str,
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(""),
    MessageSid: str = Form(""),
):
    """Twilio posts form-encoded params — we parse them and append to the
    thread. We verify the signature using the tenant's auth token.
    """
    creds = await get_decrypted_credentials(tenant_id)
    if not creds:
        # Unknown tenant — return 204 so Twilio stops retrying.
        return Response(status_code=204)

    # Reconstruct the full URL Twilio hit (host + path).
    url = str(request.url)
    form = await request.form()
    params = {k: v for k, v in form.items()}
    sig = request.headers.get("X-Twilio-Signature")
    if not verify_signature(
        url=url, params=params, signature_header=sig,
        auth_token=creds["auth_token"],
    ):
        raise HTTPException(403, "Invalid Twilio signature")

    # Try to match to a patient by phone.
    db = tenant_db(tenant_id)
    digits = (From or "").lstrip("+").lstrip("1")
    patient = await db.patients.find_one(
        {"tenant_id": tenant_id, "phone": digits}, {"_id": 0, "id": 1},
    )
    patient_id = patient["id"] if patient else None

    await _append_thread_message(
        tenant_id=tenant_id, to=From, direction="inbound",
        body=Body or "", patient_id=patient_id,
        provider_sid=MessageSid,
    )
    # Empty TwiML response — means "no auto-reply".
    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )
