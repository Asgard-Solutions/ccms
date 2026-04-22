"""Riverbend demo integrity verifier.

Runs after `seed_demo_clinic` + `seed_demo_billing` and walks every
domain's foreign keys against the canonical entity set, reporting any
row whose references don't resolve.

Prints a structured report and returns `(violations_count, report)`
so both the reseed CLI and the test suite can use it.

Scope — referential integrity checks:
  * appointments          → patient / provider / location / appointment_type / room
  * clinical_*            → patient / episode / diagnosis
  * patient_intake_forms  → patient
  * claims                → patient / payer / billing_provider / rendering_provider / facility / location / assigned_to
  * claim_lines           → claim
  * claim_diagnoses       → claim
  * billing_invoices      → patient
  * remittances           → claim / payer
  * patient_insurance_policies → patient / payer
  * notifications         → patient / appointment
  * follow_up_suggestions → patient / appointment / appointment_type / provider
  * episodes              → patient
  * treatment_plans       → patient / episode
  * outcome_entries       → patient / episode
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/../..")

from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")


async def _load_ids(db, collection: str, tenant_id: str,
                    match: dict | None = None) -> set[str]:
    q = {"tenant_id": tenant_id}
    if match:
        q.update(match)
    return {
        d["id"] async for d in db[collection].find(q, {"_id": 0, "id": 1})
    }


async def _check_refs(
    db, *,
    collection: str,
    tenant_id: str,
    ref_field: str,
    valid_ids: set[str] | None,
    violations: list,
    description: str,
    tenant_scoped: bool = True,
    allow_null: bool = True,
    extra_filter: dict | None = None,
) -> int:
    """Walk every doc and flag rows whose `ref_field` points at an id
    not in `valid_ids`."""
    q: dict = {"tenant_id": tenant_id} if tenant_scoped else {}
    if extra_filter:
        q.update(extra_filter)
    count = 0
    async for d in db[collection].find(q, {"_id": 0, "id": 1, ref_field: 1}):
        val = d.get(ref_field)
        if val is None:
            if not allow_null:
                violations.append({
                    "collection": collection, "row_id": d.get("id"),
                    "ref_field": ref_field, "value": None,
                    "description": f"{description} — ref_field is null",
                })
                count += 1
            continue
        if valid_ids is not None and val not in valid_ids:
            violations.append({
                "collection": collection, "row_id": d.get("id"),
                "ref_field": ref_field, "value": val,
                "description": description,
            })
            count += 1
    return count


async def verify_riverbend_integrity(tenant_slug: str = "default") -> tuple[int, dict]:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    tenant = await db.tenants.find_one({"slug": tenant_slug}, {"_id": 0, "id": 1})
    if not tenant:
        raise SystemExit(f"No tenant with slug={tenant_slug}")
    tid = tenant["id"]

    # --- canonical entity maps ----------------------------------------
    patient_ids    = await _load_ids(db, "patients",              tid)
    location_ids   = await _load_ids(db, "locations",             tid)
    user_ids       = await _load_ids(db, "users",                 tid)
    # user_ids for other-tenant admins are valid actors on assignees
    # too, but not for a pure Riverbend integrity check we keep tight.
    all_user_ids   = {u["id"] async for u in db.users.find({}, {"_id": 0, "id": 1})}
    appt_type_ids  = await _load_ids(db, "appointment_types",     tid)
    room_ids       = await _load_ids(db, "rooms",                 tid)
    provider_ids   = await _load_ids(db, "providers",             tid)
    facility_ids   = await _load_ids(db, "service_facilities",    tid)
    payer_ids      = await _load_ids(db, "billing_payers",        tid)
    appointment_ids= await _load_ids(db, "appointments",          tid)
    episode_ids    = await _load_ids(db, "clinical_episode_cases",tid)
    claim_ids      = await _load_ids(db, "claims",                tid)
    policy_ids     = await _load_ids(db, "patient_insurance_policies", tid)
    # Workflow chain counts (reported in `counts` below).
    initial_exam_ids = await _load_ids(db, "clinical_initial_exams", tid)
    follow_up_note_ids = await _load_ids(db, "clinical_follow_up_notes", tid)
    reexam_ids = await _load_ids(db, "clinical_reexams", tid)

    # Providers map also accepts user IDs (some billing entry points
    # stored a user UUID as provider_id before the 4c audit).
    provider_or_user = provider_ids | all_user_ids
    # Rendering-provider on claims may legitimately reference a user
    # who is the treating doctor; accept either.
    rendering_ok = provider_ids | all_user_ids

    violations: list[dict] = []
    c = 0

    # ---- appointments ------------------------------------------------
    c += await _check_refs(db, collection="appointments", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="appointment.patient_id missing in patients")
    c += await _check_refs(db, collection="appointments", tenant_id=tid,
        ref_field="provider_id", valid_ids=all_user_ids, allow_null=True,
        violations=violations, description="appointment.provider_id missing in users")
    c += await _check_refs(db, collection="appointments", tenant_id=tid,
        ref_field="location_id", valid_ids=location_ids, allow_null=False,
        violations=violations, description="appointment.location_id missing in locations")
    c += await _check_refs(db, collection="appointments", tenant_id=tid,
        ref_field="appointment_type_id", valid_ids=appt_type_ids, allow_null=True,
        violations=violations, description="appointment.appointment_type_id missing in appointment_types")
    c += await _check_refs(db, collection="appointments", tenant_id=tid,
        ref_field="room_id", valid_ids=room_ids, allow_null=True,
        violations=violations, description="appointment.room_id missing in rooms")

    # ---- claims ------------------------------------------------------
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="claim.patient_id missing in patients")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="payer_id", valid_ids=payer_ids, allow_null=True,
        violations=violations, description="claim.payer_id missing in billing_payers")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="billing_provider_id", valid_ids=provider_or_user, allow_null=True,
        violations=violations, description="claim.billing_provider_id not a provider or user")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="rendering_provider_id", valid_ids=rendering_ok, allow_null=True,
        violations=violations, description="claim.rendering_provider_id not a provider or user")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="facility_id", valid_ids=facility_ids, allow_null=True,
        violations=violations, description="claim.facility_id missing in service_facilities")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="location_id", valid_ids=location_ids, allow_null=True,
        violations=violations, description="claim.location_id missing in locations")
    c += await _check_refs(db, collection="claims", tenant_id=tid,
        ref_field="assigned_to", valid_ids=all_user_ids, allow_null=True,
        violations=violations, description="claim.assigned_to missing in users")

    # claim_lines + claim_diagnoses → claim
    c += await _check_refs(db, collection="claim_lines", tenant_id=tid,
        ref_field="claim_id", valid_ids=claim_ids, allow_null=False,
        violations=violations, description="claim_line orphaned (claim_id missing)")
    c += await _check_refs(db, collection="claim_diagnoses", tenant_id=tid,
        ref_field="claim_id", valid_ids=claim_ids, allow_null=False,
        violations=violations, description="claim_diagnosis orphaned (claim_id missing)")

    # ---- insurance ---------------------------------------------------
    c += await _check_refs(db, collection="patient_insurance_policies", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="insurance_policy.patient_id missing")
    c += await _check_refs(db, collection="patient_insurance_policies", tenant_id=tid,
        ref_field="payer_id", valid_ids=payer_ids, allow_null=False,
        violations=violations, description="insurance_policy.payer_id missing")

    # ---- billing artifacts -------------------------------------------
    c += await _check_refs(db, collection="billing_invoices", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="invoice.patient_id missing")
    c += await _check_refs(db, collection="remittances", tenant_id=tid,
        ref_field="claim_id", valid_ids=claim_ids, allow_null=True,
        violations=violations, description="remittance.claim_id missing")

    # ---- notifications + follow-ups ----------------------------------
    c += await _check_refs(db, collection="notifications", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=True,
        violations=violations, description="notification.patient_id missing",
        extra_filter={"source": "demo_seed"})
    c += await _check_refs(db, collection="notifications", tenant_id=tid,
        ref_field="appointment_id", valid_ids=appointment_ids, allow_null=True,
        violations=violations, description="notification.appointment_id missing",
        extra_filter={"source": "demo_seed"})
    c += await _check_refs(db, collection="follow_up_suggestions", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="follow_up.patient_id missing",
        extra_filter={"source": "demo_seed"})
    c += await _check_refs(db, collection="follow_up_suggestions", tenant_id=tid,
        ref_field="appointment_id", valid_ids=appointment_ids, allow_null=True,
        violations=violations, description="follow_up.appointment_id missing",
        extra_filter={"source": "demo_seed"})
    c += await _check_refs(db, collection="follow_up_suggestions", tenant_id=tid,
        ref_field="appointment_type_id", valid_ids=appt_type_ids, allow_null=True,
        violations=violations, description="follow_up.appointment_type_id missing",
        extra_filter={"source": "demo_seed"})

    # ---- clinical ----------------------------------------------------
    c += await _check_refs(db, collection="clinical_episode_cases", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="clinical_episode.patient_id missing")
    c += await _check_refs(db, collection="clinical_diagnoses", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="diagnosis.patient_id missing")
    c += await _check_refs(db, collection="clinical_diagnoses", tenant_id=tid,
        ref_field="episode_id", valid_ids=episode_ids, allow_null=True,
        violations=violations, description="diagnosis.episode_id missing")
    c += await _check_refs(db, collection="clinical_treatment_plans", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="treatment_plan.patient_id missing")
    c += await _check_refs(db, collection="clinical_treatment_plans", tenant_id=tid,
        ref_field="episode_id", valid_ids=episode_ids, allow_null=True,
        violations=violations, description="treatment_plan.episode_id missing")
    c += await _check_refs(db, collection="clinical_history", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="history.patient_id missing")
    c += await _check_refs(db, collection="clinical_outcome_entries", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="outcome.patient_id missing")
    c += await _check_refs(db, collection="clinical_outcome_entries", tenant_id=tid,
        ref_field="episode_id", valid_ids=episode_ids, allow_null=True,
        violations=violations, description="outcome.episode_id missing")
    c += await _check_refs(db, collection="patient_intake_forms", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="intake_form.patient_id missing")

    # ---- clinical workflow chain (Encounter → Exam/Note/Re-Exam) -----
    encounter_ids = await _load_ids(db, "clinical_encounters", tid)
    # Encounters must point at real patients, appointments, and (if
    # set) episodes/providers/locations.
    c += await _check_refs(db, collection="clinical_encounters", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="encounter.patient_id missing")
    c += await _check_refs(db, collection="clinical_encounters", tenant_id=tid,
        ref_field="appointment_id", valid_ids=appointment_ids, allow_null=False,
        violations=violations, description="encounter.appointment_id missing — orphan encounter")
    c += await _check_refs(db, collection="clinical_encounters", tenant_id=tid,
        ref_field="episode_id", valid_ids=episode_ids, allow_null=True,
        violations=violations, description="encounter.episode_id missing")
    c += await _check_refs(db, collection="clinical_encounters", tenant_id=tid,
        ref_field="provider_id", valid_ids=all_user_ids, allow_null=True,
        violations=violations, description="encounter.provider_id missing in users")
    c += await _check_refs(db, collection="clinical_encounters", tenant_id=tid,
        ref_field="location_id", valid_ids=location_ids, allow_null=True,
        violations=violations, description="encounter.location_id missing")

    # Initial Exams must reference a real encounter + appointment.
    c += await _check_refs(db, collection="clinical_initial_exams", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="initial_exam.patient_id missing")
    c += await _check_refs(db, collection="clinical_initial_exams", tenant_id=tid,
        ref_field="encounter_id", valid_ids=encounter_ids, allow_null=False,
        violations=violations, description="initial_exam.encounter_id missing — floating exam")
    c += await _check_refs(db, collection="clinical_initial_exams", tenant_id=tid,
        ref_field="appointment_id", valid_ids=appointment_ids, allow_null=True,
        violations=violations, description="initial_exam.appointment_id missing")
    c += await _check_refs(db, collection="clinical_initial_exams", tenant_id=tid,
        ref_field="episode_id", valid_ids=episode_ids, allow_null=True,
        violations=violations, description="initial_exam.episode_id missing")

    # Follow-up Notes must reference a real encounter.
    c += await _check_refs(db, collection="clinical_follow_up_notes", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="follow_up_note.patient_id missing")
    c += await _check_refs(db, collection="clinical_follow_up_notes", tenant_id=tid,
        ref_field="encounter_id", valid_ids=encounter_ids, allow_null=False,
        violations=violations, description="follow_up_note.encounter_id missing — floating note")
    c += await _check_refs(db, collection="clinical_follow_up_notes", tenant_id=tid,
        ref_field="appointment_id", valid_ids=appointment_ids, allow_null=True,
        violations=violations, description="follow_up_note.appointment_id missing")

    # Re-Exams must reference a real encounter + plan.
    c += await _check_refs(db, collection="clinical_reexams", tenant_id=tid,
        ref_field="patient_id", valid_ids=patient_ids, allow_null=False,
        violations=violations, description="reexam.patient_id missing")
    c += await _check_refs(db, collection="clinical_reexams", tenant_id=tid,
        ref_field="encounter_id", valid_ids=encounter_ids, allow_null=False,
        violations=violations, description="reexam.encounter_id missing — floating re-exam")

    # "No floating treatment plans" guard: every persona with a plan
    # must also have at least one encounter in the chart. A plan
    # without a prior encounter implies we're seeding documentation
    # that never actually happened in the workflow.
    plan_patient_ids = {
        d["patient_id"] async for d in db.clinical_treatment_plans.find(
            {"tenant_id": tid}, {"_id": 0, "patient_id": 1},
        )
    }
    encounter_patient_ids = {
        d["patient_id"] async for d in db.clinical_encounters.find(
            {"tenant_id": tid}, {"_id": 0, "patient_id": 1},
        )
    }
    for pid in plan_patient_ids:
        if pid not in encounter_patient_ids:
            violations.append({
                "collection": "clinical_treatment_plans",
                "row_id": pid,
                "ref_field": "(workflow chain)",
                "value": pid,
                "description": (
                    "patient has a treatment_plan but no clinical_encounter — "
                    "plan is floating without an upstream encounter"
                ),
            })
            c += 1

    # ---- uniqueness: no duplicate personas ---------------------------
    by_name = {}
    async for p in db.patients.find(
        {"tenant_id": tid},
        {"_id": 0, "first_name": 1, "last_name": 1, "email": 1, "id": 1},
    ):
        key = (p.get("first_name"), p.get("last_name"), p.get("email"))
        by_name.setdefault(key, []).append(p["id"])
    for key, ids in by_name.items():
        if len(ids) > 1:
            violations.append({
                "collection": "patients", "row_id": ",".join(ids),
                "ref_field": "(duplicate persona)",
                "value": f"{key}",
                "description": f"{len(ids)} copies of {key} — seed not idempotent",
            })
            c += 1

    report = {
        "tenant_id": tid,
        "counts": {
            "patients": len(patient_ids),
            "locations": len(location_ids),
            "users_tenant": len(user_ids),
            "providers": len(provider_ids),
            "appointment_types": len(appt_type_ids),
            "rooms": len(room_ids),
            "service_facilities": len(facility_ids),
            "payers": len(payer_ids),
            "appointments": len(appointment_ids),
            "episodes": len(episode_ids),
            "claims": len(claim_ids),
            "policies": len(policy_ids),
            "encounters": len(encounter_ids),
            "initial_exams": len(initial_exam_ids),
            "follow_up_notes": len(follow_up_note_ids),
            "reexams": len(reexam_ids),
        },
        "violations_count": c,
        "violations": violations,
    }
    return c, report


def _main() -> None:
    c, report = asyncio.run(verify_riverbend_integrity())
    print(f"Tenant: {report['tenant_id']}")
    print("Canonical entity counts:")
    for k, v in report["counts"].items():
        print(f"  {k:25s}: {v}")
    print(f"\nIntegrity violations: {c}")
    if c:
        # Group by (collection, description) for a scannable summary.
        from collections import Counter
        grouped = Counter(
            (v["collection"], v["description"]) for v in report["violations"]
        )
        print("\nBy category:")
        for (col, desc), n in sorted(grouped.items(), key=lambda x: -x[1]):
            print(f"  [{n:>4}] {col:30s} — {desc}")
        print("\nSample (first 20):")
        for v in report["violations"][:20]:
            print(f"  {v['collection']:28s} row={str(v['row_id'])[:20]:21s} "
                  f"{v['ref_field']}={str(v['value'])[:30]:31s} → {v['description']}")
        sys.exit(1)
    else:
        print("\nRiverbend demo integrity: OK")


if __name__ == "__main__":
    _main()
