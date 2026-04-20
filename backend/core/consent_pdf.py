"""
Signed-consent PDF renderer.

Renders a single consent record (HIPAA, treatment, financial, telehealth,
photo-release, or a custom one stashed under `consents.additional`) into a
one-page PDF with the patient's typed signature, the wet-ink canvas PNG,
document version, acceptance timestamp, and IP. Bytes are returned in
memory so the caller can either stream them to the client or persist them
to object storage as a permanent audit record.

The output is intentionally lightweight — no external fonts, no network
calls — so it always works inside the container.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

# Body text for each canonical consent type. Kept short + auditable; real
# deployments can inject longer clinic-specific language via the grouped
# `consents.<type>.document_version` (which we stamp on the PDF).
CONSENT_BODIES: dict[str, tuple[str, str]] = {
    "hipaa": (
        "HIPAA Privacy Notice",
        "I acknowledge that I have received a copy of this practice's Notice of "
        "Privacy Practices, which describes how my protected health information "
        "(PHI) may be used and disclosed, and how I may obtain access to this "
        "information, in accordance with the Health Insurance Portability and "
        "Accountability Act of 1996 (HIPAA) and its implementing regulations.",
    ),
    "treatment": (
        "Consent to Treatment",
        "I voluntarily consent to chiropractic examination, diagnostic procedures, "
        "and treatment as recommended by the treating provider. I understand the "
        "nature of chiropractic adjustments and other therapies that may be "
        "administered, including the potential risks and benefits. I understand "
        "that I may withdraw my consent at any time.",
    ),
    "financial": (
        "Financial Policy Agreement",
        "I acknowledge financial responsibility for all services rendered. I "
        "understand co-pays, deductibles, and any balance not covered by "
        "insurance are due at the time of service. I authorise the practice to "
        "bill my insurance carrier(s) on my behalf and to release information "
        "necessary to process claims.",
    ),
    "telehealth": (
        "Telehealth Consent",
        "I consent to receiving healthcare services via telehealth. I understand "
        "the risks, benefits, and limitations of telehealth services, including "
        "the potential for technical failures. I understand that a telehealth "
        "visit is not a substitute for in-person care where such care is "
        "clinically indicated.",
    ),
    "photo_release": (
        "Photo & Imaging Release",
        "I authorise this practice to take, retain, and use clinical photographs "
        "and imaging for the purposes of my treatment, the patient record, and "
        "quality-of-care reviews. I understand my images will never be used for "
        "marketing without my separate written consent.",
    ),
}


def _friendly_type(consent_type: str) -> str:
    return (consent_type or "Consent").replace("_", " ").title()


def _format_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    except (ValueError, TypeError):
        return iso


def _decode_signature_image(data_url: str | None) -> bytes | None:
    if not data_url or not isinstance(data_url, str):
        return None
    try:
        if "," in data_url:
            data_url = data_url.split(",", 1)[1]
        return base64.b64decode(data_url)
    except (ValueError, TypeError) as exc:
        logger.warning("failed to decode signature image: %s", exc)
        return None


def render_consent_pdf(
    *,
    consent_type: str,
    consent: dict[str, Any],
    patient: dict[str, Any],
    clinic_name: str = "Chiropractic Clinic",
) -> bytes:
    """Render a single consent into a PDF; returns the raw PDF bytes.

    `consent` must be the decrypted ConsentRecord dict; `patient` is the
    decrypted patient document (or a trimmed copy of it).
    """
    title, body_text = CONSENT_BODIES.get(
        consent_type,
        (_friendly_type(consent.get("type") or consent_type), "This consent acknowledges the terms set out by the practice."),
    )

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title=f"{title} — {patient.get('first_name', '')} {patient.get('last_name', '')}".strip(),
        author=clinic_name,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18,
        textColor=colors.HexColor("#1F2924"), spaceAfter=6,
    )
    small = ParagraphStyle(
        "small", parent=styles["BodyText"], fontSize=9,
        textColor=colors.HexColor("#5C6A61"), leading=12,
    )
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=11,
        textColor=colors.HexColor("#1F2924"), leading=16, spaceAfter=12,
    )
    label = ParagraphStyle(
        "label", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=9,
        textColor=colors.HexColor("#5C6A61"), leading=12, spaceAfter=2,
    )

    story: list[Any] = []
    story.append(Paragraph(clinic_name.upper(), small))
    story.append(Paragraph(title, h1))
    story.append(Paragraph(
        f"Document version: {consent.get('document_version') or 'v1'} · "
        f"Generated {_format_dt(datetime.now(timezone.utc).isoformat())}",
        small,
    ))
    story.append(Spacer(1, 0.25 * inch))

    # Patient header block
    pt_name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip() or "—"
    patient_info = [
        ["Patient", pt_name],
        ["Date of birth", patient.get("date_of_birth") or "—"],
        ["Patient ID", patient.get("id") or "—"],
    ]
    tbl = Table(patient_info, colWidths=[1.3 * inch, 4.5 * inch])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5C6A61")),
        ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#1F2924")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.25 * inch))

    # Consent body
    story.append(Paragraph(body_text, body))

    # Acknowledgement block
    story.append(Paragraph("Acknowledgement", label))
    story.append(Paragraph(
        "By signing below, the patient (or their authorised representative) confirms "
        "they have read, understood, and accepted the terms of this consent.",
        small,
    ))
    story.append(Spacer(1, 0.2 * inch))

    # Signature lines — typed name on the left, drawn signature (if any) on the right.
    sig_bytes = _decode_signature_image(consent.get("signature_image"))
    left_cells = [
        [Paragraph("Signed by (typed)", label)],
        [Paragraph(consent.get("signature_name") or pt_name, body)],
        [Paragraph("Signed at", label)],
        [Paragraph(_format_dt(consent.get("signed_at")), body)],
        [Paragraph("Accepted", label)],
        [Paragraph("Yes" if consent.get("accepted") else "No", body)],
    ]
    if consent.get("ip_address"):
        left_cells.extend([
            [Paragraph("IP address", label)],
            [Paragraph(consent["ip_address"], body)],
        ])

    if sig_bytes:
        try:
            sig_img = Image(io.BytesIO(sig_bytes), width=2.6 * inch, height=1.1 * inch, kind="proportional")
        except (ValueError, OSError) as exc:
            logger.warning("signature render failed: %s", exc)
            sig_img = Paragraph("<i>Signature image unavailable.</i>", small)
    else:
        sig_img = Paragraph(
            "<i>No wet-ink signature captured — typed name above serves as the electronic signature.</i>",
            small,
        )

    sig_table = Table(
        [[left_cells, sig_img]],
        colWidths=[2.8 * inch, 3.0 * inch],
    )
    sig_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (1, 0), (1, 0), 0.5, colors.HexColor("#E5E7EB")),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("RIGHTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (1, 0), (1, 0), 8),
        ("BOTTOMPADDING", (1, 0), (1, 0), 8),
    ]))
    story.append(sig_table)

    story.append(Spacer(1, 0.35 * inch))
    story.append(Paragraph(
        "This document was generated from the patient's electronic record. "
        "Any alterations after signing invalidate this consent.",
        small,
    ))

    doc.build(story)
    return buf.getvalue()
