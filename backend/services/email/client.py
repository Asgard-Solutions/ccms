"""Tenant-aware Resend client with log-only fallback.

Symmetrical with `services.sms.client.send_sms`. The single entry point
is ``send_email(...)``; everything else (statement delivery, password
resets, appointment reminders) calls into here.

When no tenant credentials exist (or the toggle is off), we fall back to
the existing legacy env-var path (RESEND_API_KEY + SENDER_EMAIL). When
that also isn't present, we log-only — write a row in
``email_outbound_log`` and return ``status=logged``.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from core.tenancy import tenant_db
from services.email import (
    _now_iso, get_decrypted_credentials,
)

logger = logging.getLogger("ccms.email.client")

OUTBOUND_LOG = "email_outbound_log"


async def _log(
    *, tenant_id: str, to: str, subject: str, html_or_text: str,
    category: str, related_id: str | None, provider: str,
    provider_id: str | None, status_str: str, error: str | None,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "to": to,
        "subject": subject,
        "preview": (html_or_text or "")[:140],
        "category": category,
        "related_id": related_id,
        "provider": provider,
        "provider_id": provider_id,
        "status": status_str,
        "error": error,
        "created_at": _now_iso(),
    }
    await tenant_db(tenant_id)[OUTBOUND_LOG].insert_one(dict(row))
    row.pop("_id", None)
    return row


async def send_email(
    *, tenant_id: str, to: str, subject: str,
    html: str | None = None, text: str | None = None,
    category: str = "transactional",
    related_id: Optional[str] = None,
) -> dict:
    if not html and not text:
        raise ValueError("send_email requires html or text")

    # 1) Prefer tenant-scoped Resend credentials.
    creds = await get_decrypted_credentials(tenant_id)
    if creds and creds.get("enabled"):
        return await _send_via_resend(
            tenant_id=tenant_id,
            api_key=creds["api_key"],
            from_email=creds["from_email"],
            from_name=creds.get("from_name"),
            reply_to=creds.get("reply_to"),
            to=to, subject=subject, html=html, text=text,
            category=category, related_id=related_id,
        )

    # 2) Env-var fallback (legacy single-key deployments).
    env_key = os.environ.get("RESEND_API_KEY")
    env_from = os.environ.get("SENDER_EMAIL")
    if env_key and env_from:
        return await _send_via_resend(
            tenant_id=tenant_id,
            api_key=env_key, from_email=env_from,
            from_name=os.environ.get("SENDER_NAME"),
            reply_to=None,
            to=to, subject=subject, html=html, text=text,
            category=category, related_id=related_id,
        )

    # 3) Log-only fallback.
    return await _log(
        tenant_id=tenant_id, to=to, subject=subject,
        html_or_text=text or html or "",
        category=category, related_id=related_id,
        provider="log-only", provider_id=None,
        status_str="logged", error=None,
    )


async def _send_via_resend(
    *, tenant_id: str, api_key: str, from_email: str,
    from_name: str | None, reply_to: str | None,
    to: str, subject: str, html: str | None, text: str | None,
    category: str, related_id: str | None,
) -> dict:
    try:
        import resend  # late-bound
        import asyncio
        sender = (
            f"{from_name} <{from_email}>" if from_name else from_email
        )
        params: dict = {"from": sender, "to": [to], "subject": subject}
        if html:
            params["html"] = html
        if text:
            params["text"] = text
        if reply_to:
            params["reply_to"] = [reply_to]
        resend.api_key = api_key
        sent = await asyncio.to_thread(resend.Emails.send, params)
        return await _log(
            tenant_id=tenant_id, to=to, subject=subject,
            html_or_text=text or html or "",
            category=category, related_id=related_id,
            provider="resend",
            provider_id=(sent or {}).get("id"),
            status_str="sent", error=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Resend send failed (tenant=%s): %s", tenant_id, exc)
        return await _log(
            tenant_id=tenant_id, to=to, subject=subject,
            html_or_text=text or html or "",
            category=category, related_id=related_id,
            provider="resend", provider_id=None,
            status_str="failed", error=str(exc)[:300],
        )


async def list_outbound(
    tenant_id: str, *, patient_id: str | None = None, limit: int = 100,
) -> list[dict]:
    q: dict = {"tenant_id": tenant_id}
    if patient_id:
        q["related_id"] = patient_id
    cur = (
        tenant_db(tenant_id)[OUTBOUND_LOG]
        .find(q, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [row async for row in cur]
