"""Clinical Phase 4 — Initial Exam workflow tests.

Covers:
  - create-from-encounter happy path + prefill correctness
  - one-exam-per-encounter guard (duplicate returns existing)
  - appointment/provider/episode/location auto-fill from encounter
  - partial PATCH (history/examination/assessment merges cleanly)
  - explicit /prefill is non-destructive (doesn't overwrite provider edits)
  - diagnosis_ids validation (cross-patient rejected)
  - mark-sign-ready / unmark / sign transitions; 409 on invalid states
  - sign materializes new_diagnoses + de-dups against existing active
  - primary uniqueness enforced post-sign
  - signed exams are immutable (PATCH 409)
  - narrative renders all expected sections and diagnoses
  - chart visibility via list + summary.initial_exams counts
  - tenant isolation
  - reauth required on writes
  - cancelled encounter rejected 409
"""
from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password, *, reauth=True):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    s.headers["Authorization"] = f"Bearer {r.cookies.get('access_token')}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r.status_code == 200, r.text
        tok = r.cookies.get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*GROUP_ADMIN)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


def _new_patient(s):
    r = s.post(f"{API}/patients", json={
        "first_name": "Exam",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"exam_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0300",
        "date_of_birth": "1979-11-15",
        "gender": "female",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _pick_provider(s):
    r = s.get(f"{API}/auth/providers", timeout=10)
    assert r.status_code == 200
    return r.json()[0]["id"]


def _book_appointment(s, patient_id, provider_id):
    for _ in range(3):
        base = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 45))
        start = base.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=random.randint(0, 8), minutes=random.randint(0, 50),
        )
        end = start + timedelta(minutes=20)
        r = s.post(f"{API}/appointments", json={
            "patient_id": patient_id, "provider_id": provider_id,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "reason": "Initial visit",
        }, timeout=15)
        if r.status_code == 201:
            return r.json()
    assert False, "Could not book an appointment in 3 tries"


def _launch_encounter(s, appt_id, *, encounter_type="new_patient_exam", episode_id=None):
    body = {"encounter_type": encounter_type}
    if episode_id:
        body["episode_id"] = episode_id
    r = s.post(f"{API}/appointments/{appt_id}/clinical/encounters", json=body, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()["encounter"]


def _seed_history_and_dx(s, patient_id):
    """Plant some values in history + diagnoses so prefill has something to
    pull. Returns (history_payload, dx_id)."""
    # history via PATCH
    history_payload = {
        "chief_complaint": "Low-back pain radiating to right leg",
        "history_of_present_illness": "Began 3 weeks ago after lifting",
        "medications": "Ibuprofen 400mg PRN",
        "allergies": "NKDA",
        "review_of_systems": "GI: nausea; MSK: low back stiffness",
    }
    r = s.patch(f"{API}/patients/{patient_id}/clinical/history", json=history_payload, timeout=10)
    assert r.status_code == 200, r.text
    # one active diagnosis
    r = s.post(f"{API}/patients/{patient_id}/clinical/diagnoses", json={
        "icd10_code": "M54.50", "label": "Low back pain, unspecified",
    }, timeout=10)
    assert r.status_code == 201, r.text
    return history_payload, r.json()["id"]


# ============================================================================
# Create + prefill + appointment auto-fill
# ============================================================================
def test_create_from_encounter_prefill_and_autofill(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    provider = _pick_provider(admin)
    appt = _book_appointment(admin, pid, provider)
    enc = _launch_encounter(admin, appt["id"])

    _, existing_dx_id = _seed_history_and_dx(admin, pid)

    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": True},
        timeout=15,
    )
    assert r.status_code == 201, r.text
    exam = r.json()
    assert exam["encounter_id"] == enc["id"]
    assert exam["appointment_id"] == appt["id"]
    assert exam["provider_id"] == provider
    assert exam["patient_id"] == pid
    assert exam["status"] == "draft"
    # Prefilled history
    assert exam["history"]["chief_complaint"] == "Low-back pain radiating to right leg"
    assert exam["history"]["medications"] == "Ibuprofen 400mg PRN"
    # Active diagnosis auto-selected
    assert existing_dx_id in exam["diagnosis_ids"]
    # Template snapshot frozen into the document
    assert exam["template_id"].startswith("default-")
    assert exam["template_snapshot"]["sections"][0]["id"] == "history"


def test_one_exam_per_encounter(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])

    r1 = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"]}, timeout=10,
    )
    assert r1.status_code == 201
    exam_id = r1.json()["id"]
    # Second attempt returns the existing exam, not a new one.
    r2 = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"]}, timeout=10,
    )
    assert r2.status_code == 200
    assert r2.headers.get("X-Exam-Existed") == "true"
    assert r2.json()["id"] == exam_id


