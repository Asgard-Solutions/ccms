"""
services/billing/clearinghouse/x12_270_271.py
ASC X12N 270 / 271 Health Care Eligibility Benefit (005010X279A1).

Scope
-----
Translate the CCMS canonical patient/policy/provider/payer context
into a 270 eligibility REQUEST wire, and parse 271 RESPONSE wires back
into a canonical `EligibilityResult` shape.

The builder/parser are intentionally transport-agnostic: any adapter
(mock, Change Healthcare, Optum, Availity, …) can feed or receive X12
bytes here. No network I/O, no database calls — pure functions.

Segment coverage (005010X279A1, minimum viable subset)
------------------------------------------------------
Request (270):
  Envelope: ISA / GS / ST*270 / BHT / SE / GE / IEA
  2000A (Information Source): HL*1**20*1
    2100A (Payer):   NM1*PR*2*NAME*...*PI*payer_id
  2000B (Information Receiver): HL*2*1*21*1
    2100B (Provider): NM1*1P*2*NAME*...*XX*NPI
  2000C (Subscriber): HL*3*2*22*0
    2100C (Subscriber): NM1*IL*1*LAST*FIRST***MI*member_id
    TRN*1*trace*submitter
    DMG*D8*dob*gender
    DTP*291*D8*inquiry_date
    EQ*30                       (Health Benefit Plan Coverage)

Response (271) — parser recognises:
  ST*271
  2100A NM1*PR (payer echo)
  2100B NM1*1P (provider echo)
  2100C NM1*IL (subscriber name / member id)
  2100C DMG     (dob / gender)
  2110C EB*     (eligibility/benefit entries — core of the response)
           EB01: 1=active, 6=inactive, V=non-covered, I=non-covered,
                 A=co-insurance, B=copay, C=deductible, G=out-of-pocket
           EB03: service type code (30=plan, 98=prof visit, …)
           EB04: insurance type
           EB05: plan coverage description
           EB06: time period qualifier
           EB07: monetary amount
  2110C DTP*   (356=plan begin, 357=plan end, 291=plan active)
  2110C MSG*   (free-text messages)

The parser is deliberately forgiving — 271 responses vary widely
between payers. We ignore unknown EB qualifiers and surface any MSG
text as `notes`.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Separators (mirrors x12_837p.py)
# ---------------------------------------------------------------------------
ELEMENT_SEP = "*"
COMPONENT_SEP = ":"
REPETITION_SEP = "^"
SEGMENT_TERMINATOR = "~"
SEGMENT_JOIN = "~\n"


# ---------------------------------------------------------------------------
# Service type codes we care about for the eligibility inquiry. `30`
# (Health Benefit Plan Coverage) is the generic "tell me everything"
# request; additional codes can be appended as the UI grows richer.
# ---------------------------------------------------------------------------
SERVICE_TYPE_LABELS: dict[str, str] = {
    "30": "Health benefit plan",
    "1":  "Medical care",
    "33": "Chiropractic",
    "35": "Dental",
    "47": "Hospital",
    "48": "Hospital — inpatient",
    "50": "Hospital — outpatient",
    "86": "Emergency services",
    "88": "Pharmacy",
    "98": "Professional (physician) visit — office",
    "AL": "Vision (optometry)",
    "MH": "Mental health",
    "UC": "Urgent care",
}


EB_QUALIFIER_LABELS: dict[str, str] = {
    "1":  "Active coverage",
    "6":  "Inactive",
    "A":  "Co-insurance",
    "B":  "Copay",
    "C":  "Deductible",
    "G":  "Out-of-pocket (stop-loss)",
    "I":  "Non-covered",
    "V":  "Cannot process",
    "CB": "Coverage basis",
    "D":  "Benefit description",
    "F":  "Limitations",
    "J":  "Cost containment",
}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _pad(v: str | None, length: int) -> str:
    s = "" if v is None else str(v)
    return s[:length].ljust(length)


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _yyyymmdd(v: Any) -> str:
    if v in (None, ""):
        return ""
    s = str(v)
    if "T" in s:
        s = s.split("T", 1)[0]
    s = s.replace("-", "").replace("/", "")
    return s[:8]


def _yymmdd_hhmm(dt: datetime) -> tuple[str, str]:
    return dt.strftime("%y%m%d"), dt.strftime("%H%M")


def _upper(v: str | None, limit: int) -> str:
    if not v:
        return ""
    return str(v).strip().upper()[:limit]


_GENDER_MAP = {
    "male": "M", "m": "M",
    "female": "F", "f": "F",
    "u": "U", "unknown": "U", "non-binary": "U",
    "other": "U", "prefer-not-to-say": "U",
}


def _gender(v: str | None) -> str:
    return _GENDER_MAP.get((v or "").strip().lower(), "U")


def _money_from_str(v: str | None) -> int | None:
    """Parse an EB07 dollar amount (e.g. `500`, `1500.00`) into cents.
    Returns None when the value is blank/unparseable."""
    if v is None or str(v).strip() == "":
        return None
    try:
        return round(float(str(v).strip()) * 100)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 270 REQUEST builder
# ---------------------------------------------------------------------------
def build_270_request(
    *,
    submitter: dict,
    receiver: dict,
    provider: dict,
    payer: dict,
    patient: dict,
    policy: dict,
    service_type_codes: list[str] | None = None,
    inquiry_date: str | None = None,
    now: datetime | None = None,
    usage_indicator: str = "T",
) -> str:
    """Build a spec-compliant 270 eligibility inquiry wire.

    Required arguments
    ------------------
    submitter : {"id": ETIN, "name": "...", "contact_name": "...",
                 "contact_phone": "..."}
    receiver  : {"id": payer_id, "name": "..."}
    provider  : {"npi": "1234567890", "name": "...",
                 "entity_type": "person" | "org",
                 "last_name": "...", "first_name": "..."}
    payer     : {"name": "...", "electronic_payer_id": "..."}
    patient   : {"first_name", "last_name", "date_of_birth", "sex_at_birth"}
    policy    : {"member_id", "subscriber_name" (if not self),
                 "relationship_to_subscriber", ...}

    Optional
    --------
    service_type_codes : list of X12 service-type codes (default `["30"]`)
    inquiry_date       : ISO date (default: today)
    now                : envelope timestamp (default: utcnow)
    usage_indicator    : "T" (test) or "P" (production)
    """
    now = now or datetime.now(timezone.utc)
    inquiry_date = inquiry_date or now.strftime("%Y-%m-%d")
    svc_codes = service_type_codes or ["30"]
    segments: list[str] = []

    def seg(*fields: Any) -> None:
        rendered = [("" if f is None else str(f)) for f in fields]
        while len(rendered) > 1 and rendered[-1] == "":
            rendered.pop()
        segments.append(ELEMENT_SEP.join(rendered))

    # --- ISA envelope
    yy_mmdd, hhmm = _yymmdd_hhmm(now)
    isa13 = "".join(str(ord(c) % 10) for c in uuid.uuid4().hex[:9]).rjust(9, "0")
    seg(
        "ISA",
        "00", _pad("", 10),
        "00", _pad("", 10),
        "ZZ", _pad((submitter.get("id") or "SUB").upper(), 15),
        "ZZ", _pad((receiver.get("id") or "RCV").upper(), 15),
        yy_mmdd, hhmm,
        REPETITION_SEP,
        "00501",
        isa13,
        "0",
        usage_indicator,
        COMPONENT_SEP,
    )

    # --- GS envelope (HB = Eligibility, Coverage, or Benefit Inquiry)
    gs06 = isa13.lstrip("0") or "1"
    seg("GS", "HB",
        (submitter.get("id") or "SUB").upper(),
        (receiver.get("id") or "RCV").upper(),
        now.strftime("%Y%m%d"),
        now.strftime("%H%M"),
        gs06,
        "X", "005010X279A1")

    # --- ST / BHT header
    st_index = len(segments)
    seg("ST", "270", "0001", "005010X279A1")
    seg("BHT", "0022", "13", uuid.uuid4().hex[:30], now.strftime("%Y%m%d"),
        now.strftime("%H%M"))

    # --- 2000A Information source (payer)
    seg("HL", "1", "", "20", "1")
    seg("NM1", "PR", "2",
        _upper(payer.get("name") or "PAYER", 60), "", "", "", "",
        "PI", payer.get("electronic_payer_id") or receiver.get("id") or "")

    # --- 2000B Information receiver (provider)
    seg("HL", "2", "1", "21", "1")
    entity_type = (provider.get("entity_type") or "person").lower()
    if entity_type.startswith("org"):
        seg("NM1", "1P", "2",
            _upper(provider.get("name") or "PROVIDER", 60), "", "", "", "",
            "XX", provider.get("npi") or "")
    else:
        seg("NM1", "1P", "1",
            _upper(provider.get("last_name") or provider.get("name"), 60),
            _upper(provider.get("first_name"), 35), "", "", "",
            "XX", provider.get("npi") or "")

    # --- 2000C Subscriber
    seg("HL", "3", "2", "22", "0")

    # TRN — trace for round-trip correlation. Submitter-assigned id.
    trace = uuid.uuid4().hex[:30]
    seg("TRN", "1", trace, (submitter.get("id") or "SUB").upper())

    # 2100C NM1 — subscriber identity
    relationship = (policy.get("relationship_to_subscriber") or "self").lower()
    is_self = relationship == "self"
    if is_self:
        last = _upper(patient.get("last_name"), 60)
        first = _upper(patient.get("first_name"), 35)
    else:
        raw = (policy.get("subscriber_name") or "").strip()
        if " " in raw:
            parts = raw.rsplit(maxsplit=1)
            first = _upper(parts[0], 35)
            last = _upper(parts[1], 60)
        else:
            last = _upper(raw, 60)
            first = ""
    seg("NM1", "IL", "1", last, first, "", "", "",
        "MI", policy.get("member_id") or "")

    # 2100C DMG — DOB / gender (required when subscriber is patient)
    dob = patient.get("date_of_birth") or policy.get("subscriber_dob") or ""
    gender = _gender(
        patient.get("sex_at_birth") if is_self
        else policy.get("subscriber_gender") or patient.get("sex_at_birth"),
    )
    if _yyyymmdd(dob):
        seg("DMG", "D8", _yyyymmdd(dob), gender)

    # DTP*291 — eligibility inquiry date
    seg("DTP", "291", "D8", _yyyymmdd(inquiry_date))

    # 2110C EQ — one per service-type inquired
    for code in svc_codes:
        seg("EQ", code)

    # --- SE trailer — count ST..SE inclusive
    segment_count = len(segments) - st_index + 1  # +1 for SE itself
    seg("SE", str(segment_count), "0001")

    # --- GE / IEA
    seg("GE", "1", gs06)
    seg("IEA", "1", isa13)

    wire = SEGMENT_JOIN.join(segments) + SEGMENT_TERMINATOR
    return wire


# ---------------------------------------------------------------------------
# 271 RESPONSE parser
# ---------------------------------------------------------------------------
_SEG_SPLIT = re.compile(r"~\s*")


def parse_271_response(wire: str) -> dict[str, Any]:
    """Parse a 271 eligibility response wire into a canonical dict.

    Returns a dict shaped like the `EligibilityResult`:

        {
            "transaction_type": "271",
            "trace_number": "...",
            "coverage_active": bool,
            "payer_name": "...",
            "payer_id": "...",
            "provider_name": "...",
            "provider_npi": "...",
            "subscriber_name": "LAST FIRST",
            "member_id": "...",
            "date_of_birth": "YYYY-MM-DD" | None,
            "gender": "M" | "F" | "U" | None,
            "plan_name": "...",
            "effective_date": "YYYY-MM-DD" | None,
            "termination_date": "YYYY-MM-DD" | None,
            "copay_cents": int | None,
            "deductible_cents": int | None,
            "deductible_met_cents": int | None,
            "coinsurance_pct": int | None,
            "out_of_pocket_cents": int | None,
            "benefits": [ {qualifier, label, service_type, service_type_label,
                           amount_cents, percent, plan, message, time_period} ],
            "messages": [...],
        }
    """
    out: dict[str, Any] = {
        "transaction_type": "271",
        "trace_number": None,
        "coverage_active": False,
        "payer_name": None, "payer_id": None,
        "provider_name": None, "provider_npi": None,
        "subscriber_name": None, "member_id": None,
        "date_of_birth": None, "gender": None,
        "plan_name": None,
        "effective_date": None, "termination_date": None,
        "copay_cents": None, "deductible_cents": None,
        "deductible_met_cents": None,
        "coinsurance_pct": None, "out_of_pocket_cents": None,
        "benefits": [],
        "messages": [],
    }

    for raw in _SEG_SPLIT.split(wire or ""):
        if not raw or not raw.strip():
            continue
        fields = raw.split(ELEMENT_SEP)
        tag = fields[0].strip()
        if tag == "TRN" and len(fields) >= 3:
            out["trace_number"] = fields[2]
        elif tag == "NM1" and len(fields) >= 4:
            role = fields[1]
            name_last = fields[3]
            name_first = fields[4] if len(fields) > 4 else ""
            id_qual = fields[8] if len(fields) > 8 else ""
            id_val = fields[9] if len(fields) > 9 else ""
            if role == "PR":
                out["payer_name"] = name_last
                if id_qual == "PI":
                    out["payer_id"] = id_val
            elif role == "1P":
                out["provider_name"] = " ".join(
                    [p for p in (name_first, name_last) if p],
                ) or name_last
                if id_qual == "XX":
                    out["provider_npi"] = id_val
            elif role == "IL":
                out["subscriber_name"] = " ".join(
                    [p for p in (name_first, name_last) if p],
                ) or name_last
                if id_qual == "MI":
                    out["member_id"] = id_val
        elif tag == "DMG" and len(fields) >= 4:
            dob_raw = fields[2]
            if fields[1] == "D8" and len(dob_raw) == 8:
                out["date_of_birth"] = (
                    f"{dob_raw[0:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}"
                )
            out["gender"] = fields[3] or None
        elif tag == "EB" and len(fields) >= 2:
            entry = _parse_eb(fields)
            out["benefits"].append(entry)
            q = entry.get("qualifier")
            if q == "1":
                out["coverage_active"] = True
            elif q == "6" or q == "I":
                # Hard "inactive" at the plan level wins.
                if entry.get("service_type") in (None, "", "30"):
                    out["coverage_active"] = False
            # Pull the top-line financials out of the benefits list so
            # the UI can render a snapshot without iterating.
            amt = entry.get("amount_cents")
            pct = entry.get("percent")
            st = entry.get("service_type")
            if q == "B" and amt is not None and st in (None, "", "30", "98"):
                out["copay_cents"] = amt
            elif q == "C" and amt is not None:
                tp = entry.get("time_period")
                # `29` = remaining, `23` = total calendar year, etc.
                if tp == "29":
                    # "Remaining deductible" — we derive `met` from this.
                    if out["deductible_cents"] is not None:
                        out["deductible_met_cents"] = max(
                            0, out["deductible_cents"] - amt,
                        )
                else:
                    out["deductible_cents"] = amt
            elif q == "A" and pct is not None:
                out["coinsurance_pct"] = pct
            elif q == "G" and amt is not None:
                out["out_of_pocket_cents"] = amt
            elif q == "CB" and entry.get("plan"):
                out["plan_name"] = entry["plan"]
        elif tag == "DTP" and len(fields) >= 4:
            qual, fmt, val = fields[1], fields[2], fields[3]
            iso = _dtp_to_iso(fmt, val)
            if not iso:
                continue
            if qual == "356":
                out["effective_date"] = iso[0] if isinstance(iso, tuple) else iso
            elif qual == "357":
                out["termination_date"] = iso[1] if isinstance(iso, tuple) else iso
            elif qual == "291" and isinstance(iso, tuple):
                out["effective_date"] = out["effective_date"] or iso[0]
                out["termination_date"] = out["termination_date"] or iso[1]
        elif tag == "MSG" and len(fields) >= 2:
            if fields[1]:
                out["messages"].append(fields[1])

    # Mirror plan name into the `plan_name` field when an EB*1 carried
    # the plan description in EB05 without a CB qualifier.
    if not out["plan_name"]:
        for b in out["benefits"]:
            if b.get("qualifier") == "1" and b.get("plan"):
                out["plan_name"] = b["plan"]
                break

    return out


def _parse_eb(fields: list[str]) -> dict[str, Any]:
    """Parse a single EB segment into a structured benefit row."""
    def g(i: int) -> str:
        return fields[i] if len(fields) > i else ""

    qualifier = g(1) or None
    service_type = g(3) or None
    insurance_type = g(4) or None
    plan = g(5) or None
    time_period = g(6) or None
    amount = _money_from_str(g(7))
    percent_raw = g(8)
    try:
        percent = round(float(percent_raw) * 100) if percent_raw else None
    except ValueError:
        percent = None

    return {
        "qualifier": qualifier,
        "label": EB_QUALIFIER_LABELS.get(qualifier or "", qualifier or ""),
        "service_type": service_type,
        "service_type_label": SERVICE_TYPE_LABELS.get(
            service_type or "", service_type or "",
        ),
        "insurance_type": insurance_type,
        "plan": plan,
        "time_period": time_period,
        "amount_cents": amount,
        "percent": percent,
    }


def _dtp_to_iso(fmt: str, val: str) -> str | tuple[str, str] | None:
    """Convert a DTP date field into an ISO date (or date range)."""
    if not val:
        return None
    if fmt == "D8" and len(val) == 8:
        return f"{val[0:4]}-{val[4:6]}-{val[6:8]}"
    if fmt == "RD8" and len(val) == 17 and "-" in val:
        start, end = val.split("-", 1)
        if len(start) == 8 and len(end) == 8:
            return (
                f"{start[0:4]}-{start[4:6]}-{start[6:8]}",
                f"{end[0:4]}-{end[4:6]}-{end[6:8]}",
            )
    return None
