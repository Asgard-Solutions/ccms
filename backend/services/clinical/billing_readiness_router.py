"""Billing Readiness router — Phase 8.

Read-only evaluative surface that answers one question:

    "Is the documentation on this encounter complete enough to defensibly
    bill?"

Output is designed to be consumed later by the Billing module without
rework. This router NEVER mutates billing data, never posts charges, and
never triggers claim generation.

Signal sources:
    - clinical_encounters        (encounter itself + appointment link)
    - clinical_initial_exams     (NPE visit type)
    - clinical_follow_up_notes   (follow-up / treatment visit)
    - clinical_reexams           (re-evaluation)
    - clinical_diagnoses         (dx linkage)
    - clinical_treatment_plans   (plan linkage)

Endpoint:
    GET /api/patients/{pid}/clinical/encounters/{eid}/billing-readiness

Each check has: `key`, `label`, `severity` (`info|warn|fail`),
`pass` (bool), optional `detail`. The aggregator resolves
`overall_status`:
    - `blocked`   if any `severity="fail"` check fails
    - `warnings`  if any `severity="warn"` check fails but no fails
    - `ready`     if all fail+warn checks pass (info-only failures
                   don't change overall status)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter
from services.clinical.models import now_iso
from services.clinical.router import _load_patient

router = APIRouter(prefix="/patients", tags=["clinical"])

# Map encounter_type → canonical note artifact contract. `required_plan` is
# True for recurring care (follow-up + treatment visits); initial exams and
# re-evals don't require an existing plan.
ENCOUNTER_CONTRACT: dict[str, dict[str, Any]] = {
    "new_patient_exam": {
        "note_kind": "initial_exam",
        "note_collection": "clinical_initial_exams",
        "label": "New patient exam",
        "required_plan": False,
    },
    "follow_up": {
        "note_kind": "follow_up_note",
        "note_collection": "clinical_follow_up_notes",
        "label": "Follow-up visit",
        "required_plan": True,
    },
    "treatment_visit": {
        "note_kind": "follow_up_note",
        "note_collection": "clinical_follow_up_notes",
        "label": "Treatment visit",
        "required_plan": True,
    },
    "re_evaluation": {
        "note_kind": "re_exam",
        "note_collection": "clinical_reexams",
        "label": "Re-evaluation",
        "required_plan": False,
    },
}

CheckSeverity = Literal["info", "warn", "fail"]
ReadinessStatus = Literal["ready", "warnings", "blocked"]


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    label: str
    severity: CheckSeverity
    passed: bool
    detail: str | None = None


class ReadinessDiagnosis(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    icd10_code: str | None = None
    label: str | None = None
    status: str | None = None


class ReadinessProcedure(BaseModel):
    """Future-billing friendly. No CPT codes in this phase — just the
    structured intervention descriptor the note/plan already carries."""

    model_config = ConfigDict(extra="ignore")
    kind: str
    description: str | None = None
    body_region: str | None = None
    count: int = 1


class ReadinessNote(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: str                 # follow_up_note | initial_exam | re_exam
    id: str | None = None
    status: str | None = None
    signed_at: str | None = None
    signed_by: str | None = None
    signed_by_name: str | None = None
    has_addenda: bool = False
    addendum_count: int = 0


class ReadinessTreatmentPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str | None = None
    plan_status: str | None = None


class BillingReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    encounter_id: str
    patient_id: str
    appointment_id: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    date_of_service: str | None = None
    episode_id: str | None = None
    visit_type: str | None = None
    visit_type_label: str | None = None
    note: ReadinessNote | None = None
    diagnoses: list[ReadinessDiagnosis] = Field(default_factory=list)
    procedures: list[ReadinessProcedure] = Field(default_factory=list)
    treatment_plan: ReadinessTreatmentPlan | None = None
    overall_status: ReadinessStatus
    checks: list[ReadinessCheck] = Field(default_factory=list)
    generated_at: str


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def _iso_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Best-effort: our stored ISO strings include "Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    # Date-only strings (e.g. plan.re_exam_date = "2026-05-13") parse as
    # naive — normalise to UTC so comparisons with tz-aware datetimes
    # don't raise TypeError.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _hydrate_user_name(db, tenant_id: str, user_id: str | None) -> str | None:
    if not user_id:
        return None
    u = await db.users.find_one(
        {"id": user_id, "tenant_id": tenant_id}, {"_id": 0, "name": 1, "email": 1},
    )
    if not u:
        return None
    return u.get("name") or u.get("email")


async def _count_addenda(db, tenant_id: str, parent_type: str, parent_id: str) -> tuple[int, bool]:
    cnt = await db.clinical_addenda.count_documents({
        "tenant_id": tenant_id,
        "parent_type": parent_type,
        "parent_id": parent_id,
    })
    return cnt, cnt > 0


def _procedures_from_followup(note: dict) -> list[dict]:
    """Follow-up notes carry `plan.treatment_rendered` as a list of
    `TreatmentEntry` dicts. Translate each into a neutral procedure row."""
    plan = note.get("plan") or {}
    entries = plan.get("treatment_rendered") or []
    out: list[dict] = []
    for t in entries:
        if not isinstance(t, dict):
            continue
        out.append({
            "kind": t.get("kind") or "other",
            "description": t.get("description") or t.get("technique") or t.get("modality"),
            "body_region": t.get("region") or (", ".join(t.get("segments") or []) or None),
            "count": 1,
        })
    return out


def _procedures_from_plan(plan: dict | None) -> list[dict]:
    """Treatment plans carry `planned_interventions` (goal-level)."""
    if not plan:
        return []
    interventions = plan.get("planned_interventions") or []
    out: list[dict] = []
    for iv in interventions:
        if not isinstance(iv, dict):
            continue
        out.append({
            "kind": iv.get("kind") or "other",
            "description": iv.get("description") or iv.get("technique"),
            "body_region": iv.get("region"),
            "count": 1,
        })
    return out


def _procedures_from_exam(exam: dict) -> list[dict]:
    """Initial exams + re-exams carry structured exam sections. For billing
    readiness, we treat the exam's body regions + new diagnoses as proxy
    procedure entries so downstream billing workflows see consistent
    shape."""
    out: list[dict] = []
    examination = exam.get("examination") or {}
    regions = examination.get("regions_examined") or examination.get("body_regions") or []
    if regions and isinstance(regions, list):
        for r in regions:
            if isinstance(r, str) and r.strip():
                out.append({"kind": "examination", "description": r, "body_region": r, "count": 1})
    return out


@router.get(
    "/{patient_id}/clinical/encounters/{encounter_id}/billing-readiness",
    response_model=BillingReadinessResponse,
)
async def get_billing_readiness(
    patient_id: str,
    encounter_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    response = await evaluate_billing_readiness(
        db, ctx, patient_id, encounter_id,
    )
    await audit_success(
        user, "clinical.billing_readiness.viewed", request,
        entity_type="clinical_encounter", entity_id=encounter_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "overall_status": response.overall_status},
    )
    return response


async def evaluate_billing_readiness(
    db,
    ctx: TenantContext,
    patient_id: str,
    encounter_id: str,
) -> BillingReadinessResponse:
    """Core aggregation for the Phase 8 readiness view. Extracted so
    Phase 9 (claim-from-encounter) can reuse it without issuing a nested
    HTTP call.

    Raises `HTTPException(404)` if the encounter is not visible in the
    tenant scope.
    """
    enc_q = scoped_filter(
        {"id": encounter_id, "patient_id": patient_id}, ctx, location_scoped=False,
    )
    if enc_q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")
    encounter = await db.clinical_encounters.find_one(enc_q, {"_id": 0})
    if not encounter:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Encounter not found")

    enc_type = encounter.get("encounter_type") or "follow_up"
    contract = ENCOUNTER_CONTRACT.get(enc_type, ENCOUNTER_CONTRACT["follow_up"])
    note_coll = contract["note_collection"]
    note_kind = contract["note_kind"]
    requires_plan = contract["required_plan"]

    # ------------------------------------------------------------------ note
    note = await db[note_coll].find_one(
        {"tenant_id": ctx.tenant_id, "encounter_id": encounter_id}, {"_id": 0},
    )
    note_payload: ReadinessNote | None = None
    addendum_count = 0
    if note:
        addendum_count, has_add = await _count_addenda(
            db, ctx.tenant_id, note_kind, note["id"],
        )
        note_payload = ReadinessNote(
            kind=note_kind,
            id=note.get("id"),
            status=note.get("status"),
            signed_at=note.get("signed_at"),
            signed_by=note.get("signed_by"),
            signed_by_name=await _hydrate_user_name(db, ctx.tenant_id, note.get("signed_by")),
            has_addenda=has_add,
            addendum_count=addendum_count,
        )

    # ----------------------------------------------------------------- dx
    dx_ids_linked: list[str] = []
    if note:
        # The note's own linkage: assessment.linked_diagnosis_ids (follow-up),
        # diagnoses/new_diagnoses (exams)
        assessment = note.get("assessment") or {}
        dx_ids_linked.extend(assessment.get("linked_diagnosis_ids") or [])
    dx_active_cursor = db.clinical_diagnoses.find(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": patient_id,
            "status": "active",
            "episode_id": encounter.get("episode_id"),
        },
        {"_id": 0, "id": 1, "icd10_code": 1, "label": 1, "status": 1},
    )
    dx_rows = [d async for d in dx_active_cursor]
    diagnoses = [ReadinessDiagnosis(**d) for d in dx_rows]
    dx_linkage_present = bool(dx_ids_linked) or bool(dx_rows)

    # ----------------------------------------------------------------- plan
    plan = None
    plan_doc: dict | None = None
    treatment_plan_id = note.get("treatment_plan_id") if note else None
    if not treatment_plan_id and encounter.get("episode_id"):
        plan_doc = await db.clinical_treatment_plans.find_one(
            {
                "tenant_id": ctx.tenant_id,
                "patient_id": patient_id,
                "episode_id": encounter["episode_id"],
                "plan_status": "active",
            },
            {"_id": 0},
        )
    elif treatment_plan_id:
        plan_doc = await db.clinical_treatment_plans.find_one(
            {"tenant_id": ctx.tenant_id, "id": treatment_plan_id},
            {"_id": 0},
        )
    if plan_doc:
        plan = ReadinessTreatmentPlan(
            id=plan_doc["id"], title=plan_doc.get("title"),
            plan_status=plan_doc.get("plan_status"),
        )

    # ------------------------------------------------------------ procedures
    procedures_raw: list[dict] = []
    if note and note_kind == "follow_up_note":
        procedures_raw = _procedures_from_followup(note)
    elif note and note_kind in {"initial_exam", "re_exam"}:
        procedures_raw = _procedures_from_exam(note)
    if not procedures_raw and plan_doc:
        procedures_raw = _procedures_from_plan(plan_doc)
    procedures = [ReadinessProcedure(**p) for p in procedures_raw]

    # ----------------------------------------------------------------- checks
    checks: list[ReadinessCheck] = []

    def add(key: str, label: str, severity: CheckSeverity, passed: bool, detail: str | None = None):
        checks.append(ReadinessCheck(
            key=key, label=label, severity=severity, passed=passed, detail=detail,
        ))

    # patient/provider/DOS presence
    add(
        "patient_present", "Patient linked", "fail",
        _nonempty(encounter.get("patient_id")),
    )
    add(
        "provider_present", "Provider on encounter", "fail",
        _nonempty(encounter.get("provider_id")),
        None if encounter.get("provider_id") else "Assign the rendering provider on the encounter.",
    )
    add(
        "dos_present", "Date of service recorded", "fail",
        _nonempty(encounter.get("date_of_service")),
    )

    # appointment linkage
    add(
        "appointment_linked", "Appointment linked", "warn",
        _nonempty(encounter.get("appointment_id")),
        None if encounter.get("appointment_id") else "Encounter was not launched from a calendar appointment.",
    )

    # encounter completeness (status)
    enc_status = encounter.get("status")
    add(
        "encounter_completed",
        "Encounter marked completed",
        "warn",
        enc_status == "completed",
        None if enc_status == "completed" else f"Encounter status is '{enc_status}'. Mark completed when the visit is done.",
    )

    # note exists + status + signature
    add(
        "note_exists",
        f"{contract['label']} note authored",
        "fail",
        note is not None,
        None if note else "No chart note has been authored for this encounter.",
    )
    note_status = note.get("status") if note else None
    add(
        "note_signed",
        "Note signed",
        "fail",
        note_status == "signed",
        None if note_status == "signed" else f"Note status is '{note_status or 'missing'}'. Provider signature is required before billing.",
    )
    add(
        "signature_present",
        "Provider signature captured",
        "fail",
        bool(note and note.get("signed_by") and note.get("signed_at")),
        None if note and note.get("signed_by") else "Note is not signed by a provider.",
    )

    # diagnoses linkage
    add(
        "diagnosis_linked",
        "Diagnosis linkage present",
        "fail",
        dx_linkage_present,
        None if dx_linkage_present else "Link at least one active ICD-10 diagnosis for this episode.",
    )

    # treatment documented
    treatment_documented = False
    if note_kind == "follow_up_note" and note:
        treatment_documented = bool((note.get("plan") or {}).get("treatment_rendered"))
    elif note_kind in {"initial_exam", "re_exam"} and note:
        treatment_documented = _nonempty((note.get("examination") or {})) or bool(procedures)
    add(
        "treatment_documented",
        "Treatment/findings documented",
        "fail",
        treatment_documented,
        None if treatment_documented else "Document the treatment rendered (adjustments, modalities, soft tissue, exercise) or exam findings.",
    )

    # objective findings
    objective_present = False
    if note:
        obj = note.get("objective") or note.get("examination") or {}
        objective_present = _nonempty(obj)
    add(
        "objective_findings",
        "Objective findings captured",
        "warn",
        objective_present,
        None if objective_present else "Add region findings, ROM, palpation, or exam sections.",
    )

    # response/progress
    response_present = False
    if note_kind == "follow_up_note" and note:
        response_present = _nonempty((note.get("assessment") or {}).get("response_to_care"))
    elif note_kind in {"initial_exam", "re_exam"} and note:
        response_present = _nonempty(note.get("recommendation_decision")) or _nonempty(
            (note.get("assessment") or {}).get("clinical_impression"),
        )
    add(
        "response_documented",
        "Response / progress documented",
        "warn",
        response_present,
        None if response_present else "Document the patient's response to care or an assessment summary.",
    )

    # treatment plan linkage (conditional)
    plan_linked_pass = (plan is not None) if requires_plan else True
    add(
        "plan_linkage",
        "Treatment plan linkage" + (" (required)" if requires_plan else ""),
        "fail" if requires_plan else "info",
        plan_linked_pass,
        None if plan_linked_pass else "No active treatment plan on this episode. Follow-up/treatment visits must link a plan.",
    )

    # re-exam due warning (simple heuristic based on plan.re_exam_date)
    reexam_due = False
    if plan_doc and plan_doc.get("re_exam_date"):
        re_exam_dt = _parse_iso(plan_doc["re_exam_date"])
        dos_dt = _parse_iso(encounter.get("date_of_service"))
        ref = dos_dt or _iso_now()
        if re_exam_dt and ref >= re_exam_dt and enc_type != "re_evaluation":
            reexam_due = True
    add(
        "reexam_not_overdue",
        "Re-exam schedule",
        "warn",
        not reexam_due,
        "Treatment plan's re-exam date has passed; schedule a re-evaluation." if reexam_due else None,
    )

    # Derive overall status
    overall: ReadinessStatus = "ready"
    if any(not c.passed and c.severity == "fail" for c in checks):
        overall = "blocked"
    elif any(not c.passed and c.severity == "warn" for c in checks):
        overall = "warnings"

    response = BillingReadinessResponse(
        encounter_id=encounter_id,
        patient_id=patient_id,
        appointment_id=encounter.get("appointment_id"),
        provider_id=encounter.get("provider_id"),
        provider_name=await _hydrate_user_name(db, ctx.tenant_id, encounter.get("provider_id")),
        date_of_service=encounter.get("date_of_service"),
        episode_id=encounter.get("episode_id"),
        visit_type=enc_type,
        visit_type_label=contract["label"],
        note=note_payload,
        diagnoses=diagnoses,
        procedures=procedures,
        treatment_plan=plan,
        overall_status=overall,
        checks=checks,
        generated_at=now_iso(),
    )
    return response
