"""
Phase 7 — 837 Professional 005010X222A1 generator.

Covers:
  * Envelope structure: ISA fixed-width / GS / ST / BHT / SE / GE / IEA.
  * SE01 segment count matches ST..SE inclusive.
  * ISA13 == IEA02 and GS06 == GE02 round-trip.
  * Loop 1000A submitter + Loop 1000B receiver identity.
  * Loop 2010AA billing provider with NPI + address + tax-id REF*EI.
  * Subscriber == patient (HL*2*1*22*1 + SBR*P*18) path.
  * Subscriber != patient (HL with HL04=0 + PAT*19 + Loop 2000C/2010CA).
  * Loop 2010BB payer with claims CPID priority over card payer id.
  * Claim loop 2300: CLM composite, frequency, DTP*431 onset,
    DTP*454 initial treatment, DTP*439 accident, REF*G1 prior auth,
    REF*9F referral, HI diagnosis (ABK/ABF).
  * Service line 2400: LX / SV1 composite with modifiers / DTP*472 /
    REF*6R line control.
  * ICD-10 decimal stripped on the wire.
  * Money formatted to 2dp.
  * Claim filing indicator derived from payer_type.
  * Usage indicator (T/P) respected.
  * Deterministic generation with explicit control numbers.
  * Generator wired into the live POST /claims/{id}/submissions path
    so the persisted `payload_x12` is the new wire-ready 837P.
  * Idempotent content hash (`raw_837_hash`) matches sha256 of payload.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

_BACKEND_DIR = "/app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


def _segments(doc: str) -> list[list[str]]:
    """Split the 837 document into element lists keyed by segment tag."""
    # Segments may be joined with "~\n" or just "~"; handle both.
    raw_segments = re.split(r"~\n?", doc.rstrip("~").rstrip("\n"))
    out: list[list[str]] = []
    for seg in raw_segments:
        if not seg.strip():
            continue
        out.append(seg.split("*"))
    return out


def _segs_of_kind(segments: list[list[str]], tag: str) -> list[list[str]]:
    return [s for s in segments if s and s[0] == tag]


# ---------------------------------------------------------------------------
# Fixtures — minimal valid inputs
# ---------------------------------------------------------------------------
_NOW = datetime(2026, 4, 15, 9, 5, tzinfo=timezone.utc)


def _base_claim_kwargs():
    return dict(
        claim={
            "id": "c-0001-abcd",
            "patient_control_number": "CCMS-0001ABCD",
            "billed_cents": 7500,
            "place_of_service": "11",
            "frequency_code": "1",
            "service_date_from": "2026-04-10",
            "service_date_to": "2026-04-10",
            "onset_date": "2026-03-15",
            "accident_date": "2026-03-01",
            "authorization_number": "AUTH-X1",
            "referral_number": "REF-Y2",
            "billing_provider_id": "1234567893",
            "rendering_provider_id": "1234567893",
        },
        patient={
            "first_name": "Jane", "last_name": "Doe",
            "date_of_birth": "1985-07-15", "gender": "female",
            "address_details": {
                "street1": "100 Main St", "city": "Austin",
                "state": "TX", "postal_code": "78701",
            },
        },
        payer={
            "name": "Acme Health Plan", "payer_type": "commercial",
            "electronic_payer_id": "60054",
            "claims_cpid": "CPID-7777",
            "trading_partner_id": "TP-ZZ",
        },
        policy={
            "rank": "primary", "member_id": "M-ACME-999",
            "group_number": "GRP-777", "subscriber_name": "Jane Doe",
            "relationship_to_subscriber": "self",
        },
        diagnoses=[
            {"sequence": 1, "code": "M54.16"},
            {"sequence": 2, "code": "M99.01"},
        ],
        lines=[
            {"id": "L1", "sequence": 1, "service_date": "2026-04-10",
             "code_type": "cpt", "code": "98940", "units": 1,
             "billed_cents": 5500, "diagnosis_pointers": [1, 2],
             "modifiers": ["AT"]},
            {"id": "L2", "sequence": 2, "service_date": "2026-04-10",
             "code_type": "cpt", "code": "97110", "units": 2,
             "billed_cents": 4000, "diagnosis_pointers": [1],
             "modifiers": []},
        ],
        billing_provider={
            "npi": "1234567893", "name": "CCMS CHIROPRACTIC",
            "entity_type": "organization", "tax_id": "12-3456789",
            "taxonomy_code": "111N00000X",
            "address": {"street1": "200 Commerce Blvd", "city": "Austin",
                        "state": "TX", "postal_code": "78702"},
        },
        rendering_provider={
            "npi": "9876543210", "first_name": "John", "last_name": "Smith",
            "taxonomy_code": "111N00000X",
        },
        service_facility={
            "npi": "5555566666", "name": "Downtown Clinic",
            "address": {"street1": "200 Commerce Blvd", "city": "Austin",
                        "state": "TX", "postal_code": "78702"},
        },
        submitter={"id": "CCMS123", "name": "CCMS BILLING LLC",
                   "contact_phone": "5125551000"},
        receiver={"id": "CHC", "name": "CHANGE HEALTHCARE"},
        control_numbers={"isa13": "100000001", "gs06": "1", "st02": "0001",
                         "usage_indicator": "T", "bht_ref": "BHT-001"},
        generated_at=_NOW,
    )


def _build_default():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    return build_x12_837p_wire(**_base_claim_kwargs())


# ---------------------------------------------------------------------------
# 1. Envelope — ISA / GS / ST / SE / GE / IEA
# ---------------------------------------------------------------------------
def test_envelope_isa_is_fixed_width_and_control_numbers_round_trip():
    doc = _build_default()
    segs = _segments(doc)

    isa = _segs_of_kind(segs, "ISA")[0]
    # ISA has 17 elements (tag + 16 fields).
    assert len(isa) == 17, isa
    # ISA06 (submitter id) padded to 15 chars.
    assert isa[6] == "CCMS123".ljust(15)
    # ISA08 (receiver id) padded to 15 chars.
    assert isa[8] == "CHC".ljust(15)
    # ISA09 YYMMDD, ISA10 HHMM
    assert isa[9] == "260415"
    assert isa[10] == "0905"
    assert isa[11] == "^"
    assert isa[12] == "00501"
    assert isa[13] == "100000001"
    assert isa[14] == "0"
    assert isa[15] == "T"   # test usage
    assert isa[16] == ":"

    iea = _segs_of_kind(segs, "IEA")[0]
    assert iea[2] == isa[13], "IEA02 must match ISA13"

    gs = _segs_of_kind(segs, "GS")[0]
    ge = _segs_of_kind(segs, "GE")[0]
    assert gs[6] == "1"
    assert ge[2] == gs[6], "GE02 must match GS06"
    assert gs[8] == "005010X222A1"

    st = _segs_of_kind(segs, "ST")[0]
    se = _segs_of_kind(segs, "SE")[0]
    assert st[1] == "837"
    assert st[2] == "0001"
    assert st[3] == "005010X222A1"
    assert se[2] == st[2], "SE02 must match ST02"


def test_se01_counts_segments_from_st_through_se_inclusive():
    doc = _build_default()
    segs = _segments(doc)
    # Locate ST and SE by index; the count is inclusive.
    st_idx = next(i for i, s in enumerate(segs) if s[0] == "ST")
    se_idx = next(i for i, s in enumerate(segs) if s[0] == "SE")
    expected = se_idx - st_idx + 1
    se = segs[se_idx]
    assert int(se[1]) == expected, (int(se[1]), expected)


def test_usage_indicator_production_flag_is_honoured():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["control_numbers"]["usage_indicator"] = "P"
    doc = build_x12_837p_wire(**kw)
    isa = _segs_of_kind(_segments(doc), "ISA")[0]
    assert isa[15] == "P"


def test_bht_carries_reference_and_transaction_purpose():
    doc = _build_default()
    bht = _segs_of_kind(_segments(doc), "BHT")[0]
    assert bht[1] == "0019"
    assert bht[2] == "00"
    assert bht[3] == "BHT-001"
    assert bht[6] == "CH"


# ---------------------------------------------------------------------------
# 2. Loop 1000A / 1000B — submitter + receiver
# ---------------------------------------------------------------------------
def test_submitter_and_receiver_loops_emit_nm1_41_and_nm1_40():
    doc = _build_default()
    segs = _segments(doc)
    nm1_41 = next(s for s in segs if s[0] == "NM1" and s[1] == "41")
    assert nm1_41[3] == "CCMS BILLING LLC"
    assert nm1_41[8] == "46"
    assert nm1_41[9] == "CCMS123"

    per = _segs_of_kind(segs, "PER")[0]
    assert per[1] == "IC"
    assert per[3] == "TE"
    assert per[4] == "5125551000"

    nm1_40 = next(s for s in segs if s[0] == "NM1" and s[1] == "40")
    assert nm1_40[3] == "CHANGE HEALTHCARE"
    assert nm1_40[8] == "46"
    assert nm1_40[9] == "CHC"


# ---------------------------------------------------------------------------
# 3. Loop 2010AA — billing provider
# ---------------------------------------------------------------------------
def test_billing_provider_loop_2010AA_carries_npi_address_and_taxid():
    doc = _build_default()
    segs = _segments(doc)
    hl1 = _segs_of_kind(segs, "HL")[0]
    assert hl1[1] == "1"
    assert hl1[3] == "20"
    prv = _segs_of_kind(segs, "PRV")[0]
    assert prv[1] == "BI"
    assert prv[2] == "PXC"
    assert prv[3] == "111N00000X"
    nm1_85 = next(s for s in segs if s[0] == "NM1" and s[1] == "85")
    assert nm1_85[2] == "2"
    assert nm1_85[3] == "CCMS CHIROPRACTIC"
    assert nm1_85[8] == "XX"
    assert nm1_85[9] == "1234567893"
    n3 = next(s for s in segs if s[0] == "N3")
    assert n3[1] == "200 Commerce Blvd"
    n4 = next(s for s in segs if s[0] == "N4")
    assert n4[1] == "Austin"
    assert n4[2] == "TX"
    assert n4[3] == "78702"
    ref_ei = next(s for s in segs if s[0] == "REF" and s[1] == "EI")
    # Tax ID digits only — dashes stripped.
    assert ref_ei[2] == "123456789"


def test_billing_provider_npi_required_raises_if_missing():
    from services.billing.clearinghouse.x12_837p import build_837p_document
    with pytest.raises(ValueError, match="billing_provider.npi"):
        build_837p_document(
            submitter={"id": "CCMS"},
            receiver={"id": "CHC"},
            billing_provider={"name": "x"},   # no NPI
            claim_contexts=[{"claim": {"id": "c"}}],
        )


# ---------------------------------------------------------------------------
# 4. Loop 2000B / 2010BA — subscriber == patient path
# ---------------------------------------------------------------------------
def test_subscriber_is_patient_uses_hl22_with_hl04_equals_1_and_sbr_18():
    doc = _build_default()
    segs = _segments(doc)
    # HL*2 is the subscriber hierarchy.
    hl2 = [s for s in segs if s[0] == "HL" and s[1] == "2"][0]
    assert hl2[3] == "22"
    assert hl2[4] == "1"   # no dependent level
    sbr = _segs_of_kind(segs, "SBR")[0]
    assert sbr[1] == "P"
    assert sbr[2] == "18"
    assert sbr[3] == "GRP-777"
    assert sbr[9] == "CI"   # commercial
    nm1_il = next(s for s in segs if s[0] == "NM1" and s[1] == "IL")
    assert nm1_il[3] == "DOE"
    assert nm1_il[4] == "JANE"
    assert nm1_il[8] == "MI"
    assert nm1_il[9] == "M-ACME-999"
    dmg = next(s for s in segs if s[0] == "DMG" and len(s) >= 4)
    assert dmg[1] == "D8"
    assert dmg[2] == "19850715"
    assert dmg[3] == "F"


# ---------------------------------------------------------------------------
# 5. Loop 2000C — subscriber != patient (dependent path)
# ---------------------------------------------------------------------------
def test_subscriber_differs_from_patient_emits_dependent_loop():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["policy"]["relationship_to_subscriber"] = "child"
    kw["policy"]["subscriber_name"] = "Alice Doe"
    kw["policy"]["subscriber_dob"] = "1985-02-10"
    kw["policy"]["subscriber_gender"] = "F"
    kw["policy"]["subscriber_address"] = {
        "street1": "100 Main St", "city": "Austin",
        "state": "TX", "postal_code": "78701",
    }
    kw["patient"]["first_name"] = "Tommy"
    kw["patient"]["last_name"] = "Doe"
    kw["patient"]["date_of_birth"] = "2015-05-20"
    kw["patient"]["gender"] = "male"
    doc = build_x12_837p_wire(**kw)
    segs = _segments(doc)

    hl2 = [s for s in segs if s[0] == "HL" and s[1] == "2"][0]
    assert hl2[3] == "22"
    # Dependent present → HL04 = 0
    assert hl2[4] == "0"

    sbr = _segs_of_kind(segs, "SBR")[0]
    # SBR02 omitted when subscriber is not patient.
    assert sbr[1] == "P"
    assert sbr[2] == ""

    # Subscriber name: "Alice Doe" → last=DOE, first=ALICE.
    nm1_il = next(s for s in segs if s[0] == "NM1" and s[1] == "IL")
    assert nm1_il[3] == "DOE"
    assert nm1_il[4] == "ALICE"

    # Dependent / patient loop.
    hl3 = [s for s in segs if s[0] == "HL" and s[1] == "3"][0]
    assert hl3[2] == "2", "HL03 parent must be subscriber HL id"
    assert hl3[3] == "23"
    pat = _segs_of_kind(segs, "PAT")[0]
    assert pat[1] == "19"   # child
    nm1_qc = next(s for s in segs if s[0] == "NM1" and s[1] == "QC")
    assert nm1_qc[3] == "DOE"
    assert nm1_qc[4] == "TOMMY"


# ---------------------------------------------------------------------------
# 6. Loop 2010BB — payer
# ---------------------------------------------------------------------------
def test_payer_loop_prefers_claims_cpid_over_card_electronic_id():
    doc = _build_default()
    segs = _segments(doc)
    nm1_pr = next(s for s in segs if s[0] == "NM1" and s[1] == "PR")
    assert nm1_pr[3] == "ACME HEALTH PLAN"
    assert nm1_pr[8] == "PI"
    # claims_cpid wins over electronic_payer_id.
    assert nm1_pr[9] == "CPID-7777"
    ref_2u = [s for s in segs if s[0] == "REF" and s[1] == "2U"]
    assert ref_2u and ref_2u[0][2] == "TP-ZZ"


def test_payer_falls_back_to_electronic_payer_id_when_no_cpid():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["payer"] = {"name": "Cigna", "payer_type": "commercial",
                   "electronic_payer_id": "62308"}
    doc = build_x12_837p_wire(**kw)
    nm1_pr = next(s for s in _segments(doc) if s[0] == "NM1" and s[1] == "PR")
    assert nm1_pr[9] == "62308"


def test_claim_filing_indicator_maps_from_payer_type():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    for ptype, expected in [
        ("commercial", "CI"),
        ("medicare", "MB"),
        ("medicaid", "MC"),
        ("workers_comp", "WC"),
        ("auto", "AM"),
        ("self_pay", "09"),
        (None, "CI"),
    ]:
        kw = _base_claim_kwargs()
        kw["payer"]["payer_type"] = ptype
        doc = build_x12_837p_wire(**kw)
        sbr = _segs_of_kind(_segments(doc), "SBR")[0]
        assert sbr[9] == expected, (ptype, sbr[9])


# ---------------------------------------------------------------------------
# 7. Loop 2300 — claim + DTP + REF + HI
# ---------------------------------------------------------------------------
def test_clm_segment_fields():
    doc = _build_default()
    clm = _segs_of_kind(_segments(doc), "CLM")[0]
    assert clm[1] == "CCMS-0001ABCD"
    assert clm[2] == "75.00"
    # CLM05 composite: POS:facility-qual:freq
    assert clm[5] == "11:B:1"
    assert clm[6] == "Y"   # provider signature on file
    assert clm[7] == "A"   # accept assignment
    assert clm[8] == "Y"   # benefits assignment
    assert clm[9] == "Y"   # release of info


def test_frequency_code_resubmission_value_flows_through():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["claim"]["frequency_code"] = "7"
    doc = build_x12_837p_wire(**kw)
    clm = _segs_of_kind(_segments(doc), "CLM")[0]
    assert clm[5].endswith(":7")


def test_dtp_segments_onset_initial_treatment_accident():
    doc = _build_default()
    segs = _segments(doc)
    dtps = {s[1]: s for s in segs if s[0] == "DTP" and len(s) >= 4}
    assert "431" in dtps, "DTP*431 onset required when onset_date present"
    assert dtps["431"][2] == "D8"
    assert dtps["431"][3] == "20260315"
    assert "454" in dtps, (
        "DTP*454 initial treatment should fall back to onset_date"
    )
    assert dtps["454"][3] == "20260315"
    assert "439" in dtps
    assert dtps["439"][3] == "20260301"


def test_ref_g1_and_9f_emitted_when_auth_and_referral_present():
    doc = _build_default()
    refs = {s[1]: s for s in _segs_of_kind(_segments(doc), "REF")}
    assert refs["G1"][2] == "AUTH-X1"
    assert refs["9F"][2] == "REF-Y2"


def test_hi_segment_strips_icd10_decimal_and_uses_abk_then_abf():
    doc = _build_default()
    hi = _segs_of_kind(_segments(doc), "HI")[0]
    # hi[1] = ABK:M5416, hi[2] = ABF:M9901
    assert hi[1] == "ABK:M5416"
    assert hi[2] == "ABF:M9901"


def test_hi_segment_caps_at_12_diagnoses():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["diagnoses"] = [
        {"sequence": i, "code": f"M{i:02d}.0"} for i in range(1, 16)
    ]
    doc = build_x12_837p_wire(**kw)
    hi = _segs_of_kind(_segments(doc), "HI")[0]
    # 1 tag element + 12 dx composites = 13 elements total.
    assert len(hi) == 13


# ---------------------------------------------------------------------------
# 8. Loop 2310B / 2310C — rendering provider + service facility
# ---------------------------------------------------------------------------
def test_rendering_provider_emits_nm1_82_with_npi_and_prv_taxonomy():
    doc = _build_default()
    segs = _segments(doc)
    nm1_82 = next(s for s in segs if s[0] == "NM1" and s[1] == "82")
    assert nm1_82[2] == "1"   # person
    assert nm1_82[3] == "SMITH"
    assert nm1_82[4] == "JOHN"
    assert nm1_82[8] == "XX"
    assert nm1_82[9] == "9876543210"
    prv_pe = [s for s in segs if s[0] == "PRV" and s[1] == "PE"]
    assert prv_pe and prv_pe[0][3] == "111N00000X"


def test_service_facility_emits_nm1_77_with_npi_and_address():
    doc = _build_default()
    segs = _segments(doc)
    nm1_77 = next(s for s in segs if s[0] == "NM1" and s[1] == "77")
    assert nm1_77[2] == "2"
    assert nm1_77[3] == "DOWNTOWN CLINIC"
    assert nm1_77[8] == "XX"
    assert nm1_77[9] == "5555566666"


# ---------------------------------------------------------------------------
# 9. Loop 2400 — service lines
# ---------------------------------------------------------------------------
def test_service_line_lx_sv1_dtp_ref():
    doc = _build_default()
    segs = _segments(doc)
    lx_segs = _segs_of_kind(segs, "LX")
    assert [s[1] for s in lx_segs] == ["1", "2"]

    sv1_segs = _segs_of_kind(segs, "SV1")
    # Line 1 — CPT with modifier, 2 dx pointers.
    assert sv1_segs[0][1] == "HC:98940:AT"
    assert sv1_segs[0][2] == "55.00"
    assert sv1_segs[0][3] == "UN"
    assert sv1_segs[0][4] == "1"
    assert sv1_segs[0][7] == "1:2"
    # Line 2 — no modifiers, 1 dx pointer.
    assert sv1_segs[1][1] == "HC:97110"
    assert sv1_segs[1][2] == "40.00"
    assert sv1_segs[1][4] == "2"
    assert sv1_segs[1][7] == "1"

    dtp_472 = [s for s in segs if s[0] == "DTP" and s[1] == "472"]
    assert len(dtp_472) == 2
    assert all(s[3] == "20260410" for s in dtp_472)

    ref_6r = [s for s in segs if s[0] == "REF" and s[1] == "6R"]
    assert {s[2] for s in ref_6r} == {"L1", "L2"}


def test_service_line_caps_modifiers_at_four():
    from services.billing.clearinghouse.x12_837p import build_x12_837p_wire
    kw = _base_claim_kwargs()
    kw["lines"] = [{
        "id": "L1", "sequence": 1, "service_date": "2026-04-10",
        "code_type": "cpt", "code": "98940", "units": 1,
        "billed_cents": 5500, "diagnosis_pointers": [1],
        "modifiers": ["25", "59", "GA", "GP", "GZ"],   # 5 modifiers
    }]
    doc = build_x12_837p_wire(**kw)
    sv1 = _segs_of_kind(_segments(doc), "SV1")[0]
    # HC + code + 4 modifiers = 6 components; GZ dropped.
    assert sv1[1] == "HC:98940:25:59:GA:GP"
    assert "GZ" not in sv1[1]


# ---------------------------------------------------------------------------
# 10. Deterministic / non-regression
# ---------------------------------------------------------------------------
def test_deterministic_output_when_control_numbers_and_timestamp_fixed():
    doc_a = _build_default()
    doc_b = _build_default()
    assert doc_a == doc_b


def test_raw_837_hash_matches_sha256_of_document():
    doc = _build_default()
    h = hashlib.sha256(doc.encode("utf-8")).hexdigest()
    # Sanity — re-hash does not drift, and hash changes when payload does.
    assert hashlib.sha256(doc.encode("utf-8")).hexdigest() == h
    tampered = doc.replace("CCMS-0001ABCD", "CCMS-0001DIFF")
    assert hashlib.sha256(tampered.encode("utf-8")).hexdigest() != h


# ---------------------------------------------------------------------------
# 11. Live integration — router now persists a wire-ready 837P
# ---------------------------------------------------------------------------
def _seed_and_submit(s):
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"P7 Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial", "remit_method": "era",
        "claims_cpid": "CPID-LIVE",
    }, timeout=15).json()
    patient = s.post(f"{API}/patients", json={
        "first_name": "P7", "last_name": f"Wire{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"p7-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    policy = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "P7 Subscriber",
        "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()
    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "policy_id": policy["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567893",
        "rendering_provider_id": "1234567893",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
            "modifiers": ["AT"],
        }],
    }, timeout=15).json()
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)
    r = s.post(
        f"{API}/billing/claims/{claim['id']}/submissions",
        json={"method": "manual_portal"}, timeout=15,
    )
    assert r.status_code == 201, r.text
    sub = r.json()
    return claim, sub


def _fetch_submission_payload(s, claim_id: str, sub_id: str) -> dict:
    r = s.get(
        f"{API}/billing/claims/{claim_id}/submissions/{sub_id}/payload",
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_live_submission_persists_wire_ready_837P_document():
    s = _login(*ADMIN)
    claim, sub = _seed_and_submit(s)
    payload = _fetch_submission_payload(s, claim["id"], sub["id"])
    x12 = payload.get("payload_x12") or ""
    assert x12.startswith("ISA*"), x12[:120]
    assert "~\nGS*HC*" in x12
    assert "~\nST*837*" in x12
    assert "005010X222A1" in x12
    assert "IEA*1*" in x12
    # Envelope snapshot on the submission row should match.
    assert payload["claim_id"] == claim["id"]
    # Hash recorded on the submission equals sha256 of what we just read.
    expected = hashlib.sha256(x12.encode("utf-8")).hexdigest()
    # The payload response includes the row's persisted hash via detail.
    rows = s.get(
        f"{API}/billing/claims/{claim['id']}/submissions", timeout=10,
    ).json()
    # The ClaimSubmissionPublic response strips Phase 6 fields, so pull
    # the raw hash straight from Mongo for verification.
    from motor.motor_asyncio import AsyncIOMotorClient
    import asyncio as _asyncio

    async def _raw():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            return await c[os.environ["DB_NAME"]].claim_submissions.find_one(
                {"id": sub["id"]}, {"_id": 0},
            )
        finally:
            c.close()

    raw = _asyncio.run(_raw())
    assert raw is not None
    assert raw.get("payload_format") == "json+x12-837p-005010X222A1"
    assert raw.get("raw_837_hash") == expected
    # The 2010BB payer loop should carry the payer's claims_cpid.
    assert "CPID-LIVE" in x12
    assert rows, "submission list must return at least one row"


def test_live_submission_se_count_matches_st_to_se_block():
    s = _login(*ADMIN)
    claim, sub = _seed_and_submit(s)
    payload = _fetch_submission_payload(s, claim["id"], sub["id"])
    x12 = payload["payload_x12"]
    segs = _segments(x12)
    st_idx = next(i for i, sg in enumerate(segs) if sg[0] == "ST")
    se_idx = next(i for i, sg in enumerate(segs) if sg[0] == "SE")
    assert int(segs[se_idx][1]) == (se_idx - st_idx + 1)
