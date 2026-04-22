"""
Phase 5 — professional claim data model foundation.

Covers the newly added fields + collections:
  * Claim: patient_control_number (auto + explicit), payer_claim_control_number
    mirror from remit, accident_date, onset_date.
  * Policy: subscriber_dob, subscriber_gender, subscriber_address.
  * Providers: CRUD, uniqueness on (kind, npi).
  * Service Facilities: CRUD.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email, password):
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


# ---------------------------------------------------------------------------
# Claim — patient_control_number + accident/onset dates
# ---------------------------------------------------------------------------
def _seed_claim(s, *, overrides=None, policy_overrides=None):
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"P5 Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial", "remit_method": "era",
    }, timeout=15).json()
    pt = s.post(f"{API}/patients", json={
        "first_name": "P5", "last_name": f"Test{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"p5-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    pol_body = {
        "patient_id": pt["id"], "payer_id": payer["id"], "rank": "primary",
        "subscriber_name": "P5 Subscriber", "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }
    pol_body.update(policy_overrides or {})
    pol = s.post(f"{API}/billing/insurance-policies", json=pol_body,
                 timeout=15).json()
    body = {
        "patient_id": pt["id"], "payer_id": payer["id"], "policy_id": pol["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567890", "rendering_provider_id": "1234567890",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
            "modifiers": ["AT"],
        }],
    }
    body.update(overrides or {})
    return s.post(f"{API}/billing/claims", json=body, timeout=15).json(), pol, payer


def test_claim_pcn_auto_assigned_when_absent():
    s = _login(*ADMIN)
    claim, _, _ = _seed_claim(s)
    pcn = claim["patient_control_number"]
    assert pcn, "PCN must be auto-assigned on create"
    assert pcn.startswith("CCMS-")
    # Auto-derived from first 8 chars of uuid, uppercase.
    assert pcn == f"CCMS-{claim['id'][:8].upper()}"


def test_claim_pcn_respects_caller_value():
    s = _login(*ADMIN)
    claim, _, _ = _seed_claim(s, overrides={
        "patient_control_number": "CLINIC-REF-12345",
    })
    assert claim["patient_control_number"] == "CLINIC-REF-12345"


def test_claim_accident_and_onset_dates_round_trip():
    s = _login(*ADMIN)
    claim, _, _ = _seed_claim(s, overrides={
        "accident_date": "2026-01-15",
        "onset_date":    "2026-01-10",
    })
    assert claim["accident_date"] == "2026-01-15"
    assert claim["onset_date"] == "2026-01-10"


def test_claim_rejects_invalid_accident_date_format():
    s = _login(*ADMIN)
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"P5 Bad Date Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial", "remit_method": "era",
    }, timeout=15).json()
    pt = s.post(f"{API}/patients", json={
        "first_name": "P5Bad", "last_name": "Dates",
        "date_of_birth": "1990-01-01",
        "email": f"p5bad-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    pol = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": pt["id"], "payer_id": payer["id"], "rank": "primary",
        "subscriber_name": "X", "relationship_to_subscriber": "self",
        "member_id": "M-1",
    }, timeout=15).json()
    r = s.post(f"{API}/billing/claims", json={
        "patient_id": pt["id"], "payer_id": payer["id"], "policy_id": pol["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567890", "rendering_provider_id": "1234567890",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "accident_date": "2026/01/15",  # bad format — should 422
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
        }],
    }, timeout=15)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Policy — structured subscriber identity
# ---------------------------------------------------------------------------
def test_policy_subscriber_fields_round_trip():
    s = _login(*ADMIN)
    _, pol, _ = _seed_claim(s, policy_overrides={
        "relationship_to_subscriber": "spouse",
        "subscriber_name": "Spouse Name",
        "subscriber_dob": "1988-07-22",
        "subscriber_gender": "M",
        "subscriber_address": {
            "street1": "42 Test Ave",
            "city": "Austin",
            "state": "TX",
            "postal_code": "78701",
            "country": "US",
        },
    })
    assert pol["subscriber_dob"] == "1988-07-22"
    assert pol["subscriber_gender"] == "M"
    assert pol["subscriber_address"]["city"] == "Austin"
    assert pol["relationship_to_subscriber"] == "spouse"


def test_policy_subscriber_fields_optional():
    s = _login(*ADMIN)
    _, pol, _ = _seed_claim(s)    # no subscriber_* in overrides
    assert pol["subscriber_dob"] is None
    assert pol["subscriber_gender"] is None
    assert pol["subscriber_address"] is None


# ---------------------------------------------------------------------------
# Providers — CRUD
# ---------------------------------------------------------------------------
def test_provider_create_list_patch():
    s = _login(*ADMIN)
    npi = f"{uuid.uuid4().int % 10**10:010d}"
    r = s.post(f"{API}/billing/providers", json={
        "kind": "billing",
        "name": f"Provider {npi[-4:]}",
        "npi": npi,
        "tax_id": "12-3456789",
        "taxonomy_code": "111N00000X",
        "phone": "+1-555-0100",
        "address": {"street1": "100 Clinic Way", "city": "Austin",
                    "state": "TX", "postal_code": "78701"},
    }, timeout=15)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["kind"] == "billing"
    assert p["npi"] == npi

    # List filter by kind.
    r = s.get(f"{API}/billing/providers?kind=billing", timeout=10)
    assert r.status_code == 200
    assert any(row["id"] == p["id"] for row in r.json())

    # Patch the taxonomy.
    r = s.patch(f"{API}/billing/providers/{p['id']}", json={
        "taxonomy_code": "207RC0000X",
    }, timeout=15)
    assert r.status_code == 200
    assert r.json()["taxonomy_code"] == "207RC0000X"


def test_provider_npi_uniqueness_per_kind():
    s = _login(*ADMIN)
    npi = f"{uuid.uuid4().int % 10**10:010d}"
    r = s.post(f"{API}/billing/providers", json={
        "kind": "rendering", "name": "Dr A", "npi": npi,
    }, timeout=15)
    assert r.status_code == 201, r.text
    # Same (kind, npi) → 409.
    r = s.post(f"{API}/billing/providers", json={
        "kind": "rendering", "name": "Dr B", "npi": npi,
    }, timeout=15)
    assert r.status_code == 409, r.text
    # Different kind with same NPI → allowed (solo practitioner as both
    # billing org and rendering indiv. is common).
    r = s.post(f"{API}/billing/providers", json={
        "kind": "billing", "name": "Dr A Org", "npi": npi,
    }, timeout=15)
    assert r.status_code == 201


def test_provider_requires_admin():
    s = _login(*DOCTOR)
    r = s.get(f"{API}/billing/providers", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_provider_npi_format_enforced_by_schema():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/providers", json={
        "kind": "billing", "name": "Bad NPI", "npi": "abcd",
    }, timeout=15)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Service facilities — CRUD
# ---------------------------------------------------------------------------
def test_service_facility_round_trip():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/service-facilities", json={
        "name": f"Facility {uuid.uuid4().hex[:4]}",
        "npi": f"{uuid.uuid4().int % 10**10:010d}",
        "address": {"street1": "500 Clinic Dr", "city": "Austin",
                    "state": "TX", "postal_code": "78701"},
        "phone": "+1-555-0199",
    }, timeout=15)
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["name"]
    # List.
    r = s.get(f"{API}/billing/service-facilities", timeout=10)
    assert r.status_code == 200
    assert any(row["id"] == f["id"] for row in r.json())
    # Patch status.
    r = s.patch(f"{API}/billing/service-facilities/{f['id']}", json={
        "status": "inactive",
    }, timeout=15)
    assert r.status_code == 200
    assert r.json()["status"] == "inactive"


def test_service_facility_requires_admin():
    s = _login(*DOCTOR)
    r = s.get(f"{API}/billing/service-facilities", timeout=10)
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Back-compat: existing claim fields unchanged.
# ---------------------------------------------------------------------------
def test_claim_public_shape_backwards_compatible():
    s = _login(*ADMIN)
    claim, _, _ = _seed_claim(s)
    # All Phase-2 fields must still be present.
    for k in (
        "patient_id", "payer_id", "status", "service_date_from",
        "service_date_to", "billed_cents", "claim_type",
        "place_of_service", "frequency_code", "billing_provider_id",
        "rendering_provider_id",
    ):
        assert k in claim, f"missing legacy field {k}"
    # And new fields default to None when not supplied.
    assert claim["payer_claim_control_number"] is None
    assert claim["accident_date"] is None
    assert claim["onset_date"] is None
