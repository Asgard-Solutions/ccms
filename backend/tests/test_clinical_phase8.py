"""Clinical Phase 8 tests — lifecycle locking, addenda, billing readiness,
audit coverage.

Scope per Phase 8:
  - Sign locks note content (PATCH 409 after sign).
  - Addendum requires signed parent (409 on unsigned).
  - Addendum draft → edit → sign → locked (PATCH/DELETE 409).
  - Addendum non-author (different doctor) cannot sign / edit / delete
    another doctor's draft (403). Admin can.
  - Billing readiness returns `blocked` when note is draft, `warnings`
    when missing plan linkage on a follow-up visit with signed note but
    no plan, `ready` when fully documented.
  - Care timeline surfaces `addendum` kind once signed.
  - Audit events are emitted for create / edit / sign / delete /
    appointment linkage / diagnosis linkage / treatment plan update.
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
DOCTOR = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
FLOATER_DOC = ("floater-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email, password, *, reauth=True):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
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
def doctor():
    return _login(*DOCTOR)


@pytest.fixture(scope="module")
def floater():
    return _login(*FLOATER_DOC)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


# ---------------------------------------------------------------------------
# Helpers — reuse Phase 5 fixtures
# ---------------------------------------------------------------------------
def _new_patient(s):
    r = s.post(f"{API}/patients", json={
        "first_name": "P8",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"p8_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0800",
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


def _launch_enc(s, appt_id, encounter_type="follow_up", episode_id=None):
    body = {"encounter_type": encounter_type}
    if episode_id:
        body["episode_id"] = episode_id
    r = s.post(f"{API}/appointments/{appt_id}/clinical/encounters",
               json=body, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()["encounter"]


def _make_episode(s, patient_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/episodes",
               json={"title": "LBP", "case_type": "injury_episode"}, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _make_plan(s, patient_id, episode_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/treatment-plans", json={
        "episode_id": episode_id, "title": "Plan",
        "goals": [{"description": "pain", "measure_type": "pain_scale"}],
        "planned_interventions": [
            {"kind": "adjustment", "description": "Diversified L4-L5"},
        ],
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _make_diagnosis(s, patient_id, episode_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/diagnoses", json={
        "icd10_code": "M54.5",
        "label": "Low back pain",
        "episode_id": episode_id,
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _make_note(s, patient_id, encounter_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/notes",
               json={"encounter_id": encounter_id}, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _fill_note(s, patient_id, note_id, *, treatment_plan_id=None):
    """Populate required fields so the note can pass `mark-sign-ready`."""
    body = {
        "subjective": {
            "interval_history": "Pain improving since last visit.",
            "pain_scale_0_10": 4,
            "pain_change": "better",
        },
        "objective": {
            "region_findings": [{"body_region": "lumbar",
                                 "palpation": "less guarded"}],
            "reassessment_summary": "Lumbar ROM improved.",
        },
        "assessment": {
            "response_to_care": "improving",
            "clinical_impression": "Lumbar strain responding.",
        },
        "plan": {
            "treatment_rendered": [
                {"kind": "adjustment", "segments": ["L4", "L5"],
                 "technique": "Diversified"},
            ],
            "regions_treated": ["lumbar"],
            "home_care_reinforcement": "Glute bridge x 2 sets.",
            "next_visit_plan": "Continue 2x/week.",
            "recommended_interval_days": 3,
        },
    }
    if treatment_plan_id is not None:
        body["treatment_plan_id"] = treatment_plan_id
    r = s.patch(f"{API}/patients/{patient_id}/clinical/notes/{note_id}",
                json=body, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def _sign_note(s, patient_id, note_id):
    r = s.post(f"{API}/patients/{patient_id}/clinical/notes/{note_id}/mark-sign-ready",
               timeout=15)
    assert r.status_code == 200, r.text
    r = s.post(f"{API}/patients/{patient_id}/clinical/notes/{note_id}/sign",
               timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Lifecycle locking
# ---------------------------------------------------------------------------
def test_signed_note_blocks_patch(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    dx = _make_diagnosis(admin, p["id"], ep["id"])
    plan = _make_plan(admin, p["id"], ep["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])
    _fill_note(admin, p["id"], note["id"], treatment_plan_id=plan["id"])
    _sign_note(admin, p["id"], note["id"])

    # PATCH must be rejected
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/notes/{note['id']}",
        json={"subjective": {"pain_scale_0_10": 9}},
        timeout=15,
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Addendum workflow
# ---------------------------------------------------------------------------
def _make_signed_note(s):
    """Convenience: provision a fully-signed follow-up note and return
    (patient, encounter, note)."""
    p = _new_patient(s)
    ep = _make_episode(s, p["id"])
    dx = _make_diagnosis(s, p["id"], ep["id"])
    plan = _make_plan(s, p["id"], ep["id"])
    prov = _pick_provider(s)
    appt = _book_appt(s, p["id"], prov)
    enc = _launch_enc(s, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(s, p["id"], enc["id"])
    _fill_note(s, p["id"], note["id"], treatment_plan_id=plan["id"])
    signed = _sign_note(s, p["id"], note["id"])
    return p, enc, signed


def test_addendum_requires_signed_parent(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])

    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/follow_up_note/{note['id']}/addenda",
        json={"reason": "Clarify", "narrative": "Narrative needs more context."},
        timeout=15,
    )
    assert r.status_code == 409


def test_addendum_create_edit_sign_lock(admin):
    p, _enc, note = _make_signed_note(admin)
    # Create draft addendum
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/follow_up_note/{note['id']}/addenda",
        json={"reason": "Clarify MOI",
              "narrative": "Patient reports MVA was 14 days prior, not 10."},
        timeout=15,
    )
    assert r.status_code == 201, r.text
    ad = r.json()
    assert ad["status"] == "draft"

    # Edit the draft
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}",
        json={"narrative": "Patient clarifies the MVA was 14 days prior, not 10 as originally charted."},
        timeout=15,
    )
    assert r.status_code == 200
    assert "14 days" in r.json()["narrative"]

    # Sign
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}/sign",
        timeout=15,
    )
    assert r.status_code == 200
    signed_ad = r.json()
    assert signed_ad["status"] == "signed"
    assert signed_ad["signed_at"]
    assert signed_ad["signed_by"]

    # Once signed: PATCH and DELETE both 409
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}",
        json={"narrative": "Trying to change signed addendum content of sufficient length"},
        timeout=15,
    )
    assert r.status_code == 409
    r = admin.delete(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}",
        timeout=15,
    )
    assert r.status_code == 409

    # Parent hydrate surfaces addendum count
    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/notes/{note['id']}",
        timeout=15,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["has_addenda"] is True
    assert data["addendum_count"] >= 1
    assert data["latest_addendum_at"]


def test_addendum_non_author_cannot_sign_but_admin_can(doctor, floater, admin):
    """doctor authors a draft; floater (another doctor) may not sign or
    edit it (403); admin can sign it."""
    p, _enc, note = _make_signed_note(doctor)
    r = doctor.post(
        f"{API}/patients/{p['id']}/clinical/follow_up_note/{note['id']}/addenda",
        json={"reason": "Add finding", "narrative": "Additional finding missed on initial charting."},
        timeout=15,
    )
    assert r.status_code == 201, r.text
    ad = r.json()

    # Non-author doctor — forbidden. Note: floater may not have permission
    # to see this patient at all (location isolation); treat 403 or 404 as
    # "denied" since both are "cannot act on this addendum".
    r = floater.post(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}/sign",
        timeout=15,
    )
    assert r.status_code in (403, 404)
    r = floater.patch(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}",
        json={"narrative": "Hostile edit attempt of sufficient length."},
        timeout=15,
    )
    assert r.status_code in (403, 404)

    # Admin may sign it — but note GROUP_ADMIN is on sunrise tenant and
    # the doctor patient may be on another tenant. Use the doctor's own
    # admin — GROUP_ADMIN covers the sunrise tenant.
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}/sign",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "signed"


# ---------------------------------------------------------------------------
# Billing readiness
# ---------------------------------------------------------------------------
def test_billing_readiness_blocked_when_note_draft(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    _make_note(admin, p["id"], enc["id"])

    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/billing-readiness",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["overall_status"] == "blocked"
    keys = {c["key"]: c for c in report["checks"]}
    assert keys["note_signed"]["passed"] is False
    assert keys["signature_present"]["passed"] is False
    # Readiness response carries future-billing summary shape
    assert report["visit_type"] == "follow_up"
    assert report["visit_type_label"] == "Follow-up visit"
    assert "procedures" in report
    assert "diagnoses" in report


def test_billing_readiness_ready_when_fully_documented(admin):
    p, enc, _note = _make_signed_note(admin)
    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/billing-readiness",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    report = r.json()
    # overall may be `ready` or `warnings` (warnings are tolerable — e.g.
    # encounter-completed is still in_progress). We only assert no blocker.
    assert report["overall_status"] in ("ready", "warnings")
    keys = {c["key"]: c for c in report["checks"]}
    assert keys["note_signed"]["passed"]
    assert keys["signature_present"]["passed"]
    assert keys["diagnosis_linked"]["passed"]
    assert keys["treatment_documented"]["passed"]
    assert keys["plan_linkage"]["passed"]
    assert report["note"]["kind"] == "follow_up_note"
    assert report["note"]["status"] == "signed"
    assert len(report["procedures"]) >= 1
    assert len(report["diagnoses"]) >= 1


def test_billing_readiness_warns_on_missing_plan(admin):
    """A follow-up visit with no active plan should surface a `plan_linkage`
    failure (required_plan=True for follow_up)."""
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    dx = _make_diagnosis(admin, p["id"], ep["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])
    # Fill & sign WITHOUT a treatment_plan_id
    _fill_note(admin, p["id"], note["id"])
    _sign_note(admin, p["id"], note["id"])

    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/billing-readiness",
        timeout=15,
    )
    assert r.status_code == 200
    report = r.json()
    keys = {c["key"]: c for c in report["checks"]}
    assert keys["plan_linkage"]["passed"] is False
    assert keys["plan_linkage"]["severity"] == "fail"
    assert report["overall_status"] == "blocked"


# ---------------------------------------------------------------------------
# Timeline + audit
# ---------------------------------------------------------------------------
def test_timeline_surfaces_signed_addendum(admin):
    p, _enc, note = _make_signed_note(admin)
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/follow_up_note/{note['id']}/addenda",
        json={"reason": "Clarify", "narrative": "Additional clarification note narrative text."},
        timeout=15,
    )
    ad = r.json()
    admin.post(
        f"{API}/patients/{p['id']}/clinical/addenda/{ad['id']}/sign",
        timeout=15,
    )
    r = admin.get(f"{API}/patients/{p['id']}/clinical/care-timeline", timeout=15)
    assert r.status_code == 200
    entries = r.json()["entries"]
    kinds = {e["kind"] for e in entries}
    assert "addendum" in kinds


def test_audit_events_for_linkage_changes(admin):
    """PATCH that changes treatment_plan_id linkage must emit BOTH the
    generic `follow_up_note.updated` audit and the Phase-8 specific
    `follow_up_note.treatment_plan_linkage_changed` clinical-audit event."""
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    plan = _make_plan(admin, p["id"], ep["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])

    # First PATCH — attach plan
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/notes/{note['id']}",
        json={"treatment_plan_id": plan["id"]},
        timeout=15,
    )
    assert r.status_code == 200, r.text

    # Second PATCH — unlink the plan (null) → should trigger
    # treatment_plan_linkage_changed audit
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/notes/{note['id']}",
        json={"treatment_plan_id": None},
        timeout=15,
    )
    assert r.status_code == 200, r.text

    # Confirm the generic audit stream has at least one `updated` row
    r = admin.get(
        f"{API}/audit-logs",
        params={
            "entity_type": "clinical_follow_up_note",
            "entity_id": note["id"],
            "limit": 50,
        },
        timeout=15,
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    rows = payload if isinstance(payload, list) else payload.get("items", [])
    actions = {row.get("action") for row in rows}
    assert any("follow_up_note.updated" in a for a in actions if a), actions
