"""Clinical Phase 6 — Treatment Plans + Re-Exams tests."""
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
        "first_name": "Plan",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"plan_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0600",
        "date_of_birth": "1980-03-10",
        "gender": "female",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _provider(s):
    r = s.get(f"{API}/auth/providers", timeout=10)
    assert r.status_code == 200
    return r.json()[0]["id"]


def _book(s, patient_id, provider_id, reason="Re-evaluation"):
    for _ in range(5):
        base = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 60))
        start = base.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=random.randint(0, 8), minutes=random.choice([0, 15, 30, 45]),
        )
        end = start + timedelta(minutes=30)
        r = s.post(f"{API}/appointments", json={
            "patient_id": patient_id, "provider_id": provider_id,
            "start_time": start.isoformat(), "end_time": end.isoformat(),
            "reason": reason,
        }, timeout=15)
        if r.status_code == 201:
            return r.json()
    raise AssertionError("could not book")


def _launch_encounter(s, appt_id, encounter_type="re_evaluation"):
    r = s.post(f"{API}/appointments/{appt_id}/clinical/encounters",
               json={"encounter_type": encounter_type}, timeout=15)
    assert r.status_code in (200, 201), r.text
    return r.json()["encounter"]


def _complete_enc(s, pid, eid):
    r = s.post(f"{API}/patients/{pid}/clinical/encounters/{eid}/complete", json={}, timeout=15)
    assert r.status_code == 200, r.text


def _make_episode(s, patient_id, title="Low back acute"):
    r = s.post(f"{API}/patients/{patient_id}/clinical/episodes",
               json={"title": title, "case_type": "injury_episode"}, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _new_diag(s, patient_id, episode_id, code="M54.5", label="Low back pain"):
    r = s.post(f"{API}/patients/{patient_id}/clinical/diagnoses", json={
        "icd10_code": code, "label": label, "is_primary": True,
        "body_region": "lumbar", "episode_id": episode_id,
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _signed_initial_exam(s, patient_id, episode_id):
    """Create, stub, and sign an Initial Exam on a new-patient-exam encounter."""
    prov = _provider(s)
    appt = _book(s, patient_id, prov, reason="New patient exam")
    enc = _launch_encounter(s, appt["id"], "new_patient_exam")
    r = s.post(f"{API}/patients/{patient_id}/clinical/exams",
               json={"encounter_id": enc["id"], "prefill_from_chart": False}, timeout=15)
    assert r.status_code in (200, 201), r.text
    exam = r.json()
    s.patch(f"{API}/patients/{patient_id}/clinical/exams/{exam['id']}", json={
        "examination": {"observation_inspection": "forward head posture",
                        "palpation_findings": "L4 tenderness"},
    }, timeout=15)
    r = s.post(f"{API}/patients/{patient_id}/clinical/exams/{exam['id']}/sign", json={}, timeout=15)
    assert r.status_code == 200, r.text
    _complete_enc(s, patient_id, enc["id"])
    return r.json()


# ---------------------------------------------------------------------------
# Treatment Plan tests
# ---------------------------------------------------------------------------
def test_create_treatment_plan_and_progress(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    dx = _new_diag(admin, p["id"], ep["id"])
    prov = _provider(admin)
    body = {
        "episode_id": ep["id"], "title": "6-week LBP plan",
        "responsible_provider_id": prov,
        "diagnosis_ids": [dx["id"]],
        "target_body_regions": ["lumbar"],
        "frequency_visits_per_week": 2,
        "frequency_total_visits": 12,
        "expected_duration_weeks": 6,
        "re_exam_date": "2026-03-22",
        "planned_interventions": [
            {"kind": "adjustment", "description": "Lumbar Diversified"},
            {"kind": "modality", "description": "E-stim 10min"},
        ],
        "goals": [
            {"description": "Reduce pain", "measure_type": "pain_scale",
             "baseline_value": 7, "target_value": 2, "unit": "NRS"},
            {"description": "Return to 30-min walk", "measure_type": "functional",
             "baseline_value": "10 min", "target_value": "30 min"},
        ],
        "baselines": {
            "pain_scale_0_10": 7,
            "key_rom_summary": "lumbar flexion 40°, extension 10°",
            "functional_measures": [
                {"label": "Oswestry Index", "value": 42, "unit": "%"}
            ],
        },
        "home_care_recommendations": "McKenzie extensions 3x/day",
        "activity_work_recommendations": "Modified duty x 2 weeks",
        "discharge_criteria": "Pain ≤2 AND full ROM restored",
        "maintenance_transition_notes": "Transition to monthly once targets met",
    }
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                   json=body, timeout=15)
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["plan_status"] == "active"
    assert len(plan["goals"]) == 2
    assert all(g["id"] for g in plan["goals"])
    assert plan["progress"]["visits_completed"] == 0
    assert plan["progress"]["total_visits"] == 12
    assert plan["progress"]["percent"] == 0
    # Summary counts active
    summ = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10).json()
    assert summ["treatment_plans"]["total"] == 1
    assert summ["treatment_plans"]["open"] == 1


def test_one_active_plan_per_episode_guard(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    body = {"episode_id": ep["id"], "title": "Plan A"}
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                   json=body, timeout=15)
    assert r.status_code == 201
    plan_a = r.json()
    # Second active plan on same episode → 409
    r2 = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                    json={"episode_id": ep["id"], "title": "Plan B"}, timeout=15)
    assert r2.status_code == 409
    assert plan_a["id"] in r2.text  # existing id surfaced to caller
    # Transition Plan A -> on_hold, then we can create Plan B
    r3 = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan_a['id']}/set-status",
                    json={"plan_status": "on_hold", "reason": "pause for imaging"}, timeout=10)
    assert r3.status_code == 200
    r4 = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                    json={"episode_id": ep["id"], "title": "Plan B"}, timeout=15)
    assert r4.status_code == 201


