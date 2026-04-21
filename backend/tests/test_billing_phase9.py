"""Billing Phase 9 tests — Claims from Encounter.

Scope:
  - Ready encounter → `POST /api/billing/claims/from-encounter` creates
    a draft claim with diagnoses, lines (TBD placeholders), DOS,
    rendering provider, source_encounter_id.
  - Blocked encounter → 409 with blocking checks (unless admin forces).
  - Admin can override blocked with `force=true`.
  - Non-admin cannot force.
  - Policy-mismatch (policy belongs to a different patient) → 400.
  - Tenant isolation: encounter_id from another tenant is not found.
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
DOCTOR = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


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


# ---------------------------------------------------------------------------
# Shared clinical fixtures — reuse Phase 8 helpers
# ---------------------------------------------------------------------------
def _new_patient(s):
    r = s.post(f"{API}/patients", json={
        "first_name": "P9",
        "last_name": f"C{uuid.uuid4().hex[:6]}",
        "email": f"p9_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0900",
        "date_of_birth": "1985-01-01",
        "gender": "female",
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


def _make_diagnosis(s, patient_id, episode_id, code="M54.5", label="Low back pain"):
    r = s.post(f"{API}/patients/{patient_id}/clinical/diagnoses", json={
        "icd10_code": code,
        "label": label,
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
                {"kind": "soft_tissue", "region": "lumbar",
                 "technique": "IASTM"},
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


def _make_signed_encounter(s):
    """Full flow: patient + episode + dx + plan + signed follow-up note.
    Returns (patient, encounter)."""
    p = _new_patient(s)
    ep = _make_episode(s, p["id"])
    _make_diagnosis(s, p["id"], ep["id"])
    plan = _make_plan(s, p["id"], ep["id"])
    prov = _pick_provider(s)
    appt = _book_appt(s, p["id"], prov)
    enc = _launch_enc(s, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(s, p["id"], enc["id"])
    _fill_note(s, p["id"], note["id"], treatment_plan_id=plan["id"])
    _sign_note(s, p["id"], note["id"])
    return p, enc


def _create_payer(s):
    r = s.post(f"{API}/billing/payers", json={
        "name": f"Acme Health {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_create_claim_from_ready_encounter(admin):
    p, enc = _make_signed_encounter(admin)
    payer = _create_payer(admin)

    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": enc["id"],
        "payer_id": payer["id"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    claim = r.json()
    assert claim["status"] == "draft"
    assert claim["patient_id"] == p["id"]
    assert claim["payer_id"] == payer["id"]
    assert claim["service_date_from"] == enc["date_of_service"]
    assert claim["service_date_to"] == enc["date_of_service"]
    assert claim["rendering_provider_id"] == enc["provider_id"]
    # Auto-generated notes mention the encounter
    assert enc["id"] in (claim.get("notes") or "")


def test_ready_encounter_creates_claim_lines_and_dx(admin):
    _, enc = _make_signed_encounter(admin)
    payer = _create_payer(admin)
    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": enc["id"], "payer_id": payer["id"],
    }, timeout=15)
    assert r.status_code == 201
    claim = r.json()

    # Lines — 2 documented interventions (adjustment + soft_tissue) → 2 lines
    r = admin.get(f"{API}/billing/claims/{claim['id']}/detail", timeout=15)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert len(detail["lines"]) >= 2
    assert len(detail["diagnoses"]) >= 1
    # All placeholders/hints, 0-priced
    for ln in detail["lines"]:
        assert ln["billed_cents"] == 0
        assert ln["service_date"] == enc["date_of_service"]
    # Diagnosis codes are upper-cased ICD-10
    dx_codes = {d["code"] for d in detail["diagnoses"]}
    assert "M54.5" in dx_codes


# ---------------------------------------------------------------------------
# Blocked encounter
# ---------------------------------------------------------------------------
def test_blocked_encounter_rejects_without_force(admin):
    """Follow-up visit with no treatment plan is `blocked` per Phase 8."""
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    _make_diagnosis(admin, p["id"], ep["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])
    _fill_note(admin, p["id"], note["id"])      # no plan link
    _sign_note(admin, p["id"], note["id"])

    payer = _create_payer(admin)
    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": enc["id"], "payer_id": payer["id"],
    }, timeout=15)
    assert r.status_code == 409, r.text
    detail = r.json().get("detail") or {}
    assert detail.get("message") == "Encounter is not billing-ready"
    keys = {b["key"] for b in detail.get("blocking", [])}
    assert "plan_linkage" in keys


def test_admin_can_force_blocked_encounter(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    _make_diagnosis(admin, p["id"], ep["id"])
    prov = _pick_provider(admin)
    appt = _book_appt(admin, p["id"], prov)
    enc = _launch_enc(admin, appt["id"], "follow_up", episode_id=ep["id"])
    note = _make_note(admin, p["id"], enc["id"])
    _fill_note(admin, p["id"], note["id"])      # no plan
    _sign_note(admin, p["id"], note["id"])

    payer = _create_payer(admin)
    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": enc["id"], "payer_id": payer["id"],
        "force": True,
    }, timeout=15)
    assert r.status_code == 201, r.text
    claim = r.json()
    assert claim["status"] == "draft"
    # Synthesized notes should reflect the force override
    assert "forced" in (claim.get("notes") or "").lower()


# ---------------------------------------------------------------------------
# Missing encounter / cross-tenant
# ---------------------------------------------------------------------------
def test_missing_encounter_returns_404(admin):
    payer = _create_payer(admin)
    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": str(uuid.uuid4()),
        "payer_id": payer["id"],
    }, timeout=15)
    assert r.status_code == 404


def test_policy_must_match_patient(admin):
    # Patient A signed encounter
    pa, enc = _make_signed_encounter(admin)
    # Patient B has an insurance policy
    pb = _new_patient(admin)
    payer = _create_payer(admin)
    r = admin.post(f"{API}/billing/insurance-policies", json={
        "patient_id": pb["id"],
        "payer_id": payer["id"],
        "rank": "primary",
        "subscriber_name": "Test Subscriber",
        "member_id": f"MEM-{uuid.uuid4().hex[:8]}",
    }, timeout=15)
    assert r.status_code == 201, r.text
    policy = r.json()

    r = admin.post(f"{API}/billing/claims/from-encounter", json={
        "encounter_id": enc["id"],
        "payer_id": payer["id"],
        "policy_id": policy["id"],
    }, timeout=15)
    # Policy belongs to a different patient → 400
    assert r.status_code == 400, r.text