def test_cancelled_encounter_rejects_exam_create(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    # cancel the encounter
    admin.post(
        f"{API}/patients/{pid}/clinical/encounters/{enc['id']}/cancel",
        json={"reason": "wrong appointment"}, timeout=10,
    )
    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"]}, timeout=10,
    )
    assert r.status_code == 409


# ============================================================================
# PATCH + prefill-is-non-destructive
# ============================================================================
def test_patch_merges_sections_and_prefill_preserves_edits(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": False},
        timeout=10,
    ).json()

    # Provider edits history + examination + assessment
    r = admin.patch(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}",
        json={
            "history": {"chief_complaint": "Provider-entered CC", "occupation_activity": "Warehouse"},
            "examination": {
                "vitals": {"blood_pressure": "118/76", "pulse_bpm": 72},
                "posture": "Forward head posture noted",
                "orthopedic_tests": [
                    {"name": "SLR", "region": "Lumbar", "result": "positive", "notes": "at 45°"},
                ],
                "muscle_strength": [
                    {"muscle": "L4 — tibialis anterior", "side": "right", "grade": 4},
                ],
                "range_of_motion": {
                    "lumbar": {"flexion": "30°", "extension": "10°"},
                },
            },
            "assessment": {"assessment_summary": "Mechanical LBP"},
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["history"]["chief_complaint"] == "Provider-entered CC"
    assert data["examination"]["vitals"]["pulse_bpm"] == 72
    assert data["examination"]["orthopedic_tests"][0]["result"] == "positive"
    assert data["examination"]["range_of_motion"]["lumbar"]["flexion"] == "30°"
    assert data["assessment"]["assessment_summary"] == "Mechanical LBP"

    # Now seed a chart history AFTER the provider edited — prefill must NOT
    # overwrite their chief_complaint.
    admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={
            "chief_complaint": "Chart-level CC from intake",
            "medications": "Fresh intake meds",
            "allergies": "PCN",
        },
        timeout=10,
    )
    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}/prefill",
        json={}, timeout=10,
    )
    assert r.status_code == 200, r.text
    merged = r.json()
    # Provider-entered CC is preserved
    assert merged["history"]["chief_complaint"] == "Provider-entered CC"
    # Empty field gets filled
    assert merged["history"]["medications"] == "Fresh intake meds"
    assert merged["history"]["allergies"] == "PCN"
    assert merged["prefilled_from_chart_at"] is not None


def test_cross_patient_diagnosis_id_rejected(admin):
    patient_a = _new_patient(admin)
    patient_b = _new_patient(admin)
    # dx on patient B
    r = admin.post(
        f"{API}/patients/{patient_b['id']}/clinical/diagnoses",
        json={"icd10_code": "M54.2", "label": "Cervicalgia"}, timeout=10,
    )
    dx_b = r.json()["id"]

    appt = _book_appointment(admin, patient_a["id"], _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{patient_a['id']}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": False},
        timeout=10,
    ).json()
    r = admin.patch(
        f"{API}/patients/{patient_a['id']}/clinical/exams/{exam['id']}",
        json={"diagnosis_ids": [dx_b]}, timeout=10,
    )
    assert r.status_code == 400, r.text


# ============================================================================
# Sign-ready / sign lifecycle
# ============================================================================
def test_sign_lifecycle_and_immutability(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": False},
        timeout=10,
    ).json()

    # mark-sign-ready from draft ok
    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}/mark-sign-ready", timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "sign_ready"

    # unmark back to draft ok
    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}/unmark-sign-ready", timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "draft"

    # Sign directly from draft
    r = admin.post(f"{API}/patients/{pid}/clinical/exams/{exam['id']}/sign", timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "signed"
    assert r.json()["signed_at"] and r.json()["signed_by"]

    # Double-sign 409
    r = admin.post(f"{API}/patients/{pid}/clinical/exams/{exam['id']}/sign", timeout=10)
    assert r.status_code == 409

    # PATCH on signed 409
    r = admin.patch(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}",
        json={"history": {"chief_complaint": "nope"}}, timeout=10,
    )
    assert r.status_code == 409

    # mark-sign-ready from signed 409
    r = admin.post(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}/mark-sign-ready", timeout=10,
    )
    assert r.status_code == 409