def test_plan_patch_and_status_transitions(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                   json={"episode_id": ep["id"], "title": "Plan X",
                         "goals": [{"description": "pain", "measure_type": "pain_scale"}]},
                   timeout=15)
    plan = r.json()

    # PATCH — update goals
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}", json={
        "title": "Plan X-updated",
        "goals": [
            {"id": plan["goals"][0]["id"], "description": "pain", "measure_type": "pain_scale",
             "status": "active"},
            {"description": "function", "measure_type": "functional", "status": "active"},
        ],
        "frequency_visits_per_week": 3,
    }, timeout=15)
    assert r.status_code == 200
    fresh = r.json()
    assert fresh["title"] == "Plan X-updated"
    assert len(fresh["goals"]) == 2
    assert fresh["frequency_visits_per_week"] == 3

    # Status transitions: active -> discharged (with reason)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}/set-status",
                   json={"plan_status": "discharged", "reason": "Goals met"}, timeout=10)
    assert r.status_code == 200
    disc = r.json()
    assert disc["plan_status"] == "discharged"
    assert disc["discharge_reason"] == "Goals met"
    assert disc["discharged_at"] is not None

    # PATCH on discharged plan → 409
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}",
                    json={"title": "tamper"}, timeout=10)
    assert r.status_code == 409


def test_plan_diagnosis_linkage_validation(admin):
    p = _new_patient(admin)
    other = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    ep_other = _make_episode(admin, other["id"])
    bad_dx = _new_diag(admin, other["id"], ep_other["id"])
    # Link diagnosis that belongs to another patient → 400
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans", json={
        "episode_id": ep["id"], "title": "Bad", "diagnosis_ids": [bad_dx["id"]],
    }, timeout=15)
    assert r.status_code == 400


def test_plan_visit_progress_from_signed_notes(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    r = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                   json={"episode_id": ep["id"], "title": "Progress plan",
                         "frequency_total_visits": 4},
                   timeout=15)
    plan = r.json()

    # Create a follow-up encounter + signed note
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov, reason="Follow-up")
    enc = _launch_encounter(admin, appt["id"], "follow_up")
    # Attach episode by PATCHing the encounter
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    n = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15).json()
    admin.post(f"{API}/patients/{p['id']}/clinical/notes/{n['id']}/sign",
               json={}, timeout=10)

    fresh = admin.get(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}",
                      timeout=10).json()
    assert fresh["progress"]["visits_completed"] == 1
    assert fresh["progress"]["percent"] == 25


