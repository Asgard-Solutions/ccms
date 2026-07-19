"""
Provider-agnostic transactional SMS helper.

Behaviour:
  * If `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` + `TWILIO_FROM_NUMBER`
    are set in the environment, sends via Twilio Messages.
  * Otherwise runs in log-only mode — payload + redacted recipient +
    synthetic `message_sid` returned so callers can treat both modes
    uniformly.

Use this helper for operational SMS (password-protected report ZIP
password delivery, generic transactional messages). For MFA / OTP
flows, use `services.notifications.verify` which wraps Twilio Verify
and handles OTP lifecycle + abuse controls.

PHI must never appear in the body.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

logger = logging.getLogger("ccms.notifications.sms")


def _redact_phone(num: str) -> str:
    if not num:
        return "<invalid>"
    digits = re.sub(r"\D", "", num)
    if len(digits) < 4:
        return "***"
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def is_live() -> bool:
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_FROM_NUMBER")
    )


async def send_sms(
    *,
    to: str,
    body: str,
    event_type: str = "sms",
    correlation_id: str | None = None,
) -> dict:
    """Send one SMS. Never raises. Returns
    {ok, provider, message_sid|error, event_type, correlation_id}."""
    correlation_id = correlation_id or str(uuid.uuid4())
    base_context = {
        "event": "notification.sms",
        "event_type": event_type,
        "to": _redact_phone(to),
        "correlation_id": correlation_id,
    }

    if not is_live():
        logger.info(
            "sms.log_only", extra={**base_context, "provider": "log-only",
                                   "body_length": len(body)},
        )
        return {
            "ok": True, "provider": "log-only",
            "message_sid": f"log-{correlation_id}",
            "event_type": event_type, "correlation_id": correlation_id,
        }

    sid = os.environ["TWILIO_ACCOUNT_SID"]
    tok = os.environ["TWILIO_AUTH_TOKEN"]
    from_num = os.environ["TWILIO_FROM_NUMBER"]
    try:
        from twilio.rest import Client  # type: ignore
        client = Client(sid, tok)

        def _send():
            return client.messages.create(to=to, from_=from_num, body=body)

        res = await asyncio.to_thread(_send)
        msg_sid = getattr(res, "sid", None)
        logger.info(
            "sms.sent", extra={**base_context, "provider": "twilio-messages",
                               "message_sid": msg_sid,
                               "status": getattr(res, "status", None)},
        )
        return {
            "ok": True, "provider": "twilio-messages",
            "message_sid": msg_sid,
            "event_type": event_type, "correlation_id": correlation_id,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "sms.failed", extra={**base_context, "provider": "twilio-messages",
                                 "error": str(exc)},
        )
        return {
            "ok": False, "provider": "twilio-messages", "error": str(exc),
            "event_type": event_type, "correlation_id": correlation_id,
        }
