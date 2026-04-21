"""
Patient search endpoint — `/api/patients/search`.

Implements the lookup-first workflow replacing the old full-list dump on
the Patients page. Designed to preserve the HIPAA posture:

  * Name + top-level phone/email are searched with Mongo regex (they are
    stored in plaintext today — low-leak fields).
  * DOB, address, and the `contact.{home,cell,work}_phone` sub-fields are
    encrypted at rest. We search them via a **post-decrypt filter** on a
    bounded candidate set (`_CANDIDATE_CAP`). This keeps ciphertext at
    rest and avoids adding new plaintext PHI indexes.
  * Search results are ALWAYS returned in the masked shape — callers who
    need unmasked detail must open the patient record (already audited).
  * SQL-style `%` wildcards are supported and translated to regex safely.

Tenant + location scoping, permission checks, and audit logging match the
rest of the patient service.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.db import get_db_read
from core.deps import get_current_user
from core.masking import mask_patient
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter
from services.patient._shared import decrypt_patient_doc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["patient-search"])

_CANDIDATE_CAP = 2000     # Max rows we will decrypt in one search.
_PAGE_LIMIT_MAX = 50      # Hard cap on rows returned to the client.
_WILDCARD_CHAR = "%"      # SQL-style — translated to regex `.*`.
_DOB_FORMATS = (
    r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$",   # 1985-01-15
    r"^(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{4})$",   # 01/15/1985
    r"^(?P<m>\d{1,2})-(?P<d>\d{1,2})-(?P<y>\d{4})$",   # 01-15-1985
    r"^(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})$", # 15.01.1985 (EU)
)


def _wildcard_to_regex(value: str, *, anchored: bool = False) -> re.Pattern[str] | None:
    """Translate a user-entered search term into a compiled case-insensitive
    regex. Returns None when the input is empty after normalisation.

    Semantics:
      * Non-regex metacharacters are escaped via `re.escape`.
      * `%` is the user-visible wildcard — it becomes `.*`.
      * When `anchored=True`, the pattern matches the whole string; when
        False (default), it matches anywhere in the string (case-insensitive
        "contains").
    """
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    # Ban adjacent multi-percent input and control chars to avoid catastrophic
    # regex backtracking ("%%%%...").
    if "%%" in v:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid wildcard — `%%` is not allowed; use a single `%` per wildcard.",
        )
    if any(ord(c) < 32 for c in v):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Search contains control characters.")
    if len(v) > 120:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Search is too long (max 120 chars).")

    # Replace `%` with an unlikely placeholder before re.escape (because
    # `%` is NOT a regex metacharacter, re.escape leaves it untouched), then
    # swap the placeholder for `.*` after escaping.
    placeholder = "\x00WILDCARD\x00"
    escaped = re.escape(v.replace("%", placeholder)).replace(placeholder, ".*")
    if anchored:
        pattern = f"^{escaped}$"
    else:
        # If the user typed any wildcard, honour it verbatim; otherwise
        # fall back to a "contains" anchor so short input still matches.
        pattern = escaped if "%" in v else f".*{escaped}.*"
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid search expression: {exc}")


def _digits_only(s: Any) -> str:
    if not s:
        return ""
    return re.sub(r"\D", "", str(s))


def _phone_regex(value: str) -> re.Pattern[str] | None:
    """Phones are matched by digit substring (ignoring formatting). Supports
    `%` as a bridge between digit sequences (e.g. `555%4567`)."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    # Preserve `%` then strip all non-digit, non-% characters.
    cleaned = re.sub(r"[^0-9%]", "", v)
    if not cleaned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Phone search must contain digits.")
    return _wildcard_to_regex(cleaned, anchored=False)


