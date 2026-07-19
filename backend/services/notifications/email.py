"""
Provider-agnostic transactional email helper.

Behaviour:
  * If `RESEND_API_KEY` + `SENDER_EMAIL` are set in the environment,
    emails are delivered via Resend.
  * Otherwise the helper runs in log-only mode — payload + recipient
    (redacted) are emitted to the structured logger and a synthetic
    `message_id` is returned so callers can treat both modes uniformly.

Callers must never pass PHI in `subject` / `html_body` / `text_body` —
only opaque tokens or links. That's the app's HIPAA policy.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logger = logging.getLogger("ccms.notifications.email")

# Re-import resend lazily so unit tests that mock os.environ don't
# pull in the SDK before their patches are applied.
_RESEND_IMPORTED = False


def _resend():
    global _RESEND_IMPORTED
    import resend  # type: ignore
    if not _RESEND_IMPORTED:
        _RESEND_IMPORTED = True
    return resend


def _redact_email(addr: str) -> str:
    if not addr or "@" not in addr:
        return "<invalid>"
    local, _, domain = addr.partition("@")
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"


def is_live() -> bool:
    """True when both Resend key and sender are configured."""
    return bool(os.environ.get("RESEND_API_KEY") and os.environ.get("SENDER_EMAIL"))


async def send_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    event_type: str = "email",
    correlation_id: str | None = None,
) -> dict:
    """Send one transactional email. Never raises — always returns a
    dict like {ok, provider, message_id|error, event_type, correlation_id}."""
    correlation_id = correlation_id or str(uuid.uuid4())
    base_context = {
        "event": "notification.email",
        "event_type": event_type,
        "to": _redact_email(to),
        "correlation_id": correlation_id,
    }

    if not is_live():
        logger.info(
            "email.log_only", extra={**base_context, "provider": "log-only",
                                    "subject_preview": subject[:80]},
        )
        return {
            "ok": True, "provider": "log-only",
            "message_id": f"log-{correlation_id}",
            "event_type": event_type, "correlation_id": correlation_id,
        }

    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["SENDER_EMAIL"]
    params: dict = {
        "from": sender, "to": [to], "subject": subject,
        "html": html_body,
    }
    if text_body:
        params["text"] = text_body

    try:
        resend = _resend()
        resend.api_key = api_key
        # SDK is sync — run in thread to keep FastAPI non-blocking.
        res = await asyncio.to_thread(resend.Emails.send, params)
        msg_id = res.get("id") if isinstance(res, dict) else getattr(res, "id", None)
        logger.info(
            "email.sent", extra={**base_context, "provider": "resend",
                                 "message_id": msg_id},
        )
        return {
            "ok": True, "provider": "resend", "message_id": msg_id,
            "event_type": event_type, "correlation_id": correlation_id,
        }
    except Exception as exc:  # noqa: BLE001 — must never break caller
        logger.exception(
            "email.failed", extra={**base_context, "provider": "resend",
                                   "error": str(exc)},
        )
        return {
            "ok": False, "provider": "resend", "error": str(exc),
            "event_type": event_type, "correlation_id": correlation_id,
        }
