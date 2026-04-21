"""Clinical module Phase 2 — history + diagnoses tests.

Coverage:
 History:
  * GET auto-seeds from latest completed intake form on first access
  * GET on patient with NO completed intake returns empty history (no seed form)
  * PATCH flips field's source to provider_edit
  * PATCH exclude_unset — omitted fields left alone
  * POST /import pulls missing fields, SKIPS provider-edited fields
  * POST /import with explicit form_id works; rejects non-completed form 409
  * POST /import with no completed form available → 409
  * tenant isolation — cross-tenant history access 404
  * require_reauth — PATCH without reauth cookie → 401

 Diagnoses:
  * create + list + get + patch + resolve + reactivate lifecycle
  * episode linkage validated (cross-tenant + cross-patient episode 400)
  * is_primary uniqueness enforced within (patient, episode, active)
  * list filters by status_in and episode_id
  * resolve/reactivate 409 on wrong status
  * tenant isolation — cross-tenant probes 404
  * patient role blocked
  * summary endpoint reflects live diagnoses counts + history_present flag
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
PATIENT_USER = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token")
    assert tok
    s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r.status_code == 200, r.text
        rt = r.cookies.get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*GROUP_ADMIN)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


def _new_patient(s: requests.Session, *, with_intake_form: bool = False) -> dict:
    r = s.post(f"{API}/patients", json={
        "first_name": "Phase2",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"p2_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0140",
        "date_of_birth": "1985-05-01",
        "gender": "male",
    }, timeout=15)
    assert r.status_code == 201, r.text
    patient = r.json()
    if with_intake_form:
        # POST a fully populated intake form + mark it completed.
        body = {
            "seed_from_patient": False,
            "clinical_intake": {
                "chief_complaint": "Right-sided low back pain after lifting",
                "pain_level": 7,
                "complaint_onset": "2026-01-03",
                "pain_locations": ["Lower back", "Right buttock"],
                "aggravating_factors": "sitting, bending forward",
                "relieving_factors": "walking, ice",
                "prior_treatments": "PT for 4 weeks in 2024",
                "medications": "Ibuprofen 400mg PRN",
                "allergies": "NKDA",
                "past_medical_history": "Hypertension controlled",
                "past_surgical_history": "Appendectomy 2015",
                "family_history": "Father: cardiac disease",
                "social_history": "Non-smoker, occasional alcohol",
            },
            "case_details": {
                "case_type": "workers_comp",
                "date_of_injury": "2026-01-02",
                "employer_for_claim": "Acme Corp",
                "work_comp_carrier": "State Fund",
                "claim_number": "WC-2026-0042",
                "return_to_work_status": "modified duty",
            },
            "notes": "Patient reports difficulty sleeping due to pain.",
        }
        r = s.post(f"{API}/patients/{patient['id']}/intake-forms", json=body, timeout=15)
        assert r.status_code == 201, r.text
        form_id = r.json()["id"]
        # Complete the form so it's eligible for history import
        r = s.patch(
            f"{API}/patients/{patient['id']}/intake-forms/{form_id}",
            json={"status": "completed"}, timeout=10,
        )
        assert r.status_code == 200, r.text
        patient["_intake_form_id"] = form_id
    return patient


# ============================================================================
# HISTORY TESTS
# ============================================================================
def test_history_autoseeds_from_latest_completed_intake(admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    r = admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["patient_id"] == pid
    assert h["seeded_from_form_id"] == p["_intake_form_id"]
    # Mapped fields present
    assert h["chief_complaint"] == "Right-sided low back pain after lifting"
    assert h["severity"] == 7
    assert h["pain_locations"] == ["Lower back", "Right buttock"]
    assert h["aggravating_factors"] == ["sitting", "bending forward"]
    assert h["medications"] == "Ibuprofen 400mg PRN"
    assert h["allergies"] == "NKDA"
    # Source tracking
    meta = h["field_meta"]
    assert meta["chief_complaint"]["source"] == "intake"
    assert meta["severity"]["source_form_id"] == p["_intake_form_id"]
    # Accident + WC details structured
    assert h["accident_details"]["case_type"] == "workers_comp"
    assert h["accident_details"]["date_of_injury"] == "2026-01-02"
    assert h["work_comp_details"]["employer_for_claim"] == "Acme Corp"


def test_history_empty_when_no_completed_intake(admin):
    p = _new_patient(admin, with_intake_form=False)
    r = admin.get(f"{API}/patients/{p['id']}/clinical/history", timeout=10)
    assert r.status_code == 200
    h = r.json()
    assert h["seeded_from_form_id"] is None
    assert h["chief_complaint"] is None
    assert h["severity"] is None
    assert h["field_meta"] == {}


def test_history_patch_marks_provider_edit(admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    # Ensure history seeded
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)

    r = admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={
            "chief_complaint": "Provider-refined complaint",
            "occupation": "Warehouse associate",
            "activity_level": "moderate",
        },
        timeout=10,
    )
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["chief_complaint"] == "Provider-refined complaint"
    assert h["occupation"] == "Warehouse associate"
    assert h["activity_level"] == "moderate"
    assert h["field_meta"]["chief_complaint"]["source"] == "provider_edit"
    assert h["field_meta"]["occupation"]["source"] == "provider_edit"
    # Untouched intake fields keep their intake source
    assert h["field_meta"]["severity"]["source"] == "intake"


def test_history_patch_exclude_unset(admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    r = admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={"activity_level": "light"},
        timeout=10,
    )
    assert r.status_code == 200
    # The severity (from intake) must still be present
    assert r.json()["severity"] == 7
    assert r.json()["activity_level"] == "light"


def test_history_import_skips_provider_edited_fields(admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    # seed
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    # provider edits chief_complaint and medications
    admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={"chief_complaint": "REVISED CC", "medications": "Provider-curated meds"},
        timeout=10,
    )
    # author a NEW intake form with different values and complete it
    r = admin.post(f"{API}/patients/{pid}/intake-forms", json={
        "seed_from_patient": False,
        "clinical_intake": {
            "chief_complaint": "Should not overwrite",
            "medications": "Also should not overwrite",
            "severity": 3,  # ignored at mapping layer
            "pain_level": 4,
            "allergies": "Penicillin",   # NEW — should import
        },
    }, timeout=10)
    assert r.status_code == 201
    new_form_id = r.json()["id"]
    r = admin.patch(
        f"{API}/patients/{pid}/intake-forms/{new_form_id}",
        json={"status": "completed"}, timeout=10,
    )
    assert r.status_code == 200

    # Trigger explicit import
    r = admin.post(
        f"{API}/patients/{pid}/clinical/history/import",
        json={"form_id": new_form_id}, timeout=10,
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["source_form_id"] == new_form_id
    # Provider-edited fields must be in skipped, NOT imported
    assert "chief_complaint" in result["skipped_fields"]
    assert "medications" in result["skipped_fields"]
    # Severity was intake before -> gets overwritten (still intake source)
    assert "severity" in result["imported_fields"]
    # Allergies was empty -> imported
    assert "allergies" in result["imported_fields"]
    # The values confirm preservation
    h = result["history"]
    assert h["chief_complaint"] == "REVISED CC"
    assert h["medications"] == "Provider-curated meds"
    assert h["severity"] == 4
    assert h["allergies"] == "Penicillin"


def test_history_import_rejects_noncompleted_form(admin):
    p = _new_patient(admin, with_intake_form=False)
    pid = p["id"]
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    # Draft form
    r = admin.post(f"{API}/patients/{pid}/intake-forms", json={
        "seed_from_patient": False,
        "clinical_intake": {"chief_complaint": "draft only"},
    }, timeout=10)
    assert r.status_code == 201
    draft_id = r.json()["id"]
    r = admin.post(
        f"{API}/patients/{pid}/clinical/history/import",
        json={"form_id": draft_id}, timeout=10,
    )
    assert r.status_code == 409


def test_history_import_without_any_completed_form(admin):
    p = _new_patient(admin, with_intake_form=False)
    pid = p["id"]
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    r = admin.post(
        f"{API}/patients/{pid}/clinical/history/import",
        json={}, timeout=10,
    )
    assert r.status_code == 409


def test_history_tenant_isolation(admin, default_admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)
    # Different tenant admin gets 404 on read + PATCH + import
    assert default_admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10).status_code == 404
    assert default_admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={"chief_complaint": "hack"},
        timeout=10,
    ).status_code == 404
    assert default_admin.post(
        f"{API}/patients/{pid}/clinical/history/import",
        json={}, timeout=10,
    ).status_code == 404


def test_history_patch_requires_reauth(admin):
    p = _new_patient(admin, with_intake_form=True)
    pid = p["id"]
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)

    # Fresh session without reauth
    s = _login(*GROUP_ADMIN, reauth=False)
    r = s.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={"chief_complaint": "should be blocked"},
        timeout=10,
    )
    assert r.status_code == 401
    body = r.json()
    assert "re-auth" in (body.get("detail") or "").lower()


# ============================================================================
# DIAGNOSES TESTS
# ============================================================================
def test_diagnosis_full_lifecycle(admin):
    p = _new_patient(admin)
    pid = p["id"]

    # Create an episode to link against
    r = admin.post(
        f"{API}/patients/{pid}/clinical/episodes",
        json={"case_type": "injury_episode", "title": "Lumbar strain"},
        timeout=10,
    )
    assert r.status_code == 201
    eid = r.json()["id"]

    # Create primary dx
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={
            "icd10_code": "m54.50",
            "label": "Low back pain, unspecified",
            "episode_id": eid,
            "is_primary": True,
            "body_region": "lumbar",
            "laterality": "midline",
            "chronicity": "acute",
            "onset_date": "2026-01-02",
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    dx = r.json()
    assert dx["icd10_code"] == "M54.50"  # upper-cased
    assert dx["status"] == "active"
    assert dx["is_primary"] is True

    # Read
    r = admin.get(f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}", timeout=10)
    assert r.status_code == 200
    assert r.json()["label"] == "Low back pain, unspecified"

    # Patch label
    r = admin.patch(
        f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}",
        json={"label": "Chronic low back pain"},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["label"] == "Chronic low back pain"

    # Resolve
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}/resolve",
        json={"resolution_notes": "Fully recovered"},
        timeout=10,
    )
    assert r.status_code == 200
    row = r.json()
    assert row["status"] == "resolved"
    assert row["resolution_notes"] == "Fully recovered"
    assert row["resolved_date"] is not None

    # Double-resolve 409
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}/resolve",
        json={"resolution_notes": "again"}, timeout=10,
    )
    assert r.status_code == 409

    # Reactivate
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}/reactivate", timeout=10,
    )
    assert r.status_code == 200
    row = r.json()
    assert row["status"] == "active"
    assert row["resolved_date"] is None
    # Double reactivate 409
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{dx['id']}/reactivate", timeout=10,
    )
    assert r.status_code == 409


def test_primary_uniqueness_within_episode_group(admin):
    p = _new_patient(admin)
    pid = p["id"]
    r = admin.post(
        f"{API}/patients/{pid}/clinical/episodes",
        json={"case_type": "injury_episode", "title": "Neck case"},
        timeout=10,
    )
    eid = r.json()["id"]

    # Two primaries on same episode → second should flip the first off
    d1 = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.2", "label": "Cervicalgia", "episode_id": eid, "is_primary": True},
        timeout=10,
    ).json()
    d2 = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M25.511", "label": "Right shoulder pain", "episode_id": eid, "is_primary": True},
        timeout=10,
    ).json()

    # Only d2 should be primary now
    r = admin.get(
        f"{API}/patients/{pid}/clinical/diagnoses",
        params={"episode_id": eid, "status_in": "active"},
        timeout=10,
    )
    rows = r.json()
    by_id = {x["id"]: x for x in rows}
    assert by_id[d1["id"]]["is_primary"] is False
    assert by_id[d2["id"]]["is_primary"] is True

    # Orphan diagnosis (no episode) is a SEPARATE primary group — so it
    # stays primary even when another primary exists in an episode group.
    orphan = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "Z00.00", "label": "General wellness check", "is_primary": True},
        timeout=10,
    ).json()
    assert orphan["is_primary"] is True
    # d2 still primary on the episode side
    r = admin.get(
        f"{API}/patients/{pid}/clinical/diagnoses/{d2['id']}", timeout=10,
    )
    assert r.json()["is_primary"] is True


def test_invalid_episode_linkage_rejected(admin, default_admin):
    p = _new_patient(admin)
    pid = p["id"]

    # Cross-tenant episode id
    other = _new_patient(default_admin)
    r = default_admin.post(
        f"{API}/patients/{other['id']}/clinical/episodes",
        json={"case_type": "injury_episode", "title": "other tenant ep"},
        timeout=10,
    )
    assert r.status_code == 201
    cross_ep = r.json()["id"]
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.50", "label": "x", "episode_id": cross_ep},
        timeout=10,
    )
    assert r.status_code == 400

    # Bogus episode id
    r = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.50", "label": "x",
              "episode_id": "00000000-0000-0000-0000-000000000000"},
        timeout=10,
    )
    assert r.status_code == 400


def test_list_filters_and_tenant_isolation(admin, default_admin):
    p = _new_patient(admin)
    pid = p["id"]
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.2", "label": "Neck"}, timeout=10,
    )
    d2 = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "R51", "label": "Headache"}, timeout=10,
    ).json()
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{d2['id']}/resolve",
        json={"resolution_notes": "gone"}, timeout=10,
    )

    # Filter active only
    rows = admin.get(
        f"{API}/patients/{pid}/clinical/diagnoses",
        params={"status_in": "active"}, timeout=10,
    ).json()
    assert len(rows) == 1
    assert rows[0]["icd10_code"] == "M54.2"

    # Filter resolved only
    rows = admin.get(
        f"{API}/patients/{pid}/clinical/diagnoses",
        params={"status_in": "resolved"}, timeout=10,
    ).json()
    assert len(rows) == 1
    assert rows[0]["icd10_code"] == "R51"

    # Cross-tenant 404 on list (patient not in their tenant)
    r = default_admin.get(f"{API}/patients/{pid}/clinical/diagnoses", timeout=10)
    assert r.status_code == 404


def test_patient_role_cannot_access_diagnoses(admin):
    p = _new_patient(admin)
    pid = p["id"]
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.2", "label": "Neck"}, timeout=10,
    )
    pt = _login(*PATIENT_USER, reauth=False)
    # Patient cannot list other patients' diagnoses
    r = pt.get(f"{API}/patients/{pid}/clinical/diagnoses", timeout=10)
    assert r.status_code in (403, 404)
    # Patient cannot create diagnoses
    r = pt.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.2", "label": "x"}, timeout=10,
    )
    assert r.status_code in (403, 404)


def test_summary_reflects_history_present_and_dx_counts(admin):
    p = _new_patient(admin)
    pid = p["id"]
    r = admin.get(f"{API}/patients/{pid}/clinical/summary", timeout=10).json()
    assert r["history_present"] == 0
    assert r["diagnoses"] == {"total": 0, "open": 0}

    # Create history + 2 dx (1 resolved)
    admin.get(f"{API}/patients/{pid}/clinical/history", timeout=10)  # auto-seed no-op
    admin.patch(
        f"{API}/patients/{pid}/clinical/history",
        json={"chief_complaint": "LBP"}, timeout=10,
    )
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "M54.5", "label": "LBP"}, timeout=10,
    )
    d2 = admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses",
        json={"icd10_code": "R51", "label": "Headache"}, timeout=10,
    ).json()
    admin.post(
        f"{API}/patients/{pid}/clinical/diagnoses/{d2['id']}/resolve",
        json={"resolution_notes": "ok"}, timeout=10,
    )

    r = admin.get(f"{API}/patients/{pid}/clinical/summary", timeout=10).json()
    assert r["history_present"] == 1
    assert r["diagnoses"] == {"total": 2, "open": 1}