def _parse_dob(value: str) -> str | None:
    """Normalise a DOB input to ISO YYYY-MM-DD. Accepts several formats.
    Returns None when the input is blank; raises 400 on unrecognised shapes."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    for pat in _DOB_FORMATS:
        m = re.match(pat, v)
        if m:
            y, mo, d = int(m["y"]), int(m["m"]), int(m["d"])
            if not (1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "DOB out of range.")
            return f"{y:04d}-{mo:02d}-{d:02d}"
    # Accept leading partial year (e.g., "1985") for "search all 1985 births".
    if re.match(r"^\d{4}$", v):
        return v  # treated as a contains-match on the year
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "DOB must be `YYYY-MM-DD`, `MM/DD/YYYY`, `MM-DD-YYYY`, `DD.MM.YYYY`, or a 4-digit year.",
    )


def _extract_phones(decrypted: dict) -> list[str]:
    """Collect every phone number on a patient (top-level + contact group)."""
    phones: list[str] = []
    for key in ("phone", "phone_alt", "phone_work"):
        v = decrypted.get(key)
        if v:
            phones.append(str(v))
    contact = decrypted.get("contact")
    if isinstance(contact, dict):
        for key in ("phone", "phone_alt", "phone_work"):
            v = contact.get(key)
            if v:
                phones.append(str(v))
    return phones


def _extract_address_blob(decrypted: dict) -> str:
    """Flatten every address form into a single lower-cased blob for
    substring / regex matching."""
    parts: list[str] = []
    v = decrypted.get("address")
    if isinstance(v, str):
        parts.append(v)
    details = decrypted.get("address_details")
    if isinstance(details, dict):
        for k in ("line1", "line2", "city", "state", "postal_code", "country"):
            if details.get(k):
                parts.append(str(details[k]))
    contact = decrypted.get("contact")
    if isinstance(contact, dict):
        ca = contact.get("address_details")
        if isinstance(ca, dict):
            for k in ("line1", "line2", "city", "state", "postal_code", "country"):
                if ca.get(k):
                    parts.append(str(ca[k]))
    return " ".join(parts).lower()


def _result_shape(decrypted: dict, *, mask: bool) -> dict:
    """Trimmed projection for the search result list — identifying fields
    only, never the full intake sections."""
    source = mask_patient(decrypted) if mask else decrypted
    primary_phone = (
        decrypted.get("phone")
        or (decrypted.get("contact") or {}).get("phone")
        if decrypted.get("contact")
        else decrypted.get("phone")
    )
    addr_short = ""
    ad = decrypted.get("address_details") if isinstance(decrypted.get("address_details"), dict) else None
    if ad:
        city = ad.get("city") or ""
        state = ad.get("state") or ""
        postal = ad.get("postal_code") or ""
        addr_short = ", ".join(p for p in [city, state, postal] if p)
    if not addr_short and isinstance(decrypted.get("address"), str):
        addr_short = decrypted["address"][:80]
    return {
        "id": decrypted.get("id"),
        "first_name": source.get("first_name"),
        "last_name": source.get("last_name"),
        "display_name_masked": source.get("display_name_masked"),
        "date_of_birth": source.get("date_of_birth"),
        "primary_phone": source.get("phone") if mask else primary_phone,
        "address_summary": addr_short if not mask else "—",
        "gender": source.get("gender"),
        "status": decrypted.get("status"),
        "created_at": decrypted.get("created_at"),
    }


def _build_name_filter(name: str | None, q_global: str | None) -> dict:
    """Build the $or clause for plaintext name/phone/email matching."""
    target = (name or q_global or "").strip()
    if not target:
        return {}
    rx = _wildcard_to_regex(target)
    if rx is None:
        return {}
    # Compose an OR across plaintext columns.
    or_clauses = [
        {"first_name": {"$regex": rx.pattern, "$options": "i"}},
        {"last_name": {"$regex": rx.pattern, "$options": "i"}},
        {"email": {"$regex": rx.pattern, "$options": "i"}},
    ]
    # Phone / id-prefix only fires for the global "q" path.
    if q_global and not name:
        or_clauses.append({"phone": {"$regex": rx.pattern, "$options": "i"}})
        or_clauses.append({"id": {"$regex": f"^{re.escape(target)}", "$options": "i"}})
    return {"$or": or_clauses}


_STAFF_ROLES = ("admin", "doctor", "staff")


@router.get("/search")
async def search_patients(
    request: Request,
    q: str | None = Query(default=None, description="Global search across name / email / phone / id prefix."),
    name: str | None = Query(default=None),
    phone: str | None = Query(default=None),
    address: str | None = Query(default=None),
    dob: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=_PAGE_LIMIT_MAX),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Lookup-style search. Returns a masked, trimmed projection.

    Rules:
      * At least one of `q / name / phone / address / dob` is required.
      * `%` is the wildcard character (SQL-style). Case-insensitive.
      * DOB accepts `YYYY-MM-DD`, `MM/DD/YYYY`, `MM-DD-YYYY`, `DD.MM.YYYY`,
        or a 4-digit year.
      * Staff/doctor see tenant-scoped results. Patients see only their own
        record (mirrors list endpoint).
    """
    if user["role"] not in _STAFF_ROLES and user["role"] != "patient" and not ctx.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    # Reject all-blank search.
    if not any([q, name, phone, address, dob]):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one search term (q, name, phone, address, or dob).",
        )

    mongo_filter: dict = {"status": {"$ne": "deleted"}}

    # Role-scoped baseline.
    if user["role"] == "patient":
        mongo_filter["user_id"] = user["id"]

    # Mongo-level filter only narrows on plaintext name / email / global `q`.
    # Phone / address / DOB are evaluated in the post-decrypt loop since
    # they can live inside encrypted `contact` / `address_details` blobs.
    name_part = _build_name_filter(name, q)
    mongo_filter.update(name_part)

    mongo_filter = scoped_filter(mongo_filter, ctx, location_scoped=True)
    if mongo_filter.get("__deny__"):
        return _empty_response("deny")

    # Compile post-decrypt patterns once.
    phone_rx = _phone_regex(phone) if phone else None
    address_rx = _wildcard_to_regex(address) if address else None
    dob_value = _parse_dob(dob) if dob else None

    db = get_db_read()
    # Fetch a bounded candidate set. Sort newest first.
    cursor = db.patients.find(mongo_filter, {"_id": 0}).sort("created_at", -1).limit(_CANDIDATE_CAP + 1)
    candidates = [doc async for doc in cursor]
    truncated = len(candidates) > _CANDIDATE_CAP
    if truncated:
        candidates = candidates[:_CANDIDATE_CAP]

    matches: list[dict] = []
    for raw in candidates:
        try:
            decrypted = decrypt_patient_doc(raw)
        except Exception:  # noqa: BLE001 — corrupt row skipped (and logged)
            logger.exception("search: failed to decrypt patient id=%s", raw.get("id"))
            continue

        # Post-decrypt: phone (top-level + encrypted sub-phones).
        if phone_rx and not _match_phones(phone_rx, decrypted):
            continue

        # Post-decrypt: address (any sub-field).
        if address_rx and not address_rx.search(_extract_address_blob(decrypted)):
            continue

        # Post-decrypt: DOB.
        if dob_value:
            raw_dob = str(decrypted.get("date_of_birth") or "")
            if len(dob_value) == 4:
                if dob_value not in raw_dob:
                    continue
            else:
                if not raw_dob.startswith(dob_value):
                    continue

        matches.append(decrypted)

    total = len(matches)
    page = matches[offset : offset + limit]

    # Determine mask posture: match list-endpoint defaults. Search never
    # unmasks automatically — callers unmask per-patient via the detail view.
    mask = True  # Always masked in search; the detail page still audits unmasks.
    shaped = [_result_shape(m, mask=mask) for m in page]

    await audit_success(
        user, "patient.searched", request,
        entity_type="patient", phi_accessed=False,
        metadata={
            "fields": sorted(k for k, v in {
                "q": q, "name": name, "phone": phone, "address": address, "dob": dob,
            }.items() if v),
            "returned": len(shaped),
            "total_matches": total,
            "truncated_candidates": truncated,
        },
    )

    return {
        "results": shaped,
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated_candidates": truncated,
        "candidate_cap": _CANDIDATE_CAP,
    }


def _match_phones(rx: re.Pattern[str], decrypted: dict) -> bool:
    phones = _extract_phones(decrypted)
    for p in phones:
        if rx.search(_digits_only(p)):
            return True
    # Also try raw strings (preserves `%` anchoring for masked/formatted phones).
    return any(rx.search(str(p)) for p in phones)


def _empty_response(reason: str) -> dict:
    return {
        "results": [], "total": 0, "offset": 0, "limit": 0,
        "truncated_candidates": False, "candidate_cap": _CANDIDATE_CAP,
        "deny_reason": reason,
    }
