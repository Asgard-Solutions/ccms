"""
Twilio Verify wrapper — managed OTP for MFA-over-SMS flows.

Behaviour:
  * If `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` +
    `TWILIO_VERIFY_SERVICE_SID` are set, delegates to Twilio Verify.
  * Otherwise runs in log-only mode; `start_verification()` returns
    `{ok: True, provider: "log-only", sid: ...}` and
    `check_code()` accepts any non-empty 6-digit code so dev flows
    still work.

Never pass PHI in any argument.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid

logger = logging.getLogger("ccms.notifications.verify")


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
        and os.environ.get("TWILIO_VERIFY_SERVICE_SID")
    )


async def start_verification(*, to: str, channel: str = "sms",
                             correlation_id: str | None = None) -> dict:
    correlation_id = correlation_id or str(uuid.uuid4())
    ctx = {"event": "notification.verify.start", "to": _redact_phone(to),
           "channel": channel, "correlation_id": correlation_id}

    if not is_live():
        logger.info("verify.log_only.start", extra={**ctx, "provider": "log-only"})
        return {
            "ok": True, "provider": "log-only", "status": "pending",
            "sid": f"log-{correlation_id}",
            "correlation_id": correlation_id,
        }

    sid = os.environ["TWILIO_ACCOUNT_SID"]
    tok = os.environ["TWILIO_AUTH_TOKEN"]
    service = os.environ["TWILIO_VERIFY_SERVICE_SID"]
    try:
        from twilio.rest import Client  # type: ignore
        client = Client(sid, tok)

        def _start():
            return (client.verify.v2.services(service)
                    .verifications.create(to=to, channel=channel))

        res = await asyncio.to_thread(_start)
        logger.info(
            "verify.started",
            extra={**ctx, "provider": "twilio-verify",
                   "sid": getattr(res, "sid", None),
                   "status": getattr(res, "status", None)},
        )
        return {
            "ok": True, "provider": "twilio-verify",
            "sid": getattr(res, "sid", None),
            "status": getattr(res, "status", None),
            "correlation_id": correlation_id,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("verify.start.failed",
                         extra={**ctx, "provider": "twilio-verify",
                                "error": str(exc)})
        return {
            "ok": False, "provider": "twilio-verify", "error": str(exc),
            "correlation_id": correlation_id,
        }


async def check_code(*, to: str, code: str,
                     correlation_id: str | None = None) -> dict:
    """Verify a code submitted by the user. Returns
    {ok, provider, valid, status|error}."""
    correlation_id = correlation_id or str(uuid.uuid4())
    ctx = {"event": "notification.verify.check", "to": _redact_phone(to),
           "correlation_id": correlation_id}

    if not is_live():
        # In dev mode, any well-formed 6-digit code is accepted so local
        # flows stay testable without a real account.
        valid = bool(code and code.strip().isdigit() and 4 <= len(code.strip()) <= 10)
        logger.info("verify.log_only.check",
                    extra={**ctx, "provider": "log-only", "valid": valid})
        return {
            "ok": True, "provider": "log-only", "valid": valid,
            "status": "approved" if valid else "invalid",
            "correlation_id": correlation_id,
        }

    sid = os.environ["TWILIO_ACCOUNT_SID"]
    tok = os.environ["TWILIO_AUTH_TOKEN"]
    service = os.environ["TWILIO_VERIFY_SERVICE_SID"]
    try:
        from twilio.rest import Client  # type: ignore
        client = Client(sid, tok)

        def _check():
            return (client.verify.v2.services(service)
                    .verification_checks.create(to=to, code=code))

        res = await asyncio.to_thread(_check)
        status = getattr(res, "status", None)
        logger.info("verify.checked",
                    extra={**ctx, "provider": "twilio-verify",
                           "status": status})
        return {
            "ok": True, "provider": "twilio-verify",
            "valid": status == "approved", "status": status,
            "correlation_id": correlation_id,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("verify.check.failed",
                         extra={**ctx, "provider": "twilio-verify",
                                "error": str(exc)})
        return {
            "ok": False, "provider": "twilio-verify", "error": str(exc),
            "valid": False, "correlation_id": correlation_id,
        }
