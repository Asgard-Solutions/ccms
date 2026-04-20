"""
services/billing/statement_delivery.py — Phase 6 statement PDF + email.

PDF rendered via reportlab from the already-snapshotted statement
`body` text. Email delivery via Resend, with a graceful mock when
`RESEND_API_KEY` is not configured so non-production environments and
tests don't need a real key.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import datetime, timezone
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

log = logging.getLogger(__name__)


def render_statement_pdf(
    *, statement: dict, patient: dict,
) -> bytes:
    """Render a simple, deterministic letter-sized statement PDF."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    width, height = LETTER

    # Letterhead
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.75 * inch, height - 0.9 * inch, "Patient Statement")
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, height - 1.1 * inch,
                 f"Generated {statement.get('generated_at', '')[:19]}")
    c.drawString(0.75 * inch, height - 1.25 * inch,
                 f"As of {statement.get('as_of_date', '')}")

    # Patient block
    c.setFont("Helvetica-Bold", 11)
    c.drawString(0.75 * inch, height - 1.7 * inch, "Bill to:")
    c.setFont("Helvetica", 10)
    name = " ".join(filter(None, [patient.get("first_name"),
                                  patient.get("last_name")])) or "Patient"
    c.drawString(0.75 * inch, height - 1.9 * inch, name)
    if patient.get("email"):
        c.drawString(0.75 * inch, height - 2.05 * inch, patient["email"])

    # Balance headline
    total = statement.get("total_balance_cents", 0)
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(width - 0.75 * inch, height - 1.9 * inch,
                      f"Total due: ${total / 100:.2f}")

    # Body — stream the pre-rendered text block line by line.
    body = statement.get("body") or ""
    c.setFont("Courier", 9)
    y = height - 2.6 * inch
    for line in body.splitlines():
        if y < 1 * inch:
            c.showPage()
            c.setFont("Courier", 9)
            y = height - 0.75 * inch
        c.drawString(0.75 * inch, y, line[:110])
        y -= 12

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(0.75 * inch, 0.6 * inch,
                 "Please remit payment within 30 days of receipt. "
                 "Questions? Contact the clinic billing office.")

    c.save()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Resend email delivery
# ---------------------------------------------------------------------------
_RESEND_READY: bool | None = None


def _configure_resend() -> bool:
    """Return True if the Resend SDK can send real mail."""
    global _RESEND_READY
    if _RESEND_READY is not None:
        return _RESEND_READY
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        _RESEND_READY = False
        return False
    try:
        import resend  # noqa: F401
        resend.api_key = api_key
        _RESEND_READY = True
    except Exception as exc:  # pragma: no cover
        log.warning("Resend SDK unavailable: %s", exc)
        _RESEND_READY = False
    return _RESEND_READY


async def send_statement_email(
    *,
    to: str,
    subject: str,
    html_body: str,
    pdf_bytes: bytes,
    pdf_filename: str,
) -> dict[str, Any]:
    """Send the statement PDF. If `RESEND_API_KEY` is missing, returns
    a mocked success payload so staging / CI never need the key.
    """
    sender = os.environ.get("SENDER_EMAIL", "onboarding@resend.dev")
    if not _configure_resend():
        log.info("Resend mock — would have emailed %s (%s bytes)",
                 to, len(pdf_bytes))
        return {
            "message_id": f"mock-{int(datetime.now(timezone.utc).timestamp())}",
            "provider": "mock",
            "to": to,
            "sender": sender,
        }

    import base64
    import resend
    params = {
        "from": sender,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "attachments": [{
            "filename": pdf_filename,
            "content": base64.b64encode(pdf_bytes).decode("ascii"),
        }],
    }
    try:
        sent = await asyncio.to_thread(resend.Emails.send, params)
    except Exception as exc:
        log.error("Resend send failure: %s", exc)
        raise
    return {
        "message_id": sent.get("id"),
        "provider": "resend",
        "to": to,
        "sender": sender,
    }


def render_statement_email_html(*, patient: dict, statement: dict) -> str:
    """Minimal inline-CSS HTML body (no external fonts/images)."""
    name = " ".join(filter(None, [patient.get("first_name"),
                                  patient.get("last_name")])) or "Patient"
    total = statement.get("total_balance_cents", 0)
    return (
        "<div style=\"font-family:Helvetica,Arial,sans-serif;"
        "color:#1f2937;max-width:560px;margin:0 auto;padding:24px;\">"
        f"<h1 style=\"font-size:20px;margin:0 0 8px;\">Hi {name},</h1>"
        "<p style=\"font-size:14px;line-height:1.55;margin:0 0 16px;\">"
        f"Your current balance is <strong>${total / 100:.2f}</strong>. "
        "Your detailed statement is attached as a PDF."
        "</p>"
        "<p style=\"font-size:13px;color:#6b7280;margin:16px 0 0;\">"
        "Please remit within 30 days of receipt. If you have questions, "
        "reply to this email or contact the clinic billing office."
        "</p></div>"
    )
