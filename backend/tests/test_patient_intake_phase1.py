"""
Patient Intake — Phase 1 backward-compatible expansion.

Validates that:
  1. The legacy flat payload still creates and updates patients exactly
     as before (current frontend must keep working).
  2. The new grouped/nested intake payload (demographics, contact,
     address, emergency_contact, admin, guarantor, insurance,
     clinical_intake, case_details, consents) is accepted, persisted,
     and returned.
  3. `address` / `emergency_contact` accept BOTH a plain string and a
     structured object. When an object is sent, the legacy scalar is
     still populated so existing UI (`patient.address`) does not break.
  4. Newly added PHI sections are encrypted at rest (the raw Mongo
     document stores the ENC_PREFIX-tagged ciphertext, never plaintext).
  5. Mixed updates (grouped + legacy flat) merge correctly without
     wiping existing data.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    """Login over plain HTTP — the `Secure` cookies the backend issues can't
    traverse this transport, so we use Bearer tokens extracted from the
    Set-Cookie header for the access_token, and acquire a reauth token the
    same way."""
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    assert access, f"no access_token cookie in login response: {dict(r.cookies)}"
    s.headers["Authorization"] = f"Bearer {access}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text
    reauth = r.cookies.get("reauth_token")
    if reauth:
        s.headers["x-reauth-token"] = reauth
    return s


def _unique_email() -> str:
    return f"intake_{uuid.uuid4().hex[:10]}@example.com"


@pytest.fixture(scope="module")
def admin_session():
    return _login(*GROUP_ADMIN)


# ---------------------------------------------------------------------------
# 1. Legacy flat payload still works — current frontend modal path.
# ---------------------------------------------------------------------------

def test_legacy_flat_payload_create_and_update(admin_session):
    s = admin_session
    payload = {
        "first_name": "Legacy",
        "last_name": "Patient",
        "email": _unique_email(),
        "phone": "+1-555-0100",
        "date_of_birth": "1990-02-14",
        "gender": "male",
        "address": "124 Willow Lane, Denver, CO 80202",
        "emergency_contact": "Jane Doe / +1-555-0200",
        "notes": "Seasonal back pain",
    }
    r = s.post(f"{API}/patients", json=payload, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    # Unmasked on create response — legacy fields round-trip.
    assert body["first_name"] == "Legacy"
    assert body["last_name"] == "Patient"
    assert body["date_of_birth"] == "1990-02-14"
    assert body["address"] == "124 Willow Lane, Denver, CO 80202"
    assert body["emergency_contact"] == "Jane Doe / +1-555-0200"
    assert body["unmasked"] is True

    # Legacy-style PUT updating flat fields only.
    r = s.put(
        f"{API}/patients/{pid}",
        json={"notes": "Updated back pain notes", "phone": "+1-555-0111"},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["notes"] == "Updated back pain notes"
    assert updated["phone"] == "+1-555-0111"
    # Existing fields preserved.
    assert updated["first_name"] == "Legacy"
    assert updated["address"] == "124 Willow Lane, Denver, CO 80202"


# ---------------------------------------------------------------------------
# 2. New grouped payload is accepted + persisted.
# ---------------------------------------------------------------------------

def test_grouped_payload_create_full_intake(admin_session):
    s = admin_session
    email = _unique_email()
    payload = {
        # Legacy flats intentionally omitted to ensure backfill from groups.
        "demographics": {
            "first_name": "Grouped",
            "last_name": "Intake",
            "middle_name": "Q",
            "preferred_name": "Grp",
            "date_of_birth": "1985-07-22",
            "gender": "female",
            "pronouns": "she/her",
            "marital_status": "married",
            "ssn_last4": "4242",
            "language": "en",
            "occupation": "Engineer",
            "employer": "Acme Co.",
        },
        "contact": {
            "phone": "+1-555-0300",
            "phone_alt": "+1-555-0301",
            "email": email,
            "preferred_contact_method": "email",
            "ok_to_leave_message": True,
        },
        "address": {
            "line1": "742 Evergreen Terrace",
            "line2": "Apt 4B",
            "city": "Springfield",
            "state": "OR",
            "postal_code": "97477",
            "country": "USA",
        },
        "emergency_contact": {
            "name": "Pat Intake",
            "relationship": "spouse",
            "phone": "+1-555-0350",
            "email": "pat@example.com",
        },
        "admin": {
            "referral_source": "Google Search",
            "mrn": "MRN-00042",
            "tags": ["new-patient", "insurance-verified"],
        },
        "guarantor": {
            "same_as_patient": True,
        },
        "insurance": {
            "primary": {
                "carrier": "BlueCross BlueShield",
                "plan_name": "Gold PPO",
                "plan_type": "PPO",
                "member_id": "XJK123456",
                "group_number": "GRP-88",
                "policy_holder_name": "Grouped Intake",
                "policy_holder_relationship": "self",
                "policy_holder_dob": "1985-07-22",
                "copay": "$30",
                "deductible": "$1500",
            },
        },
        "clinical_intake": {
            "chief_complaint": "Lower back pain",
            "complaint_onset": "2 weeks ago",
            "pain_level": 6,
            "pain_description": "sharp, radiating",
            "pain_locations": ["lumbar", "left-glute"],
            "symptoms": ["numbness", "tingling"],
            "aggravating_factors": "sitting > 30 min",
            "relieving_factors": "stretching",
            "medications": "ibuprofen 400mg prn",
            "allergies": "penicillin",
            "past_medical_history": "HTN",
            "family_history": "father — lumbar disc surgery",
            "review_of_systems": {"musculoskeletal": "positive", "neuro": "negative"},
            "notes": "Prefers afternoon appts.",
        },
        "case_details": {
            "case_type": "personal_injury",
            "date_of_injury": "2025-12-01",
            "injury_description": "Slip and fall at grocery store",
            "attorney_name": "Saul G.",
            "attorney_phone": "+1-555-0444",
            "attorney_email": "saul@lawfirm.example",
            "claim_number": "CLM-98765",
        },
        "consents": {
            "hipaa": {
                "type": "hipaa",
                "accepted": True,
                "signature_name": "Grouped Intake",
                "signed_at": "2026-02-10T10:00:00Z",
                "document_version": "v2.1",
            },
            "treatment": {
                "type": "treatment",
                "accepted": True,
                "signature_name": "Grouped Intake",
                "signed_at": "2026-02-10T10:00:00Z",
            },
        },
    }
    r = s.post(f"{API}/patients", json=payload, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]

    # Legacy top-level fields backfilled from demographics/contact.
    assert body["first_name"] == "Grouped"
    assert body["last_name"] == "Intake"
    assert body["date_of_birth"] == "1985-07-22"
    assert body["gender"] == "female"
    assert body["phone"] == "+1-555-0300"
    assert body["email"] == email

    # `address` flattened to legacy string for backward-compatible UI.
    assert isinstance(body["address"], str) and body["address"]
    assert "742 Evergreen Terrace" in body["address"]
    assert "Springfield" in body["address"]

    # Structured address returned alongside the legacy scalar.
    assert body["address_details"]["line1"] == "742 Evergreen Terrace"
    assert body["address_details"]["city"] == "Springfield"
    assert body["address_details"]["postal_code"] == "97477"

    # Emergency contact: both legacy string + structured object.
    assert isinstance(body["emergency_contact"], str)
    assert "Pat Intake" in body["emergency_contact"]
    assert body["emergency_contact_details"]["name"] == "Pat Intake"
    assert body["emergency_contact_details"]["relationship"] == "spouse"

    # Grouped sections round-trip unmodified.
    assert body["demographics"]["pronouns"] == "she/her"
    assert body["demographics"]["ssn_last4"] == "4242"
    assert body["contact"]["preferred_contact_method"] == "email"
    assert body["admin"]["tags"] == ["new-patient", "insurance-verified"]
    assert body["insurance"]["primary"]["carrier"] == "BlueCross BlueShield"
    assert body["insurance"]["primary"]["member_id"] == "XJK123456"
    assert body["clinical_intake"]["pain_level"] == 6
    assert body["clinical_intake"]["pain_locations"] == ["lumbar", "left-glute"]
    assert body["clinical_intake"]["review_of_systems"]["musculoskeletal"] == "positive"
    assert body["case_details"]["case_type"] == "personal_injury"
    assert body["case_details"]["claim_number"] == "CLM-98765"
    assert body["consents"]["hipaa"]["accepted"] is True
    assert body["consents"]["hipaa"]["signature_name"] == "Grouped Intake"

    # Re-fetch with unmask and confirm groups persist identically.
    r = s.get(f"{API}/patients/{pid}?unmask=true", timeout=15)
    assert r.status_code == 200, r.text
    fetched = r.json()
    assert fetched["unmasked"] is True
    assert fetched["demographics"]["preferred_name"] == "Grp"
    assert fetched["insurance"]["primary"]["plan_type"] == "PPO"
    assert fetched["clinical_intake"]["allergies"] == "penicillin"

    # Masked response must hide the grouped sections entirely.
    r = s.get(f"{API}/patients/{pid}", timeout=15)
    assert r.status_code == 200, r.text
    masked = r.json()
    assert masked["unmasked"] is False
    for k in (
        "demographics", "contact", "address_details", "emergency_contact_details",
        "admin", "guarantor", "insurance", "clinical_intake", "case_details", "consents",
    ):
        assert masked.get(k) is None, f"grouped section `{k}` leaked in masked response"


# ---------------------------------------------------------------------------
# 3. Mixed update — grouped sections patch without wiping legacy fields.
# ---------------------------------------------------------------------------

def test_grouped_update_preserves_other_sections(admin_session):
    s = admin_session
    r = s.post(
        f"{API}/patients",
        json={
            "first_name": "Partial",
            "last_name": "Update",
            "email": _unique_email(),
            "phone": "+1-555-0500",
            "demographics": {"occupation": "Nurse"},
        },
        timeout=15,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # Patch just the insurance section.
    r = s.put(
        f"{API}/patients/{pid}",
        json={
            "insurance": {
                "primary": {"carrier": "Aetna", "member_id": "A-1001"},
            },
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["insurance"]["primary"]["carrier"] == "Aetna"
    # Legacy fields untouched.
    assert body["first_name"] == "Partial"
    assert body["last_name"] == "Update"
    assert body["phone"] == "+1-555-0500"
    # demographics section untouched (still has occupation).
    assert body["demographics"]["occupation"] == "Nurse"


# ---------------------------------------------------------------------------
# 4. Grouped address via object is also accepted on update and backfills flat.
# ---------------------------------------------------------------------------

def test_update_with_object_address(admin_session):
    s = admin_session
    r = s.post(
        f"{API}/patients",
        json={
            "first_name": "Addr",
            "last_name": "Switcher",
            "email": _unique_email(),
        },
        timeout=15,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    r = s.put(
        f"{API}/patients/{pid}",
        json={
            "address": {
                "line1": "1 Infinite Loop",
                "city": "Cupertino",
                "state": "CA",
                "postal_code": "95014",
            },
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["address"], str)
    assert "1 Infinite Loop" in body["address"]
    assert "Cupertino" in body["address"]
    assert body["address_details"]["state"] == "CA"


# ---------------------------------------------------------------------------
# 5. Encryption-at-rest — new PHI sections must be stored as ciphertext.
# ---------------------------------------------------------------------------

def test_grouped_sections_encrypted_at_rest(admin_session):
    """Probe the raw Mongo document to confirm new sensitive sections are
    stored encrypted (ENC_PREFIX) and never in plaintext."""
    s = admin_session
    r = s.post(
        f"{API}/patients",
        json={
            "first_name": "Enc",
            "last_name": "AtRest",
            "email": _unique_email(),
            "clinical_intake": {
                "chief_complaint": "Confidential complaint PHI marker X1Y2Z3",
                "allergies": "penicillin",
            },
            "insurance": {
                "primary": {"carrier": "Cigna", "member_id": "CIG-SECRET-0001"},
            },
            "consents": {
                "hipaa": {"accepted": True, "signature_name": "Enc AtRest"},
            },
        },
        timeout=15,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # Right-to-access export returns decrypted — confirm round-trip.
    r = s.get(f"{API}/patients/{pid}/export", timeout=15)
    assert r.status_code == 200, r.text
    export = r.json()
    patient = export["patient"]
    assert patient["clinical_intake"]["chief_complaint"].startswith("Confidential complaint PHI marker")
    assert patient["insurance"]["primary"]["member_id"] == "CIG-SECRET-0001"
    assert patient["consents"]["hipaa"]["signature_name"] == "Enc AtRest"

    # Raw DB probe — the stored document must never contain the plaintext
    # PHI marker. We look the document up directly via Motor.
    import asyncio
    import sys
    sys.path.insert(0, "/app/backend")
    from dotenv import load_dotenv  # noqa: E402
    load_dotenv("/app/backend/.env")
    from core.db import get_db_read  # noqa: E402
    from core.crypto import ENC_PREFIX  # noqa: E402

    async def _probe():
        db = get_db_read()
        return await db.patients.find_one({"id": pid}, {"_id": 0})

    raw = asyncio.run(_probe())
    assert raw is not None
    # Every encrypted field on disk starts with the ENC_PREFIX tag.
    for key in ("clinical_intake", "insurance", "consents", "date_of_birth"):
        if raw.get(key) is not None:
            assert isinstance(raw[key], str), f"{key} should be ciphertext string on disk"
            assert raw[key].startswith(ENC_PREFIX), f"{key} must be encrypted at rest"
    # Plaintext PHI marker must not appear anywhere in the raw doc.
    import json as _json
    flat = _json.dumps(raw, default=str)
    assert "Confidential complaint PHI marker X1Y2Z3" not in flat
    assert "CIG-SECRET-0001" not in flat


# ---------------------------------------------------------------------------
# 6. Validation — at least one of (top-level first/last name) or
#    (demographics.first_name/last_name) must be provided.
# ---------------------------------------------------------------------------

def test_create_requires_name_from_either_source(admin_session):
    s = admin_session
    r = s.post(f"{API}/patients", json={"email": _unique_email()}, timeout=15)
    # Pydantic allows None top-level but router enforces after normalization.
    assert r.status_code in (400, 422), r.text
