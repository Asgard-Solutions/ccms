"""Helcim API router — settings, checkout, charges, refunds, webhook.

Mounts at `/api/billing/helcim/*`. All settings + payment endpoints are
tenant-scoped via the standard `TenantContext` dependency. The webhook
endpoint is keyed by `tenant_id` in the URL so Helcim posts directly to
the right tenant.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.authz.policy import require_permission
from services.billing.helcim import HELCIM_PAY_SCRIPT_URL, now_iso
from services.billing.helcim.client import HelcimClient
from services.billing.helcim.credentials import (
    HelcimCredentialsCreate, HelcimCredentialsPublic,
    delete_credentials, get_credentials, get_decrypted_credentials,
    to_public, update_test_outcome, upsert_credentials,
)
from services.billing.helcim.webhook_verify import verify_signature

logger = logging.getLogger("ccms.billing.helcim.router")

router = APIRouter(prefix="/billing/helcim", tags=["billing-helcim"])


# ---------------------------------------------------------------------------
# Settings: per-tenant credentials
# ---------------------------------------------------------------------------

@router.get("/settings", response_model=HelcimCredentialsPublic)
async def get_settings(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await get_credentials(ctx.tenant_id)
    return to_public(doc, ctx.tenant_id)


@router.put("/settings", response_model=HelcimCredentialsPublic)
async def put_settings(
    payload: HelcimCredentialsCreate,
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    doc = await upsert_credentials(ctx.tenant_id, payload, actor=user)
    await audit_success(
        user, "billing.helcim.credentials_updated", request,
        entity_type="helcim_credentials", entity_id=ctx.tenant_id,
        metadata={"test_mode": payload.test_mode,
                  "account_id": payload.account_id,
                  "api_token_last4": doc.get("api_token_last4")},
    )
    return to_public(doc, ctx.tenant_id)


@router.delete("/settings", status_code=204)
async def delete_settings(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    await delete_credentials(ctx.tenant_id)
    await audit_success(
        user, "billing.helcim.credentials_deleted", request,
        entity_type="helcim_credentials", entity_id=ctx.tenant_id,
    )


@router.post("/settings/test")
async def test_connection(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    creds = await get_decrypted_credentials(ctx.tenant_id)
    if not creds or not creds.get("api_token"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Helcim credentials are not configured.")
    cli = HelcimClient(creds["api_token"], account_id=creds.get("account_id"))
    res = await cli.connection_test()
    outcome = "ok" if res.ok else f"failed: {res.get('error') or res.get('status_code')}"
    await update_test_outcome(ctx.tenant_id, outcome=outcome)
    await audit_success(
        user, "billing.helcim.connection_tested", request,
        entity_type="helcim_credentials", entity_id=ctx.tenant_id,
        metadata={"outcome": outcome},
    )
    if not res.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            f"Helcim connection test failed: {res.get('error')}")
    return {"ok": True, "outcome": outcome}


# ---------------------------------------------------------------------------
# HelcimPay.js checkout — initialize a session token for the iframe modal
# ---------------------------------------------------------------------------

class CheckoutInitializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount_cents: int = Field(ge=1, le=10_000_000)
    currency: str = "USD"
    payment_type: str = "purchase"  # `purchase` | `preauth` | `verify`
    invoice_id: str | None = None
    customer_code: str | None = None
    patient_id: str | None = None
    description: str | None = Field(default=None, max_length=120)


class CheckoutInitializeResponse(BaseModel):
    checkout_token: str
    secret_token: str
    script_url: str
    session_id: str
    expires_at: str
    test_mode: bool = False


@router.post("/checkout/initialize", response_model=CheckoutInitializeResponse)
async def initialize_checkout(
    payload: CheckoutInitializeRequest, request: Request,
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    creds = await get_decrypted_credentials(ctx.tenant_id)
    if not creds:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Helcim is not configured for this clinic. Ask an admin to enter the API token in Settings → Payments.",
        )
    cli = HelcimClient(creds["api_token"])
    res = await cli.initialize_helcim_pay(
        amount=payload.amount_cents / 100,
        currency=payload.currency,
        payment_type=payload.payment_type,
        invoice_number=payload.invoice_id,
        customer_code=payload.customer_code,
        description=payload.description,
    )
    if not res.ok or not isinstance(res.get("data"), dict):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Helcim initialize failed: {res.get('error') or 'unknown error'}",
        )
    data = res["data"]
    checkout_token = data.get("checkoutToken")
    secret_token = data.get("secretToken")
    if not checkout_token or not secret_token:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            "Helcim response missing checkout/secret token.")
    # Persist the session so we can correlate the webhook + capture call.
    session_id = str(uuid.uuid4())
    db = tenant_db(ctx.tenant_id)
    await db.helcim_sessions.insert_one({
        "id": session_id,
        "tenant_id": ctx.tenant_id,
        "checkout_token": checkout_token,
        "secret_token": secret_token,
        "amount_cents": payload.amount_cents,
        "currency": payload.currency,
        "payment_type": payload.payment_type,
        "invoice_id": payload.invoice_id,
        "patient_id": payload.patient_id,
        "customer_code": payload.customer_code,
        "created_at": now_iso(),
        "created_by": user.get("id"),
        "status": "initialized",
    })
    await audit_success(
        user, "billing.helcim.checkout_initialized", request,
        entity_type="helcim_session", entity_id=session_id,
        metadata={"amount_cents": payload.amount_cents,
                  "currency": payload.currency,
                  "invoice_id": payload.invoice_id},
    )
    from datetime import datetime, timedelta, timezone
    return CheckoutInitializeResponse(
        checkout_token=checkout_token,
        secret_token=secret_token,
        script_url=HELCIM_PAY_SCRIPT_URL,
        session_id=session_id,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        test_mode=creds.get("test_mode", False),
    )


# ---------------------------------------------------------------------------
# Capture the HelcimPay.js modal response — record the payment locally
# ---------------------------------------------------------------------------

class CheckoutCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str
    transaction_id: str | None = None
    card_token: str | None = None
    customer_code: str | None = None
    approval_code: str | None = None
    amount: float | None = None
    currency: str | None = None
    response: int | None = None  # 1 = approved, 0 = declined
    response_message: str | None = None
    raw: dict[str, Any] | None = None


@router.post("/checkout/capture")
async def capture_checkout(
    payload: CheckoutCaptureRequest, request: Request,
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    session = await db.helcim_sessions.find_one(
        {"id": payload.session_id, "tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Helcim session not found.")
    if session.get("status") == "captured":
        # Idempotent: client retried capture for an already-stored row.
        return {"ok": True, "session": session, "duplicate": True}
    approved = (payload.response == 1) or bool(payload.transaction_id)
    update = {
        "status": "captured" if approved else "declined",
        "transaction_id": payload.transaction_id,
        "card_token": payload.card_token,
        "customer_code": payload.customer_code,
        "approval_code": payload.approval_code,
        "amount": payload.amount,
        "response_message": payload.response_message,
        "raw_response": payload.raw,
        "captured_at": now_iso(),
    }
    await db.helcim_sessions.update_one(
        {"id": payload.session_id, "tenant_id": ctx.tenant_id},
        {"$set": update},
    )
    await audit_success(
        user,
        "billing.helcim.checkout_captured" if approved else "billing.helcim.checkout_declined",
        request,
        entity_type="helcim_session", entity_id=payload.session_id,
        metadata={"transaction_id": payload.transaction_id,
                  "approved": approved,
                  "response_message": payload.response_message},
    )
    if not approved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Payment declined: {payload.response_message or 'unknown reason'}",
        )
    return {"ok": True, "session_id": payload.session_id,
            "transaction_id": payload.transaction_id,
            "card_token": payload.card_token,
            "customer_code": payload.customer_code}


# ---------------------------------------------------------------------------
# Charge a saved card (Customer Vault)
# ---------------------------------------------------------------------------

class ChargeSavedCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_code: str
    card_token: str
    amount_cents: int = Field(ge=1, le=10_000_000)
    currency: str = "USD"
    invoice_id: str | None = None
    description: str | None = Field(default=None, max_length=120)


@router.post("/charges/saved-card")
async def charge_saved_card(
    payload: ChargeSavedCardRequest, request: Request,
    user: dict = Depends(require_permission("payment", "collect")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    creds = await get_decrypted_credentials(ctx.tenant_id)
    if not creds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Helcim is not configured.")
    cli = HelcimClient(creds["api_token"])
    res = await cli.purchase_with_card_token(
        amount=payload.amount_cents / 100,
        currency=payload.currency,
        card_token=payload.card_token,
        customer_code=payload.customer_code,
        invoice_number=payload.invoice_id,
        comments=payload.description,
    )
    txn = (res.get("data") or {}).get("transaction") if isinstance(res.get("data"), dict) else None
    approved = res.ok and txn and (txn.get("status") == "APPROVED")
    await audit_success(
        user,
        "billing.helcim.saved_card_charged" if approved else "billing.helcim.saved_card_declined",
        request, entity_type="helcim_charge",
        entity_id=str(txn.get("transactionId") if txn else "n/a"),
        metadata={"amount_cents": payload.amount_cents, "currency": payload.currency,
                  "invoice_id": payload.invoice_id, "approved": approved},
    )
    if not approved:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Helcim charge failed: {res.get('error') or (txn.get('response') if txn else 'unknown')}",
        )
    return {"ok": True, "transaction": txn}


# ---------------------------------------------------------------------------
# Refund (full or partial)
# ---------------------------------------------------------------------------

class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    amount_cents: int | None = Field(default=None, ge=1, le=10_000_000)
    reason: str | None = Field(default=None, max_length=240)


@router.post("/refunds")
async def post_refund(
    payload: RefundRequest, request: Request,
    user: dict = Depends(require_permission("payment", "refund")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    creds = await get_decrypted_credentials(ctx.tenant_id)
    if not creds:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Helcim is not configured.")
    cli = HelcimClient(creds["api_token"])
    res = await cli.refund(
        transaction_id=payload.transaction_id,
        amount=(payload.amount_cents / 100) if payload.amount_cents else None,
        comments=payload.reason,
    )
    txn = (res.get("data") or {}).get("transaction") if isinstance(res.get("data"), dict) else None
    ok = res.ok and txn and (txn.get("status") == "APPROVED")
    await audit_success(
        user, "billing.helcim.refunded" if ok else "billing.helcim.refund_failed",
        request, entity_type="helcim_refund",
        entity_id=payload.transaction_id,
        metadata={"amount_cents": payload.amount_cents, "reason": payload.reason,
                  "ok": ok},
    )
    if not ok:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Refund failed: {res.get('error') or (txn.get('response') if txn else 'unknown')}",
        )
    return {"ok": True, "transaction": txn}


# ---------------------------------------------------------------------------
# Webhook receiver — keyed by tenant in the URL.
# Helcim posts here on transaction / settlement / chargeback events.
# ---------------------------------------------------------------------------

@router.post("/webhook/{tenant_id}")
async def helcim_webhook(
    tenant_id: str = Path(..., min_length=1),
    *, request: Request,
):
    raw = await request.body()
    creds = await get_decrypted_credentials(tenant_id)
    verifier = (creds or {}).get("webhook_verifier_token")
    if not verifier:
        # Helcim retries; failing here is the right behaviour — operator
        # hasn't completed setup yet.
        logger.warning("helcim.webhook missing verifier token tenant=%s", tenant_id)
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Helcim webhook secret not configured for this tenant.")
    ok, err = verify_signature(
        verifier_token=verifier,
        webhook_id=request.headers.get("webhook-id"),
        webhook_timestamp=request.headers.get("webhook-timestamp"),
        webhook_signature=request.headers.get("webhook-signature"),
        body=raw,
    )
    if not ok:
        logger.warning("helcim.webhook bad sig tenant=%s err=%s", tenant_id, err)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid webhook signature: {err}")

    # Idempotency — store the webhook id and bail if seen.
    db = tenant_db(tenant_id)
    webhook_id = request.headers.get("webhook-id")
    existing = await db.helcim_webhook_log.find_one(
        {"webhook_id": webhook_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1},
    )
    if existing:
        return {"ok": True, "duplicate": True}

    try:
        body_json = await request.json() if raw else {}
    except Exception:
        body_json = {"raw": raw.decode("utf-8", errors="replace")}

    event_type = (body_json.get("eventType") or "").strip() or "unknown"
    transaction_id = (
        body_json.get("transactionId")
        or body_json.get("id")
        or (body_json.get("transaction") or {}).get("transactionId")
    )

    log_doc = {
        "id": str(uuid.uuid4()),
        "webhook_id": webhook_id,
        "tenant_id": tenant_id,
        "event_type": event_type,
        "transaction_id": transaction_id,
        "received_at": now_iso(),
        "headers_subset": {
            k: request.headers.get(k)
            for k in ("webhook-id", "webhook-timestamp")
        },
        "payload": body_json,
        "processed": False,
    }
    await db.helcim_webhook_log.insert_one(log_doc)

    # Best-effort: link the webhook back to a session if we recognise the txn.
    if transaction_id:
        await db.helcim_sessions.update_one(
            {"tenant_id": tenant_id, "transaction_id": str(transaction_id)},
            {"$set": {"latest_webhook_event": event_type,
                      "latest_webhook_at": now_iso()}},
        )

    await db.helcim_webhook_log.update_one(
        {"id": log_doc["id"]}, {"$set": {"processed": True}},
    )
    return {"ok": True, "event_type": event_type}


@router.get("/webhook-log", response_model=list[dict])
async def list_webhook_log(
    request: Request,
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    rows = await db.helcim_webhook_log.find(
        {"tenant_id": ctx.tenant_id},
        {"_id": 0, "id": 1, "webhook_id": 1, "event_type": 1,
         "transaction_id": 1, "received_at": 1, "processed": 1},
    ).sort("received_at", -1).limit(50).to_list(length=50)
    return rows