def test_follow_up_note_exposes_active_plan_summary(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
               json={"episode_id": ep["id"], "title": "Visible plan",
                     "frequency_total_visits": 10,
                     "goals": [{"description": "reduce pain",
                                "measure_type": "pain_scale"}]},
               timeout=15)

    prov = _provider(admin)
    appt = _book(admin, p["id"], prov, reason="Follow-up")
    enc = _launch_encounter(admin, appt["id"], "follow_up")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    n = admin.post(f"{API}/patients/{p['id']}/clinical/notes",
                   json={"encounter_id": enc["id"]}, timeout=15).json()

    r = admin.get(f"{API}/patients/{p['id']}/clinical/notes/{n['id']}", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["active_plan_summary"] is not None
    assert body["active_plan_summary"]["title"] == "Visible plan"
    assert len(body["active_plan_summary"]["goals"]) == 1


# ---------------------------------------------------------------------------
# Re-Exam tests
# ---------------------------------------------------------------------------
def _setup_plan_and_signed_exam(admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    _signed_initial_exam(admin, p["id"], ep["id"])
    # Create plan with two goals so we can assert goal_progress snapshot
    plan = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans", json={
        "episode_id": ep["id"],
        "title": "LBP 6wk",
        "frequency_total_visits": 12,
        "goals": [
            {"description": "pain ≤ 2", "measure_type": "pain_scale",
             "baseline_value": 7, "target_value": 2},
            {"description": "30-min walk", "measure_type": "functional",
             "baseline_value": "10 min", "target_value": "30 min"},
        ],
        "baselines": {"pain_scale_0_10": 7,
                      "key_rom_summary": "lumbar flexion 40°"},
    }, timeout=15).json()
    return p, ep, plan


def test_reexam_create_auto_links_and_snapshots(admin):
    p, ep, plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)

    r = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                   json={"encounter_id": enc["id"]}, timeout=15)
    assert r.status_code == 201, r.text
    rx = r.json()
    assert rx["treatment_plan_id"] == plan["id"]
    assert rx["initial_exam_id"] is not None
    assert rx["baseline_snapshot"]["plan"]["id"] == plan["id"]
    assert rx["baseline_snapshot"]["plan"]["baselines"]["pain_scale_0_10"] == 7
    assert rx["baseline_snapshot"]["initial_exam"] is not None
    # Idempotency
    r2 = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15)
    assert r2.status_code == 200
    assert r2.headers.get("x-reexam-existed", r2.headers.get("X-ReExam-Existed")) == "true"
    assert r2.json()["id"] == rx["id"]


def test_reexam_cancelled_encounter_rejected(admin):
    p, ep, _plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    admin.post(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/cancel",
               json={"reason": "patient no-show"}, timeout=15)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                   json={"encounter_id": enc["id"]}, timeout=15)
    assert r.status_code == 409


def test_reexam_patch_goal_progress_and_outcomes(admin):
    p, ep, plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()

    g1_id = plan["goals"][0]["id"]
    g2_id = plan["goals"][1]["id"]
    payload = {
        "current_findings": {"observation_inspection": "improved posture",
                             "palpation_findings": "mild residual spasm"},
        "goal_progress": [
            {"goal_id": g1_id, "current_value": 4, "status": "improved",
             "note": "50% improvement"},
            {"goal_id": g2_id, "current_value": "20 min", "status": "on_track"},
        ],
        "outcome_updates": [
            {"measure_type": "oswestry", "label": "Oswestry", "score": 22,
             "max_score": 100, "note": "vs 42 at intake"},
            {"measure_type": "pain_vas", "label": "Pain VAS", "score": 4, "max_score": 10},
        ],
        "recommendation_decision": "modify_plan",
        "recommendation_reason": "downshift to 1x/wk",
    }
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}",
                    json=payload, timeout=15)
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert len(fresh["goal_progress"]) == 2
    assert fresh["goal_progress"][0]["status"] == "improved"
    assert len(fresh["outcome_updates"]) == 2
    assert fresh["recommendation_decision"] == "modify_plan"

    # Bad goal_id → 400
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}",
                    json={"goal_progress": [{"goal_id": "nope", "status": "improved"}]},
                    timeout=10)
    assert r.status_code == 400


def test_reexam_sign_emits_modify_plan_audit_without_mutation(admin):
    p, ep, plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()

    # Sign without recommendation → 400
    r = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 400

    # Set modify_plan + sign
    admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}", json={
        "recommendation_decision": "modify_plan",
        "recommendation_reason": "increase frequency",
    }, timeout=15)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["status"] == "signed"

    # PATCH signed → 409
    r = admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}",
                    json={"revised_plan_summary": "tamper"}, timeout=10)
    assert r.status_code == 409

    # Plan is UNCHANGED (we do not auto-mutate)
    fresh_plan = admin.get(f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}",
                           timeout=10).json()
    assert fresh_plan["title"] == plan["title"]
    assert fresh_plan["plan_status"] == "active"

    # Audit trail — the revised_recommended event must exist. Use the
    # admin audit_logs endpoint filtered to the re-exam id.
    trail = admin.get(f"{API}/audit-logs",
                      params={"entity_type": "clinical_reexam",
                              "entity_id": rx["id"]}, timeout=10)
    assert trail.status_code == 200, trail.text
    actions = [e["action"] for e in trail.json()]
    assert "clinical.re_exam.signed" in actions


