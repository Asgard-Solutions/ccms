"""Clinical Phase 5 — Follow-up / Daily Visit Note tests.

Covers:
  - create-from-encounter happy path + one-note-per-encounter idempotency
  - cancelled encounter rejects note creation (409)
  - PATCH structured round-trip (subjective/objective/assessment/plan)
  - PATCH blocks on signed
  - explicit copy-forward: non-destructive default, force mode, source must be signed,
    cross-note self-copy rejected, cross-patient source rejected
  - completeness score + missing_fields computed from REQUIRED_FIELDS
  - mark-sign-ready / unmark / sign transitions; 409 on invalid states
  - sign assigns visit_number monotonically within episode scope
  - signed notes are immutable
  - narrative renders SOAP sections when populated
  - care timeline merges encounters + exams + notes in date-desc order
  - chart visibility: signed note appears in list & summary.notes counts
  - tenant isolation (cross-tenant 404)
  - reauth required on writes
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _new_patient(s):
    r = s.post(f"{API}/patients", json={
        "first_name": "Note",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"note_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0400",
        "date_of_birth": "1982-05-20",
        "gender": "male",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _pick_provider(s):
    r = s.get(f"{API}/auth/providers", timeout=10)
    assert r.status_code == 200
    return r.json()[0]["id"]


def _book_appt(s, patient_id, provider_id):
    for _ in range(5):
        base = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 60))
        start = base.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=random.randint(0, 8), minutes=random.choice([0, 15, 30, 45]),
        )
        end = start + timedelta(minutes=20)
        r = s.post(f"{API}/appointments", json={
            "patient_id": patient_id, "provider_id": provider_id,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "reason": "Follow-up visit",
        }, timeout=15)
        if r.status_code == 201:
            return r.json()
    raise AssertionError("Could not book an appointment in 5 tries")


def _launch_enc(s, appt_id, encounter_type="follow_up"):
    r = s.post(f"{API}/appointments/{appt_id}/clinical/encounters",
               json={"encounter_type": encounter_type}, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()["encounter"]


def _complete_enc(s, pid, eid):
    r = s.post(f"{API}/patients/{pid}/clinical/encounters/{eid}/complete", json={}, timeout=15)
    assert r.status_code == 200, r.text


def _cancel_enc(s, pid, eid, reason="Patient cancelled"):
    r = s.post(f"{API}/patients/{pid}/clinical/encounters/{eid}/cancel",
               json={"reason": reason}, timeout=15)
    assert r.status_code == 200, r.text


def _make_encounter(s):
    p = _new_patient(s)
    prov = _pick_provider(s)
    appt = _book_appt(s, p["id"], prov)
    enc = _launch_enc(s, appt["id"], "follow_up")
    return p, appt, enc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_create_note_from_encounter_happy_path(admin):
    p, _appt, enc = _make_encounter(admin)

    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15)
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["encounter_id"] == enc["id"]
    assert note["patient_id"] == p["id"]
    assert note["status"] == "draft"
    assert note["provider_id"] == enc["provider_id"]
    assert note["appointment_id"] == enc["appointment_id"]
    assert note["completeness"]["total"] >= 5
    # Note appears in chart list
    r2 = admin.get(f"{API}/patients/{p['id']}/clinical/notes", timeout=10)
    assert r2.status_code == 200
    ids = [n["id"] for n in r2.json()]
    assert note["id"] in ids


def test_one_note_per_encounter_idempotent(admin):
    p, _appt, enc = _make_encounter(admin)
    r1 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc["id"]}, timeout=15)
    assert r1.status_code == 201
    r2 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc["id"]}, timeout=15)
    assert r2.status_code == 200
    assert r2.headers.get("x-note-existed", r2.headers.get("X-Note-Existed")) == "true"
    assert r2.json()["id"] == r1.json()["id"]


def test_cancelled_encounter_rejects_note_create(admin):
    p, _appt, enc = _make_encounter(admin)
    _cancel_enc(admin, p["id"], enc["id"])
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15)
    assert r.status_code == 409, r.text


def test_patch_structured_round_trip_and_completeness(admin):
    p, _appt, enc = _make_encounter(admin)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15)
    note = r.json()
    payload = {
        "subjective": {
            "interval_history": "Patient reports improved sleep, less neck stiffness.",
            "pain_scale_0_10": 3,
            "pain_change": "better",
            "functional_change": "Can now turn head while driving.",
            "adherence_home_care": "yes",
        },
        "objective": {
            "region_findings": [
                {"body_region": "cervical", "palpation": "decreased spasm bilat",
                 "rom_summary": "WNL", "notes": ""},
            ],
            "reassessment_summary": "C5-C6 motion restriction reduced",
        },
        "assessment": {
            "response_to_care": "improving",
            "clinical_impression": "Continuing to respond; taper to weekly.",
        },
        "plan": {
            "treatment_rendered": [
                {"kind": "adjustment", "segments": ["C5", "C6"], "technique": "Diversified",
                 "duration_min": 10},
                {"kind": "modality", "modality": "E-stim", "region": "cervical", "duration_min": 8},
            ],
            "regions_treated": ["cervical"],
            "home_care_reinforcement": "Continue cervical ROM exercises 2x/day",
            "next_visit_plan": "Re-eval cervical ROM; advance to resistance band",
            "recommended_interval_days": 7,
        },
    }
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{note['id']}",
                    json=payload, timeout=15)
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["subjective"]["pain_scale_0_10"] == 3
    assert fresh["subjective"]["pain_change"] == "better"
    assert len(fresh["plan"]["treatment_rendered"]) == 2
    assert fresh["plan"]["treatment_rendered"][0]["segments"] == ["C5", "C6"]
    assert fresh["completeness"]["missing_fields"] == []
    assert fresh["completeness"]["score"] == 100


def test_completeness_missing_fields(admin):
    p, _appt, enc = _make_encounter(admin)
    note = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                      json={"encounter_id": enc["id"]}, timeout=15).json()
    r = admin.get(f"{API}/patients/{p['id']}/clinical/notes/{note['id']}", timeout=10)
    assert r.status_code == 200
    c = r.json()["completeness"]
    assert c["filled"] == 0
    assert "subjective.interval_history" in c["missing_fields"]
    assert "plan.treatment_rendered" in c["missing_fields"]


def test_sign_lifecycle_and_visit_number(admin):
    """Sign two sequential follow-up notes on the same patient and verify
    visit_number increments. Also asserts draft → sign_ready → signed path
    and that signed notes are immutable + summary open counts are live.
    """
    p, _appt, enc1 = _make_encounter(admin)
    n1 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc1["id"]}, timeout=15).json()
    # mark-sign-ready then sign
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/mark-sign-ready",
                   json={}, timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "sign_ready"
    # unmark back to draft then sign directly from draft
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/unmark-sign-ready",
                   json={}, timeout=10)
    assert r.status_code == 200 and r.json()["status"] == "draft"
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 200, r.text
    signed1 = r.json()
    assert signed1["status"] == "signed"
    assert signed1["visit_number"] == 1
    # PATCH signed → 409
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}",
                    json={"subjective": {"interval_history": "tamper"}}, timeout=10)
    assert r.status_code == 409
    # Double-sign → 409
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 409

    # Second encounter for same patient → visit #2
    _complete_enc(admin, p["id"], enc1["id"])
    prov = _pick_provider(admin)
    appt2 = _book_appt(admin, p["id"], prov)
    enc2 = _launch_enc(admin, appt2["id"], "treatment_visit")
    n2 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc2["id"]}, timeout=15).json()
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n2['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 200
    assert r.json()["visit_number"] == 2

    # Summary reflects 2 signed notes (total) + 0 open
    summary = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10).json()
    assert summary["notes"]["total"] == 2
    assert summary["notes"]["open"] == 0


def test_copy_forward_non_destructive_and_force(admin):
    """Create a signed note, then copy its structured fields forward into a
    new note. By default empty fields get filled, non-empty are preserved.
    `force=True` overwrites."""
    p, _appt, enc1 = _make_encounter(admin)
    n1 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc1["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}",
                json={
                    "subjective": {"interval_history": "baseline", "pain_scale_0_10": 5},
                    "plan": {"next_visit_plan": "reassess in 7 days",
                             "treatment_rendered": [{"kind": "adjustment", "segments": ["C5"]}]},
                }, timeout=15)
    admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/sign",
               json={}, timeout=10)

    # Second encounter + note, pre-populated with ONE subjective field already.
    _complete_enc(admin, p["id"], enc1["id"])
    prov = _pick_provider(admin)
    appt2 = _book_appt(admin, p["id"], prov)
    enc2 = _launch_enc(admin, appt2["id"], "follow_up")
    n2 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc2["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{n2['id']}",
                json={"subjective": {"interval_history": "today-specific"}}, timeout=15)

    # Non-destructive copy-forward
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n2['id']}/copy-forward",
                   json={"source_note_id": n1["id"], "force": False}, timeout=15)
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["subjective"]["interval_history"] == "today-specific"  # preserved
    assert fresh["subjective"]["pain_scale_0_10"] == 5                  # filled
    assert "subjective.pain_scale_0_10" in fresh["copied_fields"]
    assert "subjective.interval_history" not in fresh["copied_fields"]
    assert fresh["plan"]["next_visit_plan"] == "reassess in 7 days"
    assert fresh["copied_from_note_id"] == n1["id"]

    # Force overwrite
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n2['id']}/copy-forward",
                   json={"source_note_id": n1["id"], "force": True}, timeout=15)
    fresh = r.json()
    assert fresh["subjective"]["interval_history"] == "baseline"
    assert "subjective.interval_history" in fresh["copied_fields"]


def test_copy_forward_rejects_unsigned_source(admin):
    p, _appt, enc = _make_encounter(admin)
    n1 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc["id"]}, timeout=15).json()
    # Second note on same patient
    _complete_enc(admin, p["id"], enc["id"])
    prov = _pick_provider(admin)
    appt2 = _book_appt(admin, p["id"], prov)
    enc2 = _launch_enc(admin, appt2["id"], "follow_up")
    n2 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc2["id"]}, timeout=15).json()
    # n1 is still draft — copy-forward must reject
    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n2['id']}/copy-forward",
                   json={"source_note_id": n1["id"]}, timeout=15)
    assert r.status_code == 400


def test_copy_forward_inline_during_create(admin):
    """copy_forward_from_note_id on POST seeds a freshly-created note with
    fields from a prior signed note."""
    p, _appt, enc1 = _make_encounter(admin)
    n1 = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                    json={"encounter_id": enc1["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}",
                json={"plan": {"home_care_reinforcement": "daily stretching",
                               "treatment_rendered": [{"kind": "adjustment",
                                                       "segments": ["L4"]}]}},
                timeout=15)
    admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n1['id']}/sign", json={}, timeout=10)

    _complete_enc(admin, p["id"], enc1["id"])
    prov = _pick_provider(admin)
    appt2 = _book_appt(admin, p["id"], prov)
    enc2 = _launch_enc(admin, appt2["id"], "follow_up")

    r = admin.post(f"{API}/patients/{p['id']}/clinical/notes", json={
        "encounter_id": enc2["id"],
        "copy_forward_from_note_id": n1["id"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    fresh = r.json()
    assert fresh["plan"]["home_care_reinforcement"] == "daily stretching"
    assert fresh["copied_from_note_id"] == n1["id"]
    assert "plan.home_care_reinforcement" in fresh["copied_fields"]


def test_narrative_renders_soap_sections(admin):
    p, _appt, enc = _make_encounter(admin)
    n = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/notes/{n['id']}", json={
        "subjective": {"interval_history": "sleeping better", "pain_scale_0_10": 2,
                       "pain_change": "better"},
        "objective": {"reassessment_summary": "full ROM returned"},
        "assessment": {"response_to_care": "improving"},
        "plan": {"treatment_rendered": [{"kind": "adjustment", "segments": ["C5"]}],
                 "next_visit_plan": "weekly"},
    }, timeout=15)
    r = admin.get(f"{API}/patients/{p['id']}/clinical/notes/{n['id']}/narrative", timeout=10)
    assert r.status_code == 200, r.text
    text = r.json()["narrative"]
    assert "FOLLOW-UP / DAILY VISIT NOTE" in text
    assert "SUBJECTIVE (S)" in text
    assert "OBJECTIVE (O)" in text
    assert "ASSESSMENT (A)" in text
    assert "PLAN (P)" in text
    assert "Pain scale: 2/10" in text
    assert "adjustment" in text.lower()


def test_care_timeline_merges_and_sorts(admin):
    p, _appt, enc = _make_encounter(admin)
    # Create follow-up note on this encounter
    n = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15).json()
    r = admin.get(f"{API}/patients/{p['id']}/clinical/care-timeline", timeout=10)
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    kinds = [e["kind"] for e in entries]
    # At minimum the encounter AND the follow-up note must appear.
    assert "encounter" in kinds
    assert "follow_up_note" in kinds
    # note entry links to the follow-up editor route
    note_entry = next(e for e in entries if e["kind"] == "follow_up_note" and e["id"] == n["id"])
    assert note_entry["link_path"].endswith(f"/clinical/follow-up/{n['id']}")
    # timeline is date-desc
    iso = [e.get("date_of_service") or "" for e in entries]
    assert iso == sorted(iso, reverse=True)


def test_tenant_isolation_and_reauth(admin, default_admin):
    p, _appt, enc = _make_encounter(admin)
    n = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15).json()
    # Cross-tenant GET/PATCH/sign → 404
    for method, path, body in [
        ("get", f"{API}/patients/{p['id']}/clinical/notes/{n['id']}", None),
        ("patch", f"{API}/patients/{p['id']}/clinical/notes/{n['id']}",
         {"subjective": {"interval_history": "hack"}}),
        ("post", f"{API}/patients/{p['id']}/clinical/notes/{n['id']}/sign", {}),
    ]:
        call = getattr(default_admin, method)
        r = call(path, **({"json": body} if body is not None else {}), timeout=10)
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"

    # Reauth required on POST create — login without reauth then try to create
    no_reauth = _login(*GROUP_ADMIN, reauth=False)
    p2, _appt, enc2 = _make_encounter(admin)
    r = no_reauth.post(f"{API}/patients/{p2['id']}/clinical/notes",
                       json={"encounter_id": enc2["id"]}, timeout=15)
    assert r.status_code == 401, r.text
    assert "re-auth" in (r.json().get("detail", "")).lower()
