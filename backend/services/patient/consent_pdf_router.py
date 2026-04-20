"""
Consent PDF router — /api/patients/{id}/consents/{type}/pdf

Renders a signed consent record as a one-page PDF with the patient's
typed + drawn signature for audit / export. PDFs are produced on demand
(never cached) so they always reflect the current consent row. Access
is authorised like GET /patients/{id}: patient-self or staff with a
break-glass reason.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from core.audit import audit_success
from core.consent_pdf import CONSENT_BODIES, render_consent_pdf
from core.deps import get_current_user
from core.tenancy import TenantContext, get_tenant_context
from services.patient._shared import (
    _patient_repo,
    decrypt_patient_doc,
    enforce_reason,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["patient-consents"])

CANONICAL_CONSENT_TYPES = set(CONSENT_BODIES.keys())


def _resolve_consent(consents: dict | None, consent_type: str) -> dict | None:
    """Return the consent record for the given type (or None)."""
    if not consents or not isinstance(consents, dict):
        return None
    if consent_type in CANONICAL_CONSENT_TYPES:
        record = consents.get(consent_type)
        if isinstance(record, dict):
            return record
        return None
    # Fallback: search `additional` list for a matching `type`.
    for extra in consents.get("additional") or []:
        if isinstance(extra, dict) and (extra.get("type") or "") == consent_type:
            return extra
    return None


@router.get("/{patient_id}/consents/{consent_type}/pdf")
async def download_consent_pdf(
    patient_id: str,
    consent_type: str,
    request: Request,
    reason: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Stream a signed-consent PDF for the given patient + consent type.

    Authorisation mirrors GET /patients/{id}: patient-self or staff with
    break-glass reason (for doctor/staff). All accesses are audited; PHI
    leaves the server only via this endpoint + the export endpoint.
    """
    p = await _patient_repo.find_one_by_id(patient_id, ctx)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")

    is_self = user["role"] == "patient" and p.get("user_id") == user["id"]
    if user["role"] == "patient" and not is_self:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    reason_required = user["role"] in ("doctor", "staff")
    enforced_reason = enforce_reason(reason, required=reason_required)

    decrypted = decrypt_patient_doc(p)
    consent = _resolve_consent(decrypted.get("consents"), consent_type)
    if not consent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consent not found on this patient")
    if not consent.get("accepted"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This consent has not been signed yet.",
        )

    try:
        pdf_bytes = render_consent_pdf(
            consent_type=consent_type,
            consent=consent,
            patient={
                "id": decrypted.get("id"),
                "first_name": decrypted.get("first_name"),
                "last_name": decrypted.get("last_name"),
                "date_of_birth": decrypted.get("date_of_birth"),
            },
        )
    except Exception:  # noqa: BLE001 — render failure surfaces as generic 500
        logger.exception("consent pdf render failed")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Unable to generate consent PDF. Please try again or contact support.",
        )

    await audit_success(
        user, "patient.consent.downloaded", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        reason=enforced_reason,
        metadata={"consent_type": consent_type, "bytes": len(pdf_bytes)},
    )
    filename = f"consent-{consent_type}-{patient_id[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