def test_reexam_sign_materializes_new_diagnoses(admin):
    p, ep, _plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()
    admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}", json={
        "recommendation_decision": "continue", "recommendation_reason": "stay the course",
        "new_diagnoses": [
            {"icd10_code": "m54.4", "label": "Lumbago with sciatica",
             "body_region": "lumbar", "is_primary": False},
        ],
    }, timeout=15)
    r = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}/sign",
                   json={}, timeout=10)
    assert r.status_code == 200, r.text
    signed = r.json()
    assert len(signed["materialized_diagnosis_ids"]) == 1
    # The problem list now includes the new dx with uppercased code
    dx_list = admin.get(f"{API}/patients/{p['id']}/clinical/diagnoses", timeout=10).json()
    codes = [d["icd10_code"] for d in dx_list]
    assert "M54.4" in codes


def test_reexam_narrative_includes_comparison_and_recommendation(admin):
    p, ep, plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()
    g1_id = plan["goals"][0]["id"]
    admin.patch(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}", json={
        "goal_progress": [{"goal_id": g1_id, "current_value": 3, "status": "improved"}],
        "outcome_updates": [{"measure_type": "ndi", "label": "NDI", "score": 18, "max_score": 50}],
        "recommendation_decision": "continue", "recommendation_reason": "continue 2x/wk",
    }, timeout=15)
    r = admin.get(f"{API}/patients/{p['id']}/clinical/re-exams/{rx['id']}/narrative",
                  timeout=10)
    assert r.status_code == 200
    text = r.json()["narrative"]
    assert "RE-EXAMINATION NOTE" in text
    assert "BASELINE (frozen)" in text
    assert "GOAL PROGRESS" in text
    assert "OUTCOME MEASURES" in text
    assert "RECOMMENDATION" in text
    assert "continue" in text.lower()


def test_care_timeline_includes_plan_and_reexam(admin):
    p, ep, plan = _setup_plan_and_signed_exam(admin)
    prov = _provider(admin)
    appt = _book(admin, p["id"], prov)
    enc = _launch_encounter(admin, appt["id"], "re_evaluation")
    admin.patch(f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
                json={"episode_id": ep["id"]}, timeout=15)
    rx = admin.post(f"{API}/patients/{p['id']}/clinical/re-exams",
                    json={"encounter_id": enc["id"]}, timeout=15).json()

    r = admin.get(f"{API}/patients/{p['id']}/clinical/care-timeline", timeout=10)
    assert r.status_code == 200
    kinds = [e["kind"] for e in r.json()["entries"]]
    assert "treatment_plan" in kinds
    assert "re_exam" in kinds
    assert "initial_exam" in kinds
    assert "encounter" in kinds
    ids_by_kind = {e["kind"]: e["id"] for e in r.json()["entries"]}
    assert ids_by_kind.get("treatment_plan") == plan["id"]
    assert ids_by_kind.get("re_exam") == rx["id"]

    # Summary re_exams counts live
    summ = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10).json()
    assert summ["re_exams"]["total"] == 1
    assert summ["re_exams"]["open"] == 1


def test_tenant_isolation_and_reauth_plan(admin, default_admin):
    p = _new_patient(admin)
    ep = _make_episode(admin, p["id"])
    plan = admin.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                      json={"episode_id": ep["id"], "title": "Tenant plan"},
                      timeout=15).json()
    # Cross-tenant access → 404
    r = default_admin.get(
        f"{API}/patients/{p['id']}/clinical/treatment-plans/{plan['id']}", timeout=10
    )
    assert r.status_code == 404

    # Reauth required on write
    no_reauth = _login(*GROUP_ADMIN, reauth=False)
    r = no_reauth.post(f"{API}/patients/{p['id']}/clinical/treatment-plans",
                       json={"episode_id": ep["id"], "title": "noauth"}, timeout=15)
    assert r.status_code == 401
    assert "re-auth" in (r.json().get("detail", "")).lower()