def test_sign_materializes_new_diagnoses_and_dedupes(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    # Seed an existing active diagnosis
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "m54.50", "label": "Old LBP",
              "body_region": "Lumbar"},
        timeout=10,
    )
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": True},
        timeout=10,
    ).json()

    # Add two new_diagnoses — one is a duplicate of the existing active one.
    r = admin.patch(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}",
        json={
            "new_diagnoses": [
                {"icd10_code": "M54.50", "label": "Dup LBP", "body_region": "Lumbar"},
                {"icd10_code": "M54.2", "label": "Cervicalgia", "body_region": "Cervical",
                 "is_primary": True},
            ],
        },
        timeout=10,
    )
    assert r.status_code == 200

    # Sign
    r = admin.post(f"{API}/patients/{pid}/clinical/exams/{exam['id']}/sign", timeout=10)
    assert r.status_code == 200
    signed = r.json()
    materialized = signed["materialized_diagnosis_ids"]
    # Exactly one NEW diagnosis row created (cervicalgia); dup was de-duped
    # to the pre-existing lbp id.
    # Count new rows by checking the full diagnosis list.
    rows = admin.get(f"{API}/patients/{pid}/clinical/diagnoses", timeout=10).json()
    codes = [r["icd10_code"] for r in rows]
    assert codes.count("M54.50") == 1  # No dup inserted
    assert "M54.2" in codes
    assert len(materialized) == 2  # Both pointers returned (one re-used id, one new)

    # Verify only one primary across active diagnoses for this patient
    active_primaries = [r for r in rows if r["is_primary"] and r["status"] == "active"]
    assert len(active_primaries) <= 1


# ============================================================================
# Narrative rendering
# ============================================================================
def test_narrative_contains_expected_sections(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    _seed_history_and_dx(admin, pid)
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": True},
        timeout=10,
    ).json()
    admin.patch(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}",
        json={
            "examination": {
                "vitals": {"blood_pressure": "118/76", "pulse_bpm": 68},
                "posture": "Anterior head translation",
                "range_of_motion": {"cervical": {"flexion": "40°", "extension": "45°"}},
                "orthopedic_tests": [{"name": "Spurling", "result": "negative"}],
            },
            "assessment": {
                "assessment_summary": "Cervicogenic pain",
                "treatment_recommendations": "Begin trial of care 3x/wk",
            },
        },
        timeout=15,
    )
    r = admin.get(f"{API}/patients/{pid}/clinical/exams/{exam['id']}/narrative", timeout=10)
    assert r.status_code == 200, r.text
    n = r.json()["narrative"]
    # Header
    assert "INITIAL EXAMINATION" in n
    # Sections
    assert "HISTORY" in n
    assert "EXAMINATION" in n
    assert "ASSESSMENT & PLAN" in n.upper()
    # Structured bits
    assert "BP: 118/76" in n
    assert "Pulse: 68" in n
    assert "Spurling" in n
    assert "cervical" in n.lower()
    # Diagnoses
    assert "DIAGNOSES" in n
    assert "M54.50" in n


# ============================================================================
# Chart visibility + summary
# ============================================================================
def test_chart_visibility_and_summary_counts(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])

    # Before: 0 exams
    s = admin.get(f"{API}/patients/{pid}/clinical/summary", timeout=10).json()
    assert s["initial_exams"] == {"total": 0, "open": 0}

    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": False},
        timeout=10,
    ).json()

    # After: 1 total, 1 open (draft)
    s = admin.get(f"{API}/patients/{pid}/clinical/summary", timeout=10).json()
    assert s["initial_exams"] == {"total": 1, "open": 1}

    # List reflects it
    rows = admin.get(f"{API}/patients/{pid}/clinical/exams", timeout=10).json()
    assert len(rows) == 1 and rows[0]["id"] == exam["id"]

    # Sign → still total=1 but open=0
    admin.post(f"{API}/patients/{pid}/clinical/exams/{exam['id']}/sign", timeout=10)
    s = admin.get(f"{API}/patients/{pid}/clinical/summary", timeout=10).json()
    assert s["initial_exams"] == {"total": 1, "open": 0}


# ============================================================================
# Tenant isolation + reauth
# ============================================================================
def test_tenant_isolation(admin, default_admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])
    exam = admin.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"], "prefill_from_chart": False},
        timeout=10,
    ).json()

    assert default_admin.get(
        f"{API}/patients/{pid}/clinical/exams", timeout=10,
    ).status_code == 404
    assert default_admin.get(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}", timeout=10,
    ).status_code == 404
    assert default_admin.patch(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}",
        json={"history": {"chief_complaint": "hack"}}, timeout=10,
    ).status_code == 404
    assert default_admin.post(
        f"{API}/patients/{pid}/clinical/exams/{exam['id']}/sign", timeout=10,
    ).status_code == 404


def test_writes_require_reauth(admin):
    patient = _new_patient(admin)
    pid = patient["id"]
    appt = _book_appointment(admin, pid, _pick_provider(admin))
    enc = _launch_encounter(admin, appt["id"])

    s = _login(*GROUP_ADMIN, reauth=False)
    r = s.post(
        f"{API}/patients/{pid}/clinical/exams",
        json={"encounter_id": enc["id"]}, timeout=10,
    )
    assert r.status_code == 401
    assert "re-auth" in (r.json().get("detail") or "").lower()
