"""
services/billing/clearinghouse/x12_837p.py
Phase 7 — ASC X12N 837 Professional 005010X222A1 generator.

Scope
-----
Translate the CCMS canonical claim model into a wire-ready 837P document.
The module is deliberately portable:

  * Knows nothing about Change Healthcare / Optum / Availity / Waystar.
  * Accepts structured submitter + receiver + billing-provider inputs so
    any adapter can feed its own trading-partner identity.
  * Exposes the builder both as a low-level document primitive
    (`build_837p_document`) AND as a drop-in replacement for the existing
    preview function (`build_x12_837p_wire`) with the same keyword
    signature for easy substitution in the router.

Segment coverage (mandatory per 005010X222A1)
---------------------------------------------
  Envelope: ISA / GS / ST / BHT / SE / GE / IEA
  Loop 1000A (Submitter):  NM1*41, PER*IC
  Loop 1000B (Receiver):   NM1*40
  Loop 2000A (Billing HL): HL*1**20*1, PRV*BI (optional taxonomy)
  Loop 2010AA (Billing):   NM1*85, N3, N4, REF*EI (tax-id)
  Loop 2000B (Subscriber): HL*2*1*22*{0|1}, SBR
  Loop 2010BA (Subscriber):NM1*IL, N3, N4, DMG
  Loop 2010BB (Payer):     NM1*PR, (N3/N4 optional), REF*2U (external id)
  Loop 2000C (Patient):    HL / PAT / NM1*QC (only when patient != subscriber)
  Loop 2300 (Claim):       CLM, DTP*431 (onset), DTP*454 (initial tx),
                           DTP*439 (accident), REF*G1 (prior auth),
                           REF*9F (referral), HI (diagnoses)
  Loop 2310B (Rendering):  NM1*82, PRV*PE
  Loop 2310C (Facility):   NM1*77, N3, N4
  Loop 2400 (Service line):LX, SV1, DTP*472, REF*6R

The module is intentionally conservative: any optional segment that
does not have a corresponding value in the canonical model is simply
omitted rather than being stuffed with filler — this keeps payloads
lean and leaves unambiguous signals for scrubber rules.

Traceability
------------
Every returned document is pure text; the submission record persists
both the raw bytes and their SHA-256 (see Phase 6 `raw_837_hash`). No
PHI ever leaves this module in any representation other than the
canonical 837P stream the caller explicitly requests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Separators (005010X222A1 baseline — configurable if a payer ever asks)
# ---------------------------------------------------------------------------
ELEMENT_SEP = "*"
COMPONENT_SEP = ":"
REPETITION_SEP = "^"
SEGMENT_TERMINATOR = "~"
# Segments are joined with `~\n` so a human can eyeball the file;
# replace with plain `~` if a wire transport rejects line feeds.
SEGMENT_JOIN = "~\n"

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _pad(v: str | None, length: int) -> str:
    """ISA fixed-width helper — space-pad / truncate to `length`."""
    s = "" if v is None else str(v)
    return s[:length].ljust(length)


def _digits(v: Any) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _yyyymmdd(v: Any) -> str:
    """Accept `YYYY-MM-DD`, `YYYYMMDD`, or ISO timestamp; return
    `YYYYMMDD`. Returns `""` when input is blank / unparseable."""
    if v in (None, ""):
        return ""
    s = str(v)
    if "T" in s:
        s = s.split("T", 1)[0]
    s = s.replace("-", "").replace("/", "")
    return s[:8]


def _yymmdd_hhmm(dt: datetime) -> tuple[str, str]:
    return dt.strftime("%y%m%d"), dt.strftime("%H%M")


def _money(cents: int | None) -> str:
    """Charge/amount formatting for SV1/CLM — spec allows up to 2dp."""
    n = int(cents or 0)
    return f"{n/100:.2f}"


def _upper_no_specials(v: str | None, limit: int) -> str:
    """Names on the wire: uppercase, trim, keep within the length
    limit. Non-ASCII characters are passed through intact — payers
    differ on how strictly they enforce basic character set B, so we
    leave normalisation to the clearinghouse per its adapter."""
    if not v:
        return ""
    return str(v).strip().upper()[:limit]


def _icd10_naked(code: str | None) -> str:
    """ICD-10 codes on the wire must omit the decimal point."""
    if not code:
        return ""
    return str(code).replace(".", "").replace(" ", "")


_GENDER_MAP = {
    "male": "M", "m": "M",
    "female": "F", "f": "F",
    "non-binary": "U", "other": "U", "prefer-not-to-say": "U",
    "u": "U", "unknown": "U",
}


def _gender(v: str | None) -> str:
    return _GENDER_MAP.get((v or "").strip().lower(), "U")


def _relationship_code(rel: str | None) -> str:
    """Patient-to-subscriber relationship — 005010X222A1 values.
       18 = self, 01 = spouse, 19 = child, G8 = other."""
    mapping = {
        "self": "18",
        "spouse": "01",
        "child": "19",
        "other": "G8",
    }
    return mapping.get((rel or "self").strip().lower(), "G8")


def _claim_filing_indicator(payer_type: str | None) -> str:
    """SBR09 per 005010X222A1 — coarse mapping driven by our canonical
    payer_type. Defaults to CI (Commercial) when the type isn't in the
    standard catalog."""
    mapping = {
        "commercial": "CI",
        "medicare": "MB",
        "medicaid": "MC",
        "workers_comp": "WC",
        "auto": "AM",
        "self_pay": "09",
        "other": "11",
    }
    return mapping.get((payer_type or "commercial").strip().lower(), "CI")


def _addr_tuple(address: Any) -> tuple[str, str, str, str]:
    """Normalise our legacy-or-structured address shape into
    (street1, city, state, zip). Accepts either a plain string or a
    dict; strings are treated as street1 and the rest stays blank."""
    if address is None:
        return "", "", "", ""
    if isinstance(address, str):
        return address[:55], "", "", ""
    if isinstance(address, dict):
        street1 = (address.get("street1") or address.get("line1")
                   or address.get("address1") or "")
        city = address.get("city") or ""
        state = address.get("state") or address.get("region") or ""
        postal = (address.get("postal_code") or address.get("zip")
                  or address.get("postcode") or "")
        return (str(street1)[:55], str(city)[:30],
                str(state)[:2], _digits(postal)[:9])
    return "", "", "", ""


# ---------------------------------------------------------------------------
# Segment writer
# ---------------------------------------------------------------------------
class _Writer:
    """Accumulates 837 segments. Tracks segment count from ST through
    SE so the SE01 count comes out right without re-scanning."""

    __slots__ = ("_segments", "_st_count", "_inside_st")

    def __init__(self) -> None:
        self._segments: list[str] = []
        self._st_count: int = 0
        self._inside_st: bool = False

    def add(self, *fields: Any) -> None:
        # Drop trailing empty elements — the spec lets us elide them
        # and payloads get noticeably leaner.
        rendered = [("" if f is None else str(f)) for f in fields]
        while len(rendered) > 1 and rendered[-1] == "":
            rendered.pop()
        seg = ELEMENT_SEP.join(rendered)
        self._segments.append(seg)
        if self._inside_st:
            self._st_count += 1

    def enter_st(self) -> None:
        self._inside_st = True
        self._st_count = 0

    def exit_st(self) -> int:
        """Return SE01 segment count (ST..SE inclusive) and stop tracking."""
        self._inside_st = False
        # ST itself is tracked as the first segment; SE is not yet
        # written, so total = tracked + 1 (for SE itself).
        return self._st_count + 1

    def finalize(self) -> str:
        return SEGMENT_JOIN.join(self._segments) + SEGMENT_TERMINATOR


# ---------------------------------------------------------------------------
# Envelope writers
# ---------------------------------------------------------------------------
def _write_isa(
    w: _Writer,
    *,
    submitter_id: str,
    receiver_id: str,
    isa13: str,
    now: datetime,
    usage_indicator: str,
) -> None:
    yy_mmdd, hhmm = _yymmdd_hhmm(now)
    # ISA uses fixed-width positional fields.
    w.add(
        "ISA",
        "00", _pad("", 10),          # ISA01, ISA02
        "00", _pad("", 10),          # ISA03, ISA04
        "ZZ", _pad(submitter_id.upper(), 15),   # ISA05, ISA06
        "ZZ", _pad(receiver_id.upper(), 15),    # ISA07, ISA08
        yy_mmdd, hhmm,               # ISA09, ISA10
        REPETITION_SEP,              # ISA11
        "00501",                     # ISA12
        str(isa13).rjust(9, "0"),    # ISA13 — 9-digit zero-padded
        "0",                         # ISA14 — ack requested
        usage_indicator,             # ISA15 — T (test) or P (production)
        COMPONENT_SEP,               # ISA16
    )


def _write_gs(
    w: _Writer,
    *,
    submitter_id: str,
    receiver_id: str,
    gs06: str,
    now: datetime,
) -> None:
    w.add(
        "GS", "HC",
        submitter_id.upper(),
        receiver_id.upper(),
        now.strftime("%Y%m%d"),
        now.strftime("%H%M"),
        str(gs06),
        "X", "005010X222A1",
    )


# ---------------------------------------------------------------------------
# Loop writers
# ---------------------------------------------------------------------------
def _write_submitter_1000A(w: _Writer, submitter: dict) -> None:
    name = _upper_no_specials(submitter.get("name") or "SUBMITTER", 60)
    # 46 = electronic transmitter id number (ETIN).
    w.add("NM1", "41", "2", name, "", "", "", "",
          "46", submitter.get("id") or "")
    contact_name = _upper_no_specials(
        submitter.get("contact_name") or submitter.get("name") or "BILLING", 60,
    )
    # PER*IC contact: phone optional; email/fax pair supported by spec
    # but we only expose phone today.
    phone = _digits(submitter.get("contact_phone") or "")
    per_fields = ["PER", "IC", contact_name]
    if phone:
        per_fields += ["TE", phone]
    w.add(*per_fields)


def _write_receiver_1000B(w: _Writer, receiver: dict) -> None:
    name = _upper_no_specials(receiver.get("name") or "RECEIVER", 60)
    w.add("NM1", "40", "2", name, "", "", "", "",
          "46", receiver.get("id") or "")


def _write_billing_provider_2010AA(
    w: _Writer, bp: dict, *, hl_id: int,
) -> None:
    # 2000A HL — foreign key anchors everything under this billing provider.
    w.add("HL", str(hl_id), "", "20", "1")
    taxonomy = bp.get("taxonomy_code")
    if taxonomy:
        # PRV*BI — billing provider taxonomy (PXC qualifier).
        w.add("PRV", "BI", "PXC", taxonomy)
    # 2010AA — entity identifier code 85 = billing provider.
    entity_type = "2" if (bp.get("entity_type") or "organization").lower().startswith("org") else "1"
    if entity_type == "2":
        name_last = _upper_no_specials(bp.get("name") or "BILLING PROVIDER", 60)
        w.add("NM1", "85", "2", name_last, "", "", "", "",
              "XX", bp.get("npi") or "")
    else:
        last = _upper_no_specials(bp.get("last_name") or bp.get("name") or "", 60)
        first = _upper_no_specials(bp.get("first_name") or "", 35)
        w.add("NM1", "85", "1", last, first, "", "", "",
              "XX", bp.get("npi") or "")
    street, city, state, zip_ = _addr_tuple(bp.get("address"))
    if street:
        w.add("N3", street)
    if city or state or zip_:
        w.add("N4", city, state, zip_)
    tax_id = _digits(bp.get("tax_id")) or None
    if tax_id:
        # REF*EI = EIN, REF*SY = SSN. We default to EI — most clinics
        # bill as an organisation. Adapter can override via context.
        qualifier = bp.get("tax_id_qualifier") or "EI"
        w.add("REF", qualifier, tax_id)


def _write_subscriber_2010BA(
    w: _Writer,
    *,
    claim_ctx: dict,
    hl_id: int,
    parent_hl_id: int,
    has_dependent: bool,
) -> None:
    patient = claim_ctx.get("patient") or {}
    policy = claim_ctx.get("policy") or {}
    payer = claim_ctx.get("payer") or {}
    relationship = (policy.get("relationship_to_subscriber") or "self").lower()
    is_self = relationship == "self"

    # Dependent level — 22 indicates subscriber is the insured. HL04
    # = "1" when no child HL follows, "0" when a dependent HL exists.
    w.add("HL", str(hl_id), str(parent_hl_id), "22", "0" if has_dependent else "1")

    # SBR — subscriber information. SBR02 = individual relationship code;
    # use 18 when subscriber is patient, else leave blank (2010CA loop
    # carries the patient relationship in the dependent case).
    rank = (policy.get("rank") or "primary").lower()
    sbr01 = {"primary": "P", "secondary": "S", "tertiary": "T"}.get(rank, "P")
    sbr02 = "18" if is_self else ""
    group = policy.get("group_number") or ""
    filing_ind = _claim_filing_indicator(payer.get("payer_type"))
    w.add("SBR", sbr01, sbr02, group, "", "", "", "", "", filing_ind)

    # NM1 subscriber — if subscriber is the patient, use patient demographics;
    # otherwise use the `subscriber_*` fields carried on the policy row.
    if is_self:
        last = _upper_no_specials(patient.get("last_name"), 60)
        first = _upper_no_specials(patient.get("first_name"), 35)
    else:
        # Canonical `subscriber_name` is stored as a single free-text
        # field. US convention is "First Last"; split so the last
        # whitespace-delimited token becomes the surname and the rest
        # becomes the given name(s). Fully-explicit subscriber first/
        # last fields (future) should shortcut around this parse.
        raw = (policy.get("subscriber_name") or "").strip()
        sub_last = policy.get("subscriber_last_name")
        sub_first = policy.get("subscriber_first_name")
        if sub_last or sub_first:
            last = _upper_no_specials(sub_last, 60)
            first = _upper_no_specials(sub_first, 35)
        elif " " in raw:
            parts = raw.rsplit(maxsplit=1)
            first = _upper_no_specials(parts[0], 35)
            last = _upper_no_specials(parts[1], 60)
        else:
            last = _upper_no_specials(raw, 60)
            first = ""
    w.add("NM1", "IL", "1", last, first, "", "", "",
          "MI", policy.get("member_id") or "")

    # Subscriber address.
    if is_self:
        address_source = (patient.get("address_details")
                          or patient.get("address"))
    else:
        address_source = policy.get("subscriber_address")
    street, city, state, zip_ = _addr_tuple(address_source)
    if street:
        w.add("N3", street)
    if city or state or zip_:
        w.add("N4", city, state, zip_)

    # DMG — subscriber demographics (required in self case, conditional
    # otherwise; safe to emit whenever dob is known).
    dob = (patient.get("date_of_birth") if is_self
           else policy.get("subscriber_dob"))
    gender = (patient.get("gender") if is_self
              else policy.get("subscriber_gender"))
    dob_x12 = _yyyymmdd(dob)
    if dob_x12:
        w.add("DMG", "D8", dob_x12, _gender(gender))


def _write_payer_2010BB(w: _Writer, payer: dict) -> None:
    name = _upper_no_specials(payer.get("name") or "PAYER", 60)
    # Prefer the Phase 6 `claims_cpid` when present (clearinghouse-
    # assigned), fall back to the insurance-card `electronic_payer_id`.
    payer_id = (payer.get("claims_cpid")
                or payer.get("electronic_payer_id")
                or payer.get("external_id")
                or "")
    w.add("NM1", "PR", "2", name, "", "", "", "",
          "PI", payer_id)
    street, city, state, zip_ = _addr_tuple(payer.get("address"))
    if street:
        w.add("N3", street)
    if city or state or zip_:
        w.add("N4", city, state, zip_)
    # REF*2U = payer's own identifier (optional but common).
    if payer.get("trading_partner_id"):
        w.add("REF", "2U", payer["trading_partner_id"])


def _write_patient_2000C_if_needed(
    w: _Writer, claim_ctx: dict, *, hl_id: int, parent_hl_id: int,
) -> None:
    patient = claim_ctx.get("patient") or {}
    policy = claim_ctx.get("policy") or {}
    relationship = (policy.get("relationship_to_subscriber") or "self").lower()
    if relationship == "self":
        return   # no dependent loop required

    rel_code = _relationship_code(relationship)
    w.add("HL", str(hl_id), str(parent_hl_id), "23", "0")
    w.add("PAT", rel_code)
    last = _upper_no_specials(patient.get("last_name"), 60)
    first = _upper_no_specials(patient.get("first_name"), 35)
    w.add("NM1", "QC", "1", last, first)
    street, city, state, zip_ = _addr_tuple(
        patient.get("address_details") or patient.get("address"),
    )
    if street:
        w.add("N3", street)
    if city or state or zip_:
        w.add("N4", city, state, zip_)
    dob_x12 = _yyyymmdd(patient.get("date_of_birth"))
    if dob_x12:
        w.add("DMG", "D8", dob_x12, _gender(patient.get("gender")))


def _write_claim_2300(w: _Writer, claim_ctx: dict) -> None:
    claim = claim_ctx.get("claim") or {}
    pcn = (claim.get("patient_control_number") or claim.get("id") or "")[:38]
    total = _money(claim.get("billed_cents"))
    pos = (claim.get("place_of_service") or "11")[:2]
    freq = (claim.get("frequency_code") or "1")[:1]
    # Claim-level hard-coded flags per 005010X222A1 baseline:
    # provider signature on file (Y), accept assignment (A),
    # benefits assignment (Y), release of info (Y).
    w.add(
        "CLM", pcn, total, "", "",
        f"{pos}{COMPONENT_SEP}B{COMPONENT_SEP}{freq}",
        "Y", "A", "Y", "Y",
    )

    # Service dates — claim header. For a chiro single-day claim
    # DTP*472 on each line covers the service-line date; DTP at claim
    # level is conditional — we emit it when the span differs from a
    # single line so payers have an explicit window.
    sdf = _yyyymmdd(claim.get("service_date_from"))
    sdt = _yyyymmdd(claim.get("service_date_to"))
    if sdf and sdt and sdf != sdt:
        # DTP*434 = statement dates (range, RD8).
        w.add("DTP", "434", "RD8", f"{sdf}-{sdt}")
    elif sdf:
        # DTP*431 is onset, handled below. We only emit DTP*472 here
        # when a claim-level single-date anchor is useful; otherwise
        # skip and let each LX line carry its own DTP*472.
        pass

    # DTP*431 Onset of current illness/injury.
    onset = _yyyymmdd(claim.get("onset_date"))
    if onset:
        w.add("DTP", "431", "D8", onset)

    # DTP*454 Initial treatment date. When the caller doesn't supply
    # `initial_treatment_date` explicitly, fall back to `onset_date`
    # for chiropractic single-episode claims.
    init_tx = _yyyymmdd(
        claim.get("initial_treatment_date") or claim.get("onset_date"),
    )
    if init_tx:
        w.add("DTP", "454", "D8", init_tx)

    # DTP*439 Accident date.
    accident = _yyyymmdd(claim.get("accident_date"))
    if accident:
        w.add("DTP", "439", "D8", accident)

    if claim.get("authorization_number"):
        w.add("REF", "G1", str(claim["authorization_number"])[:30])
    if claim.get("referral_number"):
        w.add("REF", "9F", str(claim["referral_number"])[:30])

    # HI — diagnoses. ABK = principal (first), ABF = additional.
    diagnoses = list(claim_ctx.get("diagnoses") or [])
    diagnoses.sort(key=lambda d: d.get("sequence") or 99)
    hi_parts: list[str] = ["HI"]
    for i, dx in enumerate(diagnoses[:12]):
        qual = "ABK" if i == 0 else "ABF"
        hi_parts.append(f"{qual}{COMPONENT_SEP}{_icd10_naked(dx.get('code'))}")
    if len(hi_parts) > 1:
        w._segments.append(ELEMENT_SEP.join(hi_parts))
        if w._inside_st:
            w._st_count += 1


def _write_rendering_provider_2310B(
    w: _Writer, rp: dict,
) -> None:
    entity = "2" if (rp.get("entity_type") or "person").lower().startswith("org") else "1"
    if entity == "2":
        last = _upper_no_specials(rp.get("name") or "", 60)
        first = ""
    else:
        last = _upper_no_specials(rp.get("last_name") or rp.get("name") or "", 60)
        first = _upper_no_specials(rp.get("first_name") or "", 35)
    w.add("NM1", "82", entity, last, first, "", "", "",
          "XX", rp.get("npi") or "")
    if rp.get("taxonomy_code"):
        w.add("PRV", "PE", "PXC", rp["taxonomy_code"])


def _write_service_facility_2310C(w: _Writer, sf: dict) -> None:
    name = _upper_no_specials(sf.get("name") or "SERVICE FACILITY", 60)
    w.add("NM1", "77", "2", name, "", "", "", "",
          "XX", sf.get("npi") or "")
    street, city, state, zip_ = _addr_tuple(sf.get("address"))
    if street:
        w.add("N3", street)
    if city or state or zip_:
        w.add("N4", city, state, zip_)


def _write_service_line_2400(w: _Writer, line: dict, *, pos_default: str) -> None:
    seq = int(line.get("sequence") or 1)
    w.add("LX", seq)
    code = str(line.get("code") or "")
    code_type = (line.get("code_type") or "cpt").lower()
    # HC = CPT/HCPCS composite (professional default per 5010).
    qualifier = "HC"
    if code_type in ("hcpcs",):
        qualifier = "HC"
    elif code_type in ("cdt",):
        qualifier = "AD"
    modifiers = [m for m in (line.get("modifiers") or []) if m][:4]
    composite = COMPONENT_SEP.join([qualifier, code, *modifiers])
    charge = _money(line.get("billed_cents"))
    units = int(line.get("units") or 1)
    pointers = [str(p) for p in (line.get("diagnosis_pointers") or []) if p][:4]
    sv1 = [
        "SV1", composite, charge, "UN", units,
        "",                              # SV1*05 — place of service (claim-level default applies)
        "",                              # SV1*06 — service type
        COMPONENT_SEP.join(pointers),    # SV1*07 — composite DX pointers
    ]
    w.add(*sv1)
    svc_date = _yyyymmdd(line.get("service_date"))
    if svc_date:
        w.add("DTP", "472", "D8", svc_date)
    # REF*6R — line-item control number. Payers echo this back on ERAs
    # so we can match line-level remittances. Use the line id / seq.
    line_control = line.get("id") or f"L{seq}"
    w.add("REF", "6R", str(line_control)[:30])
    # Unused: pos_default currently informational only.
    _ = pos_default


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def build_claim_context(
    *,
    claim: dict,
    patient: dict | None,
    payer: dict | None,
    policy: dict | None,
    diagnoses: Iterable[dict],
    lines: Iterable[dict],
    rendering_provider: dict | None = None,
    service_facility: dict | None = None,
    referring_provider: dict | None = None,
) -> dict:
    """Bundle everything a single claim needs to render Loops 2300-2400."""
    return {
        "claim": claim or {},
        "patient": patient or {},
        "payer": payer or {},
        "policy": policy or {},
        "diagnoses": list(diagnoses or []),
        "lines": sorted(list(lines or []),
                        key=lambda ln: int(ln.get("sequence") or 0)),
        "rendering_provider": rendering_provider,
        "service_facility": service_facility,
        "referring_provider": referring_provider,
    }


def build_837p_document(
    *,
    submitter: dict,
    receiver: dict,
    billing_provider: dict,
    claim_contexts: list[dict],
    control_numbers: dict | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Low-level document builder — writes the full ISA…IEA envelope
    around a batch of one or more `claim_context` dicts.

    `submitter` / `receiver` / `billing_provider` are **required**.
    `control_numbers` defaults to uuid-derived digits; callers who care
    about monotonic tracking (adapters) should pass their own."""
    if not submitter or not submitter.get("id"):
        raise ValueError("submitter.id is required")
    if not receiver or not receiver.get("id"):
        raise ValueError("receiver.id is required")
    if not billing_provider or not billing_provider.get("npi"):
        raise ValueError("billing_provider.npi is required")
    if not claim_contexts:
        raise ValueError("at least one claim_context is required")

    now = generated_at or datetime.now(timezone.utc)
    cn = control_numbers or {}
    isa13 = str(cn.get("isa13") or f"{uuid.uuid4().int % 10**9:09d}")
    gs06 = str(cn.get("gs06") or (int(isa13) % 10**9))
    st02 = str(cn.get("st02") or (int(isa13) % 10**4)).rjust(4, "0")
    usage = (cn.get("usage_indicator") or "T").upper()

    w = _Writer()
    _write_isa(
        w,
        submitter_id=submitter["id"],
        receiver_id=receiver["id"],
        isa13=isa13,
        now=now,
        usage_indicator=usage,
    )
    _write_gs(
        w,
        submitter_id=submitter["id"],
        receiver_id=receiver["id"],
        gs06=gs06,
        now=now,
    )

    # Transaction set
    w.enter_st()
    w.add("ST", "837", st02, "005010X222A1")
    bht_ref = (cn.get("bht_ref")
               or claim_contexts[0].get("claim", {}).get("id")
               or uuid.uuid4().hex[:30])
    w.add("BHT", "0019", "00",
          str(bht_ref)[:30], now.strftime("%Y%m%d"), now.strftime("%H%M"), "CH")

    _write_submitter_1000A(w, submitter)
    _write_receiver_1000B(w, receiver)

    # 2000A billing provider hierarchy — one per document is the common
    # case. We model multi-provider batches per HL sub-tree if needed.
    hl_counter = 1
    _write_billing_provider_2010AA(w, billing_provider, hl_id=hl_counter)

    for ctx in claim_contexts:
        billing_hl = hl_counter
        policy = ctx.get("policy") or {}
        is_self = (policy.get("relationship_to_subscriber") or "self").lower() == "self"
        # Subscriber HL
        hl_counter += 1
        subscriber_hl = hl_counter
        _write_subscriber_2010BA(
            w,
            claim_ctx=ctx,
            hl_id=subscriber_hl,
            parent_hl_id=billing_hl,
            has_dependent=not is_self,
        )
        _write_payer_2010BB(w, ctx.get("payer") or {})

        # Patient HL (dependent) — only when patient != subscriber.
        if not is_self:
            hl_counter += 1
            _write_patient_2000C_if_needed(
                w, ctx, hl_id=hl_counter, parent_hl_id=subscriber_hl,
            )

        _write_claim_2300(w, ctx)

        if ctx.get("rendering_provider"):
            _write_rendering_provider_2310B(w, ctx["rendering_provider"])
        if ctx.get("service_facility"):
            _write_service_facility_2310C(w, ctx["service_facility"])

        pos_default = (ctx.get("claim", {}).get("place_of_service") or "11")
        for line in ctx.get("lines") or []:
            _write_service_line_2400(w, line, pos_default=pos_default)

    se_count = w.exit_st()
    w.add("SE", se_count, st02)
    w.add("GE", "1", gs06)
    w.add("IEA", "1", isa13)
    return w.finalize()


