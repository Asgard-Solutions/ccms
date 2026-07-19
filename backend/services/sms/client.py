"""Tenant-aware Twilio client with log-only fallback.

`send_sms(tenant_id, to, body, *, category, related_id)` is the **only**
entry point other services should touch. It takes care of:

  1. Loading tenant credentials (returns a 'logged' stub if missing / disabled).
  2. Normalising the recipient to E.164 (US: +1XXXXXXXXXX).
  3. Calling Twilio Messages API with either `messaging_service_sid`
     or `from_number`.
  4. Persisting a row in `sms_outbound_log` with the result so both
     staff (Billing → SMS log) and tests can inspect delivery.

Log-only mode (when `enabled=false` or no creds) is a deliberate choice:
it lets the rest of the app (OTP, check-in links, questionnaire
invitations) be built and tested without Twilio keys.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from core.phone import normalize_us_phone
from core.tenancy import tenant_db
from services.sms import now_iso
from services.sms.credentials import get_decrypted_credentials

logger = logging.getLogger("ccms.sms.client")

OUTBOUND_LOG = "sms_outbound_log"


def _to_e164(raw: str) -> str | None:
    """Accept `+1...` or a 10-digit US number; return `+1XXXXXXXXXX`."""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith("+"):
        return raw
    try:
        digits = normalize_us_phone(raw)
        return f"+1{digits}" if digits else None
    except ValueError:
        return None


async def _log_outbound(
    *, tenant_id: str, to: str, body: str, category: str,
    related_id: str | None, provider: str, provider_sid: str | None,
    status_str: str, error: str | None,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "to": to,
        "body": body,
        "category": category,
        "related_id": related_id,
        "provider": provider,
        "provider_sid": provider_sid,
        "status": status_str,
        "error": error,
        "created_at": now_iso(),
    }
    await tenant_db(tenant_id)[OUTBOUND_LOG].insert_one(dict(row))
    row.pop("_id", None)
    return row


async def send_sms(
    *, tenant_id: str, to: str, body: str,
    category: str = "transactional",
    related_id: Optional[str] = None,
) -> dict:
    """Send an SMS or log-only stub it. Returns a dict with keys
    ``{id, status, provider, provider_sid, to, body}`` — ``status`` is
    one of ``sent | logged | failed``.
    """
    e164 = _to_e164(to)
    if not e164:
        return await _log_outbound(
            tenant_id=tenant_id, to=to, body=body, category=category,
            related_id=related_id, provider="invalid", provider_sid=None,
            status_str="failed", error="invalid_phone",
        )

    creds = await get_decrypted_credentials(tenant_id)
    if not creds or not creds.get("enabled"):
        # Log-only fallback — still write an outbound row so the staff
        # inbox + dev inspector see the message.
        return await _log_outbound(
            tenant_id=tenant_id, to=e164, body=body, category=category,
            related_id=related_id, provider="log-only",
            provider_sid=None, status_str="logged", error=None,
        )

    try:
        from twilio.rest import Client as TwilioClient  # late-bound
        client = TwilioClient(creds["account_sid"], creds["auth_token"])
        kwargs: dict = {"to": e164, "body": body}
        if creds.get("messaging_service_sid"):
            kwargs["messaging_service_sid"] = creds["messaging_service_sid"]
        elif creds.get("from_number"):
            kwargs["from_"] = creds["from_number"]
        else:
            raise RuntimeError(
                "No messaging_service_sid or from_number configured.",
            )
        message = client.messages.create(**kwargs)
        return await _log_outbound(
            tenant_id=tenant_id, to=e164, body=body, category=category,
            related_id=related_id, provider="twilio",
            provider_sid=message.sid,
            status_str="sent", error=None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("twilio send failed (tenant=%s): %s", tenant_id, exc)
        return await _log_outbound(
            tenant_id=tenant_id, to=e164, body=body, category=category,
            related_id=related_id, provider="twilio",
            provider_sid=None, status_str="failed", error=str(exc)[:300],
        )


async def list_outbound(
    tenant_id: str, *, patient_id: str | None = None, limit: int = 100,
) -> list[dict]:
    db = tenant_db(tenant_id)
    q: dict = {"tenant_id": tenant_id}
    if patient_id:
        q["related_id"] = patient_id
    cur = db[OUTBOUND_LOG].find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [row async for row in cur]
