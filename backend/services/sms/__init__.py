"""Twilio SMS service — tenant-scoped credentials, send/receive, OTP,
staff inbox, and a log-only fallback when no Twilio creds are configured.

Public surface is the FastAPI router at `/api/sms/*`. Internal helpers
(`client.send_sms`, `otp.request_otp`, `otp.verify_otp`) are imported by
other services (identity portal-OTP, scheduling booking-request-confirm,
clinical questionnaire-delivery).
"""
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
