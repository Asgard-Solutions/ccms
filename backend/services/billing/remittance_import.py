"""
services/billing/remittance_import.py — Phase 6 bulk remittance import.

Accepts either:
  * X12 835 EDI text (pipe-delimited, `~` segment terminator)
  * JSON in our `ccms.remit.import.v1` schema

Both are parsed into a neutral intermediate representation (IR) which
is then converted to a `RemittancePostRequest` — the same shape Phase
5 already validates and posts.

IR shape:
    {
        "header": {
            "payer_hint": str | None,      # payer name or external id
            "check_or_eft_number": str | None,
            "received_at": "YYYY-MM-DD",
            "total_paid_cents": int,
            "notes": str | None,
        },
        "claims": [
            {
                "payer_control_number": str | None,
                "patient_control_number": str | None,
                "claim_id_hint": str | None,
                "billed_cents": int,
                "paid_cents": int,
                "contractual_cents": int,
                "patient_resp_cents": int,
                "denied_cents": int,
                "denial_code": str | None,
                "lines": [{...}],
            },
        ],
    }

Matching: against existing tenant claims by (in order)
  1. explicit `claim_id_hint`
  2. `payer_control_number` == existing claim's
     `history[].external_reference`  (stored by Phase 4 submission)
  3. `patient_control_number` ≈ claim short id prefix
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

# Public schema name for JSON imports.
JSON_SCHEMA = "ccms.remit.import.v1"


# ---------------------------------------------------------------------------
# JSON ingestion
# ---------------------------------------------------------------------------
def parse_json_import(raw: bytes | str) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
    if data.get("schema") != JSON_SCHEMA:
        raise ValueError(
            f"Unsupported JSON schema: expected '{JSON_SCHEMA}'",
        )
    hdr = data.get("header") or {}
    claims_in = data.get("claims") or []
    if not claims_in:
        raise ValueError("No claims in import payload")

    def _int(v):
        return int(v or 0)

    claims: list[dict] = []
    for c in claims_in:
        claims.append({
            "payer_control_number": c.get("payer_control_number"),
            "patient_control_number": c.get("patient_control_number"),
            "claim_id_hint": c.get("claim_id"),
            "billed_cents": _int(c.get("billed_cents")),
            "paid_cents": _int(c.get("paid_cents")),
            "contractual_cents": _int(c.get("contractual_cents")),
            "patient_resp_cents": _int(c.get("patient_resp_cents")),
            "denied_cents": _int(c.get("denied_cents")),
            "denial_code": c.get("denial_code"),
            "lines": [{
                "claim_line_id": ln.get("claim_line_id"),
                "cpt_code": ln.get("cpt_code"),
                "billed_cents": _int(ln.get("billed_cents")),
                "paid_cents": _int(ln.get("paid_cents")),
                "contractual_cents": _int(ln.get("contractual_cents")),
                "patient_resp_cents": _int(ln.get("patient_resp_cents")),
                "denied_cents": _int(ln.get("denied_cents")),
                "denial_code": ln.get("denial_code"),
                "denial_category": ln.get("denial_category"),
            } for ln in (c.get("lines") or [])],
        })

    return {
        "source": "json",
        "header": {
            "payer_hint": hdr.get("payer_hint") or hdr.get("payer_name"),
            "payer_external_id": hdr.get("payer_external_id"),
            "check_or_eft_number": hdr.get("check_or_eft_number"),
            "received_at": hdr.get("received_at")
                or datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": _int(hdr.get("total_paid_cents")),
            "notes": hdr.get("notes"),
        },
        "claims": claims,
    }


# ---------------------------------------------------------------------------
# X12 835 parsing (minimal, pragmatic subset)
# ---------------------------------------------------------------------------
_SEGMENT_TERMINATORS = ("~", "\n")


def _split_segments(text: str) -> list[list[str]]:
    # 835 uses `~` as segment terminator; allow newline-split as a
    # convenience for human-authored test fixtures.
    cleaned = text.replace("\r", "")
    for term in _SEGMENT_TERMINATORS:
        if term in cleaned:
            parts = [p.strip() for p in cleaned.split(term) if p.strip()]
            break
    else:
        parts = [cleaned.strip()]
    return [p.split("*") for p in parts]


def parse_835(text: str) -> dict[str, Any]:
    """Parse a simplified 835 payload.

    Recognized segments (everything else ignored):
      ISA / GS               envelope (skipped)
      BPR                    financial information — total paid, method
      TRN                    trace/check number
      N1*PR                  payer name (one)
      DTM*405                production date — used for received_at
      CLP                    claim payment information
      CAS                    claim adjustment (CO/PR/OA + code amount)
      SVC                    service line
      LQ                     remark (ignored)
      SE / GE / IEA          trailers (skipped)
    """
    segments = _split_segments(text)

    header = {
        "payer_hint": None,
        "payer_external_id": None,
        "check_or_eft_number": None,
        "received_at": datetime.now(timezone.utc).date().isoformat(),
        "total_paid_cents": 0,
        "notes": None,
    }
    claims: list[dict] = []
    current_claim: dict | None = None
    current_line: dict | None = None

    def _cents(s: str) -> int:
        try:
            return round(float(s) * 100)
        except (ValueError, TypeError):
            return 0

    def _close_line():
        nonlocal current_line
        if current_line and current_claim is not None:
            current_claim["lines"].append(current_line)
            current_line = None

    def _close_claim():
        nonlocal current_claim
        _close_line()
        if current_claim is not None:
            claims.append(current_claim)
            current_claim = None

    for seg in segments:
        if not seg:
            continue
        tag = seg[0].upper()

        if tag == "BPR":
            # BPR*I*123.45*C*CHK*...*...
            if len(seg) >= 3:
                header["total_paid_cents"] = _cents(seg[2])
        elif tag == "TRN":
            # TRN*1*CHECKNUMBER*PAYERID
            if len(seg) >= 3:
                header["check_or_eft_number"] = seg[2] or None
            if len(seg) >= 4:
                header["payer_external_id"] = seg[3] or None
        elif tag == "N1" and len(seg) >= 3 and seg[1].upper() == "PR":
            header["payer_hint"] = seg[2] or None
        elif tag == "DTM" and len(seg) >= 3 and seg[1] == "405":
            # YYYYMMDD
            d = seg[2]
            if len(d) == 8:
                header["received_at"] = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        elif tag == "CLP":
            # CLP*claim_id*status*billed*paid*patient_resp*...*payer_ctl
            _close_claim()
            billed = _cents(seg[3]) if len(seg) > 3 else 0
            paid = _cents(seg[4]) if len(seg) > 4 else 0
            patient_resp = _cents(seg[5]) if len(seg) > 5 else 0
            payer_ctl = seg[7] if len(seg) > 7 else None
            current_claim = {
                "patient_control_number": seg[1] if len(seg) > 1 else None,
                "payer_control_number": payer_ctl or None,
                "claim_id_hint": None,
                "billed_cents": billed,
                "paid_cents": paid,
                "contractual_cents": 0,
                "patient_resp_cents": patient_resp,
                "denied_cents": 0,
                "denial_code": None,
                "lines": [],
            }
        elif tag == "CAS":
            # CAS*group*code*amount[*qty][...more triplets]
            # group: CO=contractual, PR=patient_resp, OA=other
            if current_claim is None:
                continue
            i = 1
            while i + 2 < len(seg):
                group = (seg[i] or "").upper()
                code = seg[i + 1] or ""
                amount = _cents(seg[i + 2])
                target = current_line if current_line is not None else current_claim
                if group == "CO":
                    if code == "97":
                        target["contractual_cents"] += amount
                    elif code == "45":
                        target["contractual_cents"] += amount
                    else:
                        # Generic CO: treat as denial if non-writedown
                        # adjustment codes; CARC semantics vary, but
                        # we default to denial+keep code for UI.
                        target["denied_cents"] += amount
                        target["denial_code"] = f"CO-{code}"
                elif group == "PR":
                    target["patient_resp_cents"] += amount
                elif group == "OA":
                    target["contractual_cents"] += amount
                i += 3
        elif tag == "SVC":
            # SVC*HC:CPT[:mods]*billed*paid*...*units
            _close_line()
            if current_claim is None:
                continue
            composite = seg[1] if len(seg) > 1 else ""
            cpt = None
            m = re.match(r"^HC:([A-Z0-9]+)", composite)
            if m:
                cpt = m.group(1)
            current_line = {
                "claim_line_id": None,
                "cpt_code": cpt,
                "billed_cents": _cents(seg[2]) if len(seg) > 2 else 0,
                "paid_cents": _cents(seg[3]) if len(seg) > 3 else 0,
                "contractual_cents": 0,
                "patient_resp_cents": 0,
                "denied_cents": 0,
                "denial_code": None,
                "denial_category": None,
            }
        elif tag in ("SE", "GE", "IEA"):
            _close_claim()

    _close_claim()

    if not claims:
        raise ValueError("835 payload contained no CLP segments")

    return {"source": "x12-835", "header": header, "claims": claims}


# ---------------------------------------------------------------------------
# Matching against existing tenant claims
# ---------------------------------------------------------------------------
async def match_claims(
    db, tenant_id: str, ir: dict,
) -> list[dict]:
    """For each IR claim, find the best matching tenant claim.

    Returns a list mirroring `ir["claims"]` with fields:
      * matched: bool
      * match_method: explicit | payer_control | patient_control | none
      * claim_id: str | None   (the real claim id if matched)
      * variance_cents: int    (billed_cents - claim.billed_cents; 0 when
                                no tenant claim was located)
    """
    results: list[dict] = []
    for item in ir["claims"]:
        match = {"matched": False, "match_method": "none",
                 "claim_id": None, "variance_cents": 0}

        # 1. Explicit claim_id.
        if item.get("claim_id_hint"):
            c = await db.claims.find_one(
                {"id": item["claim_id_hint"], "tenant_id": tenant_id},
                {"_id": 0, "id": 1, "billed_cents": 1, "payer_id": 1},
            )
            if c:
                match.update(matched=True, match_method="explicit",
                             claim_id=c["id"],
                             variance_cents=item["billed_cents"]
                                            - int(c.get("billed_cents") or 0))
                results.append(match)
                continue

        # 2. Payer control number — saved on Phase 4 submission rows.
        if item.get("payer_control_number"):
            sub = await db.claim_submissions.find_one(
                {"tenant_id": tenant_id,
                 "external_reference": item["payer_control_number"]},
                {"_id": 0, "claim_id": 1},
            )
            if sub:
                c = await db.claims.find_one(
                    {"id": sub["claim_id"], "tenant_id": tenant_id},
                    {"_id": 0, "id": 1, "billed_cents": 1},
                )
                if c:
                    match.update(matched=True, match_method="payer_control",
                                 claim_id=c["id"],
                                 variance_cents=item["billed_cents"]
                                                - int(c.get("billed_cents") or 0))
                    results.append(match)
                    continue

        # 3. Patient control number — 8-char prefix of claim id.
        pcn = (item.get("patient_control_number") or "").strip()
        if pcn and len(pcn) >= 6:
            # Match on id prefix.
            c = await db.claims.find_one(
                {"tenant_id": tenant_id,
                 "id": {"$regex": f"^{re.escape(pcn[:8])}"}},
                {"_id": 0, "id": 1, "billed_cents": 1},
            )
            if c:
                match.update(matched=True, match_method="patient_control",
                             claim_id=c["id"],
                             variance_cents=item["billed_cents"]
                                            - int(c.get("billed_cents") or 0))
        results.append(match)

    return results


async def resolve_payer_id(db, tenant_id: str, ir: dict) -> str | None:
    """Best-effort payer resolution from the IR header.

    Priority: electronic_payer_id → name prefix → None.
    """
    ext = ir["header"].get("payer_external_id")
    if ext:
        p = await db.billing_payers.find_one(
            {"tenant_id": tenant_id, "electronic_payer_id": ext},
            {"_id": 0, "id": 1},
        )
        if p:
            return p["id"]
    name = (ir["header"].get("payer_hint") or "").strip()
    if name:
        p = await db.billing_payers.find_one(
            {"tenant_id": tenant_id, "name": name}, {"_id": 0, "id": 1},
        )
        if p:
            return p["id"]
    return None
