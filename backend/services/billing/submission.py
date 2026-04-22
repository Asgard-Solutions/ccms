"""
services/billing/submission.py — Phase 4 claim submission scaffolding.

Two responsibilities:
  1. Build an export-ready payload for a claim (JSON primary + a simple
     ANSI X12 837P preview for eventual clearinghouse adapters).
  2. Provide the `followup_claim_ids()` helper used by the Follow-up
     work queue.

No clearinghouse I/O is performed — the payloads are persisted on the
submission record and exposed via the API so operators can hand them to
payer portals / paper mailrooms manually.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Follow-up rule: a claim that has been sitting in `submitted` (no
# outcome recorded) beyond this many days OR a claim in `rejected` /
# `denied` with no new submission attempt beyond this many days.
DEFAULT_FOLLOWUP_DAYS = 14


def build_json_payload(
    *,
    claim: dict,
    diagnoses: list[dict],
    lines: list[dict],
    patient: dict | None,
    payer: dict | None,
    policy: dict | None,
    billing_provider: dict | None = None,
    rendering_provider: dict | None = None,
    service_facility: dict | None = None,
) -> dict[str, Any]:
    """Flat JSON export of the claim — convenient for inspection, debug,
    and portal uploads. Intentionally omits PHI beyond what the payer
    already needs (name, DOB, member_id).

    Emits **business identifiers**, not internal DB UUIDs — the
    payload is meant to look like what a human biller would see:
      * patient.mrn (medical record number), not patient.id
      * payer.payer_id (external payer code), not payer.id
      * billing_provider_npi / rendering_provider_npi / facility_npi
        instead of UUID references
      * policy.member_id / group_number (internal policy UUID omitted)
    """
    def _npi(obj: dict | None) -> str | None:
        if not obj:
            return None
        v = obj.get("npi")
        return str(v).strip() if v else None

    payer_code = (
        (payer or {}).get("payer_code")
        or (payer or {}).get("external_id")
        or (payer or {}).get("code")
    ) if payer else None
    electronic_payer_id = (
        (payer or {}).get("electronic_payer_id")
        or (payer or {}).get("external_id")
    ) if payer else None

    return {
        "schema": "ccms.claim.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim": {
            # Public claim reference — the biller-facing number, not
            # the Mongo UUID. Falls back to `id` only when the demo /
            # test fixture hasn't assigned a patient_control_number.
            "control_number": (
                claim.get("patient_control_number")
                or claim.get("control_number")
                or claim.get("id")
            ),
            "claim_type": claim.get("claim_type"),
            "place_of_service": claim.get("place_of_service"),
            "frequency_code": claim.get("frequency_code"),
            "service_date_from": claim.get("service_date_from"),
            "service_date_to": claim.get("service_date_to"),
            "billed_cents": claim.get("billed_cents"),
            "billing_provider_npi": _npi(billing_provider),
            "rendering_provider_npi": _npi(rendering_provider),
            "facility_npi": _npi(service_facility),
            "authorization_number": claim.get("authorization_number"),
            "referral_number": claim.get("referral_number"),
            "notes": claim.get("notes"),
        },
        "patient": None if not patient else {
            # Medical Record Number when stored; otherwise a short
            # readable reference derived from the internal UUID so
            # the payload never leaks a Mongo UUID.
            "mrn": (
                patient.get("mrn")
                or patient.get("medical_record_number")
                or (f"RB-{patient['id'][:8].upper()}"
                    if patient.get("id") else None)
            ),
            "first_name": patient.get("first_name"),
            "last_name": patient.get("last_name"),
            "date_of_birth": patient.get("date_of_birth"),
            "gender": patient.get("gender"),
        },
        "payer": None if not payer else {
            "payer_code": payer_code,
            "electronic_payer_id": electronic_payer_id,
            "name": payer.get("name"),
            "payer_type": payer.get("payer_type"),
        },
        "policy": None if not policy else {
            "rank": policy.get("rank"),
            "member_id": policy.get("member_id"),
            "group_number": policy.get("group_number"),
            "subscriber_name": policy.get("subscriber_name"),
        },
        "diagnoses": [
            {"sequence": d.get("sequence"), "code": d.get("code")}
            for d in diagnoses
        ],
        "lines": [
            {
                "sequence": ln.get("sequence"),
                "service_date": ln.get("service_date"),
                "code_type": ln.get("code_type"),
                "code": ln.get("code"),
                "units": ln.get("units"),
                "billed_cents": ln.get("billed_cents"),
                "diagnosis_pointers": ln.get("diagnosis_pointers") or [],
                "modifiers": ln.get("modifiers") or [],
            }
            for ln in lines
        ],
    }


def build_x12_837p_preview(
    *,
    claim: dict,
    diagnoses: list[dict],
    lines: list[dict],
    patient: dict | None,
    payer: dict | None,
    policy: dict | None,
    billing_provider: dict | None = None,
) -> str:
    """Lightweight ANSI X12 837P **preview** — enough to show the shape
    and validate segment counts; intentionally NOT transmission-ready.

    Segments we emit:
      ISA / GS envelope (dummy control numbers)
      ST 837 / BHT / NM1 billing provider / NM1 subscriber / ...
      CLM / HI (diagnoses) / LX + SV1 + DTP per line
      SE / GE / IEA trailers

    Lines are `~` terminated for readability.
    """
    seg: list[str] = []

    def s(*fields):
        seg.append("*".join("" if f is None else str(f) for f in fields))

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    ctl = (claim.get("id") or "CTRL00000")[:9]

    # Interchange envelope
    s("ISA", "00", "          ", "00", "          ",
      "ZZ", "CCMS".ljust(15), "ZZ", "PAYER".ljust(15),
      today[2:], "1200", "^", "00501", ctl, "0", "T", ":")
    s("GS", "HC", "CCMS", "PAYER", today, "1200", "1", "X", "005010X222A1")
    s("ST", "837", "0001", "005010X222A1")
    s("BHT", "0019", "00", claim.get("id", "")[:30], today, "1200", "CH")

    # Billing provider — prefer the resolved provider-directory row;
    # never fall back to `claim.billing_provider_id` (a UUID) because
    # that value would show up on the preview as the NPI field.
    bp_npi = (billing_provider or {}).get("npi") or ""
    if not (len(bp_npi) == 10 and bp_npi.isdigit()):
        bp_npi = ""  # empty is safer than leaking a UUID
    bp_name = (billing_provider or {}).get("name") or "CCMS BILLING"
    s("NM1", "85", "2", bp_name, "", "", "", "", "XX", bp_npi)

    # Subscriber / patient
    if patient:
        s("NM1", "IL", "1",
          (patient.get("last_name") or "").upper(),
          (patient.get("first_name") or "").upper(),
          "", "", "",
          "MI", (policy or {}).get("member_id") or "")

    # Payer
    if payer:
        s("NM1", "PR", "2",
          (payer.get("name") or "").upper(),
          "", "", "", "",
          "PI", payer.get("external_id") or "")

    # Claim
    billed = (claim.get("billed_cents") or 0) / 100.0
    pos = claim.get("place_of_service") or "11"
    s("CLM", claim.get("id", "")[:38], f"{billed:.2f}", "",
      "", f"{pos}:B:{claim.get('frequency_code') or '1'}",
      "Y", "A", "Y", "Y")

    # Diagnoses (HI segment). Only first 12 are supported by 837P spec.
    if diagnoses:
        hi_parts = ["HI"]
        for i, d in enumerate(diagnoses[:12]):
            qual = "ABK" if i == 0 else "ABF"
            hi_parts.append(f"{qual}:{(d.get('code') or '').replace('.', '')}")
        seg.append("*".join(hi_parts))

    # Lines
    for ln in lines:
        s("LX", ln.get("sequence"))
        charge = (ln.get("billed_cents") or 0) / 100.0
        modifiers = (ln.get("modifiers") or [])[:4]
        mod_tail = ":".join(modifiers)
        code_composite = (
            f"HC:{ln.get('code') or ''}:{mod_tail}" if mod_tail
            else f"HC:{ln.get('code') or ''}"
        )
        ptrs = ln.get("diagnosis_pointers") or []
        s("SV1", code_composite, f"{charge:.2f}", "UN",
          ln.get("units") or 1, "", "",
          ":".join(str(p) for p in ptrs))
        if ln.get("service_date"):
            s("DTP", "472", "D8",
              ln["service_date"].replace("-", ""))

    # Trailers
    segment_count = len(seg) - 2   # exclude ISA/GS envelope
    s("SE", segment_count + 1, "0001")
    s("GE", "1", "1")
    s("IEA", "1", ctl)

    return "~\n".join(seg) + "~"


def followup_threshold_iso(days: int = DEFAULT_FOLLOWUP_DAYS) -> str:
    """Return ISO-8601 UTC timestamp `days` ago."""
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def followup_claim_ids(
    db, tenant_id: str, days: int = DEFAULT_FOLLOWUP_DAYS,
) -> list[str]:
    """Return claim IDs that need follow-up.

    Rule (user spec 2c — both a and b):
      a. status == 'submitted' AND (last_submission_at < cutoff OR
         submitted_at < cutoff) AND no outcome has been recorded
         on the latest submission.
      b. status in {'rejected', 'denied'} AND updated_at < cutoff AND
         no new submission created after the status change.
    """
    cutoff = followup_threshold_iso(days)

    # (a) submitted & stale
    a_cursor = db.claims.find(
        {"tenant_id": tenant_id, "status": "submitted",
         "$or": [
             {"last_submission_at": {"$lt": cutoff}},
             {"last_submission_at": {"$exists": False}},
         ]},
        {"_id": 0, "id": 1},
    )
    a_ids = {c["id"] async for c in a_cursor}

    # (b) rejected/denied & stale
    b_cursor = db.claims.find(
        {"tenant_id": tenant_id,
         "status": {"$in": ["rejected", "denied"]},
         "updated_at": {"$lt": cutoff}},
        {"_id": 0, "id": 1},
    )
    b_ids = {c["id"] async for c in b_cursor}

    return sorted(a_ids | b_ids)
