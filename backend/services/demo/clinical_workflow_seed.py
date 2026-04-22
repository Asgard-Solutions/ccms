"""Riverbend clinical workflow seed — appointments → encounter → exam → notes.

The §4d chart seed landed episodes / diagnoses / treatment plans / history,
but the Clinical tab's **Appointment-launched encounters**, **Initial
Exams**, and **Follow-up & Daily Visit notes** panels were still empty
because the demo didn't walk the real workflow:

  appointment (past, completed)
    → clinical_encounter (launched from that appointment)
       → clinical_initial_exam (first visit) OR
         clinical_follow_up_note (every subsequent treatment visit)
          → linked to treatment_plan / episode / diagnoses

This seeder backfills, per persona, a short history of past completed
appointments (the ones the treatment plan was written against) plus
the encounter + exam + note chain anchored to each. Idempotent: every
row is keyed on a deterministic (patient, date_of_service) tuple so a
reseed re-populates exactly the same narrative.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from core.db import get_db_write
from services.demo.clinical_seed import CHART_BLUEPRINT

logger = logging.getLogger("ccms.demo.clinical_workflow_seed")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Past-visit calendar per persona. Each entry generates:
#   - 1 past appointment (status=completed)
#   - 1 encounter (status=completed) keyed to the appointment
#   - Either 1 initial_exam (first past visit) or 1 follow_up_note
# visit_offset_days is negative = days in the past; encounter_type matches
# the blueprint narrative.
# ---------------------------------------------------------------------------
PAST_VISITS: dict[tuple[str, str], list[dict]] = {
    ("Hannah", "Whitaker"): [
        {"offset": -21, "hour": 9, "duration": 60, "reason": "Acute lumbar strain — initial exam",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -18, "hour": 9, "duration": 30, "reason": "Lumbar adjustment + IASTM",
         "encounter_type": "treatment_visit"},
        {"offset": -14, "hour": 10, "duration": 30, "reason": "Lumbar adjustment + core cueing",
         "encounter_type": "treatment_visit"},
        {"offset": -7, "hour": 10, "duration": 30, "reason": "Lumbar adjustment, progress check",
         "encounter_type": "follow_up"},
    ],
    ("Marcus", "Reid"): [
        {"offset": -1, "hour": 9, "duration": 60, "reason": "Medicare initial exam (active treatment)",
         "encounter_type": "new_patient_exam", "is_initial": True, "appointment_id_key": "existing_completed"},
    ],
    ("Isabella", "Cho"): [
        {"offset": -20, "hour": 11, "duration": 60, "reason": "Post-MVA initial evaluation",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -17, "hour": 11, "duration": 30, "reason": "Cervical adjustment + e-stim",
         "encounter_type": "treatment_visit"},
        {"offset": -14, "hour": 11, "duration": 30, "reason": "Cervical adjustment + MFR",
         "encounter_type": "treatment_visit"},
        {"offset": -10, "hour": 11, "duration": 30, "reason": "Cervical + thoracic adjustments",
         "encounter_type": "treatment_visit"},
        {"offset": -7, "hour": 11, "duration": 30, "reason": "Progress check — 3-wk report",
         "encounter_type": "follow_up"},
    ],
    ("Derrick", "Stone"): [
        {"offset": -13, "hour": 14, "duration": 60, "reason": "WC initial evaluation",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -10, "hour": 14, "duration": 30, "reason": "Lumbar adjustment + core progression",
         "encounter_type": "treatment_visit"},
        {"offset": -6, "hour": 14, "duration": 30, "reason": "Lumbar adjustment + e-stim",
         "encounter_type": "treatment_visit"},
        {"offset": -2, "hour": 14, "duration": 30, "reason": "WC progress — return-to-work check",
         "encounter_type": "follow_up"},
    ],
    ("Aria", "Johnson"): [
        {"offset": -42, "hour": 8, "duration": 60, "reason": "Shoulder initial evaluation",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -35, "hour": 8, "duration": 30, "reason": "Shoulder mobilization + IASTM",
         "encounter_type": "treatment_visit"},
        {"offset": -28, "hour": 8, "duration": 30, "reason": "RC progression + ultrasound",
         "encounter_type": "treatment_visit"},
        {"offset": -21, "hour": 8, "duration": 30, "reason": "Shoulder ROM recheck",
         "encounter_type": "follow_up"},
        {"offset": -14, "hour": 8, "duration": 30, "reason": "RC progression",
         "encounter_type": "treatment_visit"},
    ],
    ("Claire", "Morgan"): [
        {"offset": -120, "hour": 14, "duration": 60, "reason": "Lumbar initial evaluation",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -90, "hour": 14, "duration": 30, "reason": "Lumbar adjustment (wk 5)",
         "encounter_type": "treatment_visit"},
        {"offset": -60, "hour": 14, "duration": 30, "reason": "Lumbar adjustment (wk 9)",
         "encounter_type": "treatment_visit"},
        {"offset": -25, "hour": 14, "duration": 45, "reason": "Final re-exam — goals met, discharge",
         "encounter_type": "re_evaluation"},
    ],
    ("Jaxon", "Morgan"): [
        {"offset": -10, "hour": 17, "duration": 60, "reason": "Pediatric initial — postural check",
         "encounter_type": "new_patient_exam", "is_initial": True},
        {"offset": -3, "hour": 17, "duration": 15, "reason": "Pediatric follow-up — posture coaching",
         "encounter_type": "follow_up"},
    ],
    ("Ethan", "Parker"): [
        {"offset": -2, "hour": 17, "duration": 15, "reason": "Maintenance adjustment",
         "encounter_type": "follow_up", "appointment_id_key": "existing_completed"},
    ],
}


async def _upsert_past_appointment(
    db, tenant_id: str, location_id: str | None,
    patient_id: str, provider_id: str | None,
    spec: dict,
) -> str | None:
    """Return the appointment id, creating a new completed appointment
    if one doesn't already exist for this persona+offset. For personas
    where the existing seeded schedule already has a completed visit
    (Marcus, Ethan) we look up that appointment instead."""
    start_dt = datetime.now(timezone.utc) + timedelta(
        days=spec["offset"], hours=spec["hour"] - datetime.now(timezone.utc).hour,
    )
    start_dt = start_dt.replace(hour=spec["hour"], minute=0, second=0, microsecond=0)
    end_dt = start_dt + timedelta(minutes=spec["duration"])

    if spec.get("appointment_id_key") == "existing_completed":
        existing = await db.appointments.find_one(
            {"tenant_id": tenant_id, "patient_id": patient_id,
             "status": "completed"},
            {"_id": 0, "id": 1}, sort=[("start_time", -1)],
        )
        return existing["id"] if existing else None

    key = {
        "tenant_id": tenant_id, "patient_id": patient_id,
        "start_time": _iso(start_dt), "reason": spec["reason"],
    }
    existing = await db.appointments.find_one(key, {"_id": 0, "id": 1})
    aid = existing["id"] if existing else str(uuid.uuid4())
    doc = {
        **key, "id": aid,
        "location_id": location_id,
        "provider_id": provider_id,
        "end_time": _iso(end_dt),
        "duration_minutes": spec["duration"],
        "status": "completed",
        "source": "demo_seed",
        "updated_at": _now(),
    }
    if existing:
        await db.appointments.update_one({"id": aid}, {"$set": doc})
    else:
        doc["created_at"] = _now()
        await db.appointments.insert_one(doc)
    return aid


async def _upsert_encounter(
    db, tenant_id: str, location_id: str | None,
    patient_id: str, provider_id: str | None,
    appointment_id: str, episode_id: str | None,
    encounter_type: str, start_dt: datetime, end_dt: datetime,
    appointment_reason: str, appointment_status: str,
) -> str:
    """Idempotent on (tenant, appointment_id) where status != cancelled."""
    key = {
        "tenant_id": tenant_id, "appointment_id": appointment_id,
        "status": {"$ne": "cancelled"},
    }
    existing = await db.clinical_encounters.find_one(
        key, {"_id": 0, "id": 1},
    )
    eid = existing["id"] if existing else str(uuid.uuid4())
    snapshot = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "provider_id": provider_id,
        "location_id": location_id,
        "start_time": _iso(start_dt),
        "end_time": _iso(end_dt),
        "status": appointment_status,
        "reason": appointment_reason,
    }
    doc = {
        "tenant_id": tenant_id,
        "appointment_id": appointment_id,
        "id": eid,
        "location_id": location_id,
        "patient_id": patient_id,
        "provider_id": provider_id,
        "episode_id": episode_id,
        "encounter_type": encounter_type,
        "status": "completed",
        "date_of_service": _iso(start_dt),
        "scheduled_start": _iso(start_dt),
        "scheduled_end": _iso(end_dt),
        "scheduled_duration_min": int((end_dt - start_dt).total_seconds() // 60),
        "actual_start": _iso(start_dt),
        "actual_end": _iso(end_dt),
        "appointment_snapshot": snapshot,
        "appointment_status_at_launch": appointment_status,
        "is_exception": False,
        "exception_reason": None,
        "notes": None,
        "completed_at": _iso(end_dt + timedelta(minutes=5)),
        "completed_by": provider_id,
        "updated_at": _now(),
        "updated_by": provider_id,
    }
    if existing:
        await db.clinical_encounters.update_one({"id": eid}, {"$set": doc})
    else:
        doc["created_at"] = _now()
        doc["created_by"] = provider_id
        await db.clinical_encounters.insert_one(doc)
    return eid


async def _upsert_initial_exam(
    db, tenant_id: str, location_id: str | None,
    encounter_id: str, patient_id: str, appointment_id: str,
    provider_id: str | None, episode_id: str | None,
    date_of_service: str, diagnosis_ids: list[str],
    blueprint_history: dict, blueprint_plan: dict,
) -> None:
    """One initial exam per (tenant, encounter_id). Stores the same
    intake narrative the chart seed already anchors on the patient doc,
    plus a minimal examination / assessment block."""
    key = {"tenant_id": tenant_id, "encounter_id": encounter_id}
    existing = await db.clinical_initial_exams.find_one(key, {"_id": 0, "id": 1})
    xid = existing["id"] if existing else str(uuid.uuid4())
    examination = {
        "vitals": {"bp_systolic": 122, "bp_diastolic": 78, "pulse": 72},
        "regions": [
            {"body_region": r, "rom_summary": "Restricted", "findings": "See initial exam notes."}
            for r in (blueprint_plan.get("target_body_regions") or ["lumbar"])
        ],
        "global_notes": (blueprint_plan.get("baselines") or {}).get("key_rom_summary"),
    }
    assessment = {
        "clinical_impression": blueprint_history.get("history_of_present_illness"),
        "prognosis": "Good with compliance.",
        "plan_summary": blueprint_plan.get("title"),
    }
    doc = {
        **key, "id": xid,
        "location_id": location_id,
        "patient_id": patient_id,
        "appointment_id": appointment_id,
        "provider_id": provider_id,
        "episode_id": episode_id,
        "date_of_service": date_of_service,
        "status": "signed",
        "template_id": None,
        "template_snapshot": None,
        "history": {
            "chief_complaint": blueprint_history.get("chief_complaint"),
            "history_of_present_illness": blueprint_history.get("history_of_present_illness"),
            "onset_date": blueprint_history.get("onset_date"),
            "mechanism_of_injury": blueprint_history.get("mechanism_of_injury"),
            "severity": blueprint_history.get("severity"),
            "aggravating_factors": blueprint_history.get("aggravating_factors") or [],
            "relieving_factors": blueprint_history.get("relieving_factors") or [],
            "prior_treatment": blueprint_history.get("prior_treatment"),
        },
        "examination": examination,
        "assessment": assessment,
        "diagnosis_ids": diagnosis_ids,
        "new_diagnoses": [],
        "materialized_diagnosis_ids": diagnosis_ids,
        "prefilled_from_chart_at": _now(),
        "marked_sign_ready_at": _now(),
        "marked_sign_ready_by": provider_id,
        "signed_at": _now(),
        "signed_by": provider_id,
        "signed_by_name": None,
        "updated_at": _now(),
        "updated_by": provider_id,
    }
    if existing:
        await db.clinical_initial_exams.update_one({"id": xid}, {"$set": doc})
    else:
        doc["created_at"] = _now()
        doc["created_by"] = provider_id
        await db.clinical_initial_exams.insert_one(doc)


async def _upsert_follow_up_note(
    db, tenant_id: str, location_id: str | None,
    encounter_id: str, patient_id: str, appointment_id: str,
    provider_id: str | None, episode_id: str | None,
    treatment_plan_id: str | None,
    date_of_service: str, visit_number: int,
    blueprint_plan: dict,
) -> None:
    """One note per encounter. Structured SOAP, signed."""
    key = {"tenant_id": tenant_id, "encounter_id": encounter_id}
    existing = await db.clinical_follow_up_notes.find_one(
        key, {"_id": 0, "id": 1},
    )
    nid = existing["id"] if existing else str(uuid.uuid4())
    baseline_pain = (blueprint_plan.get("baselines") or {}).get("pain_scale_0_10") or 5
    # Linear de-escalation from baseline down to 2 across the visit
    # series so reviewers see the expected progress trajectory.
    progress_pain = max(2, baseline_pain - min(visit_number, 4))
    regions = blueprint_plan.get("target_body_regions") or ["lumbar"]
    region_findings = [
        {"body_region": r, "palpation": "Mild hypertonicity",
         "rom_summary": "Improving", "notes": None}
        for r in regions[:2]
    ]
    treatments = [
        t for t in (blueprint_plan.get("planned_interventions") or [])
        if t.get("kind") in ("adjustment", "modality", "soft_tissue", "exercise")
    ][:3]
    treatment_rendered = []
    for t in treatments:
        kind = t["kind"]
        entry = {
            "kind": kind,
            "description": t.get("description"),
            "segments": [],
            "technique": None,
            "modality": None,
            "region": None,
            "duration_min": None,
            "notes": None,
        }
        if kind == "adjustment":
            entry["technique"] = "Diversified"
            entry["segments"] = [r[:6].upper() for r in regions[:2]]
        elif kind == "modality":
            entry["modality"] = "e-stim" if "e-stim" in (t.get("description") or "") else "ultrasound"
            entry["region"] = regions[0] if regions else None
            entry["duration_min"] = 10
        elif kind == "soft_tissue":
            entry["region"] = regions[0] if regions else None
            entry["duration_min"] = 8
        treatment_rendered.append(entry)

    doc = {
        **key, "id": nid,
        "location_id": location_id,
        "patient_id": patient_id,
        "appointment_id": appointment_id,
        "provider_id": provider_id,
        "episode_id": episode_id,
        "treatment_plan_id": treatment_plan_id,
        "date_of_service": date_of_service,
        "status": "signed",
        "visit_number": visit_number,
        "subjective": {
            "interval_history": (
                f"Patient reports {'steady' if visit_number > 1 else 'initial'} "
                f"improvement. Pain now {progress_pain}/10, down from "
                f"{baseline_pain}/10 at baseline. HEP adherence good."
            ),
            "pain_scale_0_10": progress_pain,
            "pain_change": "better" if progress_pain < baseline_pain else "same",
            "functional_change": "Tolerating desk work with fewer breaks.",
            "adherence_home_care": "yes",
            "adherence_notes": None,
        },
        "objective": {
            "region_findings": region_findings,
            "reassessment_summary": (
                "Measured ROM and segmental motion improved since last visit; "
                "muscle guarding reduced."
            ),
            "vitals": None,
        },
        "assessment": {
            "response_to_care": "improving",
            "clinical_impression": (
                f"Progress consistent with the active treatment plan. "
                f"Continue current frequency; target next phase at visit "
                f"{visit_number + 3}."
            ),
        },
        "plan": {
            "treatment_rendered": treatment_rendered,
            "regions_treated": regions[:2],
            "home_care_reinforcement": blueprint_plan.get("home_care_recommendations"),
            "next_visit_plan": (
                "Continue current plan; reassess pain + ROM next visit."
            ),
            "recommended_interval_days": 3 if visit_number < 4 else 7,
        },
        "copied_from_note_id": None,
        "copied_fields": [],
        "marked_sign_ready_at": _now(),
        "marked_sign_ready_by": provider_id,
        "signed_at": _now(),
        "signed_by": provider_id,
        "updated_at": _now(),
        "updated_by": provider_id,
    }
    if existing:
        await db.clinical_follow_up_notes.update_one({"id": nid}, {"$set": doc})
    else:
        doc["created_at"] = _now()
        doc["created_by"] = provider_id
        await db.clinical_follow_up_notes.insert_one(doc)


# ---------------------------------------------------------------------------
async def seed_demo_clinical_workflow(
    tenant_id: str, location_id: str | None,
    lead_doc_id: str | None,
) -> None:
    """Walk every persona's PAST_VISITS, creating the full
    appointment → encounter → (initial_exam | follow_up_note) chain.
    Prerequisite: `seed_demo_clinical_charts` has already run so
    episodes + diagnoses + treatment_plans exist."""
    db = get_db_write()

    # Load canonical patient + episode + diagnosis + plan maps once.
    pt_by_name: dict[tuple[str, str], str] = {}
    async for p in db.patients.find(
        {"tenant_id": tenant_id},
        {"_id": 0, "id": 1, "first_name": 1, "last_name": 1},
    ):
        pt_by_name[(p["first_name"], p["last_name"])] = p["id"]

    episode_by_name: dict[tuple[str, str], str] = {}
    plan_by_name: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for name_key, pid in pt_by_name.items():
        ep = await db.clinical_episode_cases.find_one(
            {"tenant_id": tenant_id, "patient_id": pid},
            {"_id": 0, "id": 1}, sort=[("start_date", -1)],
        )
        if ep:
            episode_by_name[name_key] = ep["id"]
        plan = await db.clinical_treatment_plans.find_one(
            {"tenant_id": tenant_id, "patient_id": pid},
            {"_id": 0, "id": 1, "diagnosis_ids": 1},
            sort=[("created_at", -1)],
        )
        if plan:
            plan_by_name[name_key] = (plan["id"], plan.get("diagnosis_ids", []))

    for name_key, specs in PAST_VISITS.items():
        pid = pt_by_name.get(name_key)
        if not pid:
            continue
        bp = CHART_BLUEPRINT.get(name_key) or {}
        ep_id = episode_by_name.get(name_key)
        plan_id, dx_ids = plan_by_name.get(name_key, (None, []))

        visit_number = 0
        for spec in specs:
            # Appointment — create or reuse.
            aid = await _upsert_past_appointment(
                db, tenant_id, location_id, pid,
                lead_doc_id, spec,
            )
            if not aid:
                continue
            appt = await db.appointments.find_one(
                {"id": aid}, {"_id": 0, "start_time": 1, "end_time": 1,
                              "reason": 1, "status": 1, "duration_minutes": 1},
            )
            start_dt = datetime.fromisoformat(appt["start_time"])
            end_dt = datetime.fromisoformat(appt["end_time"])

            # Encounter.
            eid = await _upsert_encounter(
                db, tenant_id, location_id, pid, lead_doc_id,
                aid, ep_id, spec["encounter_type"],
                start_dt, end_dt, appt["reason"] or "",
                appt["status"] or "completed",
            )

            # Initial exam OR follow-up note.
            if spec.get("is_initial"):
                await _upsert_initial_exam(
                    db, tenant_id, location_id,
                    eid, pid, aid, lead_doc_id, ep_id,
                    _iso(start_dt), dx_ids,
                    bp.get("history") or {},
                    bp.get("treatment_plan") or {},
                )
            else:
                visit_number += 1
                await _upsert_follow_up_note(
                    db, tenant_id, location_id,
                    eid, pid, aid, lead_doc_id, ep_id,
                    plan_id, _iso(start_dt), visit_number,
                    bp.get("treatment_plan") or {},
                )

    logger.info(
        "demo.clinical_workflow: appointments + encounters + exams + "
        "notes seeded across %d personas",
        len(PAST_VISITS),
    )