def build_x12_837p_wire(
    *,
    claim: dict,
    diagnoses: Iterable[dict],
    lines: Iterable[dict],
    patient: dict | None,
    payer: dict | None,
    policy: dict | None,
    billing_provider: dict | None = None,
    rendering_provider: dict | None = None,
    service_facility: dict | None = None,
    referring_provider: dict | None = None,
    submitter: dict | None = None,
    receiver: dict | None = None,
    control_numbers: dict | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Drop-in wire-ready 837P builder with the same single-claim
    signature as `build_x12_837p_preview`.

    The adapter layer is expected to provide `submitter`, `receiver`,
    and `billing_provider` — when absent, safe defaults are used so
    the preview story still works in unit tests that don't care about
    envelope identity.
    """
    submitter = submitter or {
        "id": "CCMS",
        "name": "CCMS BILLING",
        "contact_name": "BILLING",
    }
    receiver = receiver or {
        "id": "PAYER",
        "name": (payer or {}).get("name") or "PAYER",
    }
    billing_provider = billing_provider or {
        "npi": (claim or {}).get("billing_provider_id") or "0000000000",
        "name": "CCMS BILLING",
        "entity_type": "organization",
        "address": None,
        "tax_id": None,
    }
    ctx = build_claim_context(
        claim=claim or {},
        patient=patient,
        payer=payer,
        policy=policy,
        diagnoses=diagnoses,
        lines=lines,
        rendering_provider=rendering_provider,
        service_facility=service_facility,
        referring_provider=referring_provider,
    )
    return build_837p_document(
        submitter=submitter,
        receiver=receiver,
        billing_provider=billing_provider,
        claim_contexts=[ctx],
        control_numbers=control_numbers,
        generated_at=generated_at,
    )


__all__ = [
    "ELEMENT_SEP", "COMPONENT_SEP", "REPETITION_SEP",
    "SEGMENT_TERMINATOR", "SEGMENT_JOIN",
    "build_claim_context",
    "build_837p_document",
    "build_x12_837p_wire",
]
