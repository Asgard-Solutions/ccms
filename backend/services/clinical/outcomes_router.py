"""Clinical Outcomes / Functional Measures router — Phase 7.

Longitudinal PRO store. Standalone entries may be recorded:
  - manually by a provider (`source=provider_charted`)
  - by a patient via intake/portal (`source=patient_reported`, `source=intake_form`)
  - auto-emitted at Re-Exam sign (`source=reexam`, `linked_reexam_id` set)

Entries with `source=reexam` are the snapshot-of-record for trending and
are locked — direct PATCH/DELETE returns 409 (edit the source Re-Exam
instead, which in this phase is immutable once signed).

Endpoints (mounted under `/api`):
    GET    /api/patients/{pid}/clinical/outcomes            (with filters)
    POST   /api/patients/{pid}/clinical/outcomes
    PATCH  /api/patients/{pid}/clinical/outcomes/{oid}
    DELETE /api/patients/{pid}/clinical/outcomes/{oid}
    GET    /api/patients/{pid}/clinical/outcomes/trends
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import now_iso
from services.clinical.router import _load_patient, _log_clinical_event

router = APIRouter(prefix="/patients", tags=["clinical"])

OUTCOME_MEASURE = Literal[
    "ndi", "oswestry", "pain_vas", "functional_index", "pain_scale", "custom",
]
OUTCOME_SOURCE = Literal[
    "provider_charted", "patient_reported", "intake_form", "reexam",
]


class OutcomeEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure_type: OUTCOME_MEASURE
    label: str = Field(min_length=1, max_length=120)
    score: float
    max_score: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    captured_at: str | None = None  # ISO; defaults to now
    source: OUTCOME_SOURCE = "provider_charted"
    note: str | None = Field(default=None, max_length=1000)
    episode_id: str | None = None
    appointment_id: str | None = None
    linked_treatment_plan_id: str | None = None


class OutcomeEntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float | None = None
    max_score: float | None = None
    unit: str | None = Field(default=None, max_length=40)
    captured_at: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    episode_id: str | None = None
    linked_treatment_plan_id: str | None = None


class OutcomeEntryPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    episode_id: str | None = None
    appointment_id: str | None = None
    linked_reexam_id: str | None = None
    linked_treatment_plan_id: str | None = None
    measure_type: OUTCOME_MEASURE
    label: str
    score: float
    max_score: float | None = None
    unit: str | None = None
    captured_at: str
    source: OUTCOME_SOURCE
    note: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None


class OutcomeSeries(BaseModel):
    model_config = ConfigDict(extra="ignore")
    measure_type: OUTCOME_MEASURE
    label: str
    unit: str | None = None
    max_score: float | None = None
    series: list[dict] = Field(default_factory=list)


class OutcomeTrends(BaseModel):
    model_config = ConfigDict(extra="ignore")
    patient_id: str
    trends: list[OutcomeSeries]
    generated_at: str


def _strip(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in {"_id", "history_log"}}


async def _load(db, ctx: TenantContext, patient_id: str, oid: str) -> dict:
    q = scoped_filter({"id": oid, "patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Outcome not found")
    doc = await db.clinical_outcome_entries.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Outcome not found")
    return doc


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@router.post(
    "/{patient_id}/clinical/outcomes",
    response_model=OutcomeEntryPublic, status_code=201,
)
async def create_outcome(
    patient_id: str,
    payload: OutcomeEntryCreate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    # `reexam` source may only be set server-side by the Re-Exam sign path.
    if payload.source == "reexam":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Use the Re-Exam sign workflow to capture reexam outcomes",
        )

    if payload.episode_id:
        ep = await db.clinical_episode_cases.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": payload.episode_id},
            {"_id": 0, "id": 1},
        )
        if not ep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Episode not found on patient")

    now = now_iso()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "patient_id": patient_id,
        "episode_id": payload.episode_id,
        "appointment_id": payload.appointment_id,
        "linked_reexam_id": None,
        "linked_treatment_plan_id": payload.linked_treatment_plan_id,
        "measure_type": payload.measure_type,
        "label": payload.label,
        "score": float(payload.score),
        "max_score": payload.max_score,
        "unit": payload.unit,
        "captured_at": payload.captured_at or now,
        "source": payload.source,
        "note": payload.note,
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
    }
    doc = stamp_for_write(doc, ctx)
    await db.clinical_outcome_entries.insert_one(doc)

    await _log_clinical_event(
        db, ctx, actor=user, patient_id=patient_id, episode_id=payload.episode_id,
        event_type="outcome_entry.created",
        entity_type="outcome_entry", entity_id=oid,
        metadata={
            "measure_type": payload.measure_type, "score": payload.score,
            "source": payload.source,
        },
    )
    await audit_success(
        user, "clinical.outcome.created", request,
        entity_type="clinical_outcome", entity_id=oid, phi_accessed=True,
        metadata={"patient_id": patient_id, "measure_type": payload.measure_type},
    )
    return _strip(doc)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/outcomes",
    response_model=list[OutcomeEntryPublic],
)
async def list_outcomes(
    patient_id: str,
    request: Request,
    measure_type: str | None = Query(default=None),
    episode_id: str | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q: dict = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if measure_type:
        q["measure_type"] = measure_type
    if episode_id:
        q["episode_id"] = episode_id
    cursor = db.clinical_outcome_entries.find(q, {"_id": 0}).sort("captured_at", -1)
    rows = [d async for d in cursor]
    await audit_success(
        user, "clinical.outcome.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(rows)},
    )
    return [_strip(d) for d in rows]


# ---------------------------------------------------------------------------
# PATCH / DELETE — locked for reexam-sourced entries
# ---------------------------------------------------------------------------
@router.patch(
    "/{patient_id}/clinical/outcomes/{oid}",
    response_model=OutcomeEntryPublic,
)
async def patch_outcome(
    patient_id: str, oid: str, payload: OutcomeEntryUpdate, request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load(db, ctx, patient_id, oid)
    if current["source"] == "reexam":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Re-exam-sourced outcomes are immutable; edit the Re-Exam source",
        )
    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return _strip(current)
    if dumped.get("episode_id"):
        ep = await db.clinical_episode_cases.find_one(
            {"tenant_id": ctx.tenant_id, "patient_id": patient_id, "id": dumped["episode_id"]},
            {"_id": 0, "id": 1},
        )
        if not ep:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Episode not found on patient")
    dumped["updated_at"] = now_iso()
    await db.clinical_outcome_entries.update_one(
        {"id": oid, "tenant_id": current["tenant_id"]}, {"$set": dumped},
    )
    fresh = await db.clinical_outcome_entries.find_one(
        {"id": oid, "tenant_id": current["tenant_id"]}, {"_id": 0},
    )
    await audit_success(
        user, "clinical.outcome.updated", request,
        entity_type="clinical_outcome", entity_id=oid, phi_accessed=True,
    )
    return _strip(fresh)


@router.delete("/{patient_id}/clinical/outcomes/{oid}", status_code=204)
async def delete_outcome(
    patient_id: str, oid: str, request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load(db, ctx, patient_id, oid)
    if current["source"] == "reexam":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Re-exam-sourced outcomes cannot be directly deleted",
        )
    await db.clinical_outcome_entries.delete_one(
        {"id": oid, "tenant_id": current["tenant_id"]},
    )
    await audit_success(
        user, "clinical.outcome.deleted", request,
        entity_type="clinical_outcome", entity_id=oid, phi_accessed=True,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Trends — chronological series per (measure_type, label)
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/outcomes/trends",
    response_model=OutcomeTrends,
)
async def get_trends(
    patient_id: str,
    request: Request,
    episode_id: str | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    q: dict = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return {"patient_id": patient_id, "trends": [], "generated_at": now_iso()}
    if episode_id:
        q["episode_id"] = episode_id
    cursor = db.clinical_outcome_entries.find(q, {"_id": 0}).sort("captured_at", 1)
    grouped: dict[tuple[str, str], dict] = {}
    async for d in cursor:
        key = (d["measure_type"], d.get("label") or d["measure_type"])
        if key not in grouped:
            grouped[key] = {
                "measure_type": d["measure_type"],
                "label": d.get("label") or d["measure_type"],
                "unit": d.get("unit"),
                "max_score": d.get("max_score"),
                "series": [],
            }
        grouped[key]["series"].append({
            "captured_at": d["captured_at"],
            "score": d["score"],
            "entry_id": d["id"],
            "source": d["source"],
            "linked_reexam_id": d.get("linked_reexam_id"),
        })
    await audit_success(
        user, "clinical.outcome.trends_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"series_count": len(grouped)},
    )
    return {
        "patient_id": patient_id,
        "trends": list(grouped.values()),
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Re-Exam sign hook — called from reexams_router when a re-exam is signed.
# Idempotent: (reexam_id, measure_type, label) key prevents duplicates.
# ---------------------------------------------------------------------------
async def emit_outcomes_from_reexam(
    db, ctx: TenantContext, *, reexam: dict, user: dict,
) -> list[str]:
    """Insert `clinical_outcome_entries` rows for each entry in the re-exam's
    `outcome_updates`. Source is `reexam`; entries are write-once."""
    emitted: list[str] = []
    updates = reexam.get("outcome_updates") or []
    if not updates:
        return emitted
    now = now_iso()
    for upd in updates:
        if upd.get("score") is None:
            continue
        # Idempotency
        existing = await db.clinical_outcome_entries.find_one(
            {
                "tenant_id": ctx.tenant_id,
                "linked_reexam_id": reexam["id"],
                "measure_type": upd["measure_type"],
                "label": upd.get("label") or upd["measure_type"],
            },
            {"_id": 0, "id": 1},
        )
        if existing:
            emitted.append(existing["id"])
            continue
        oid = str(uuid.uuid4())
        doc = {
            "id": oid,
            "patient_id": reexam["patient_id"],
            "episode_id": reexam.get("episode_id"),
            "appointment_id": reexam.get("appointment_id"),
            "linked_reexam_id": reexam["id"],
            "linked_treatment_plan_id": reexam.get("treatment_plan_id"),
            "measure_type": upd["measure_type"],
            "label": upd.get("label") or upd["measure_type"],
            "score": float(upd["score"]),
            "max_score": upd.get("max_score"),
            "unit": None,
            "captured_at": reexam.get("date_of_service") or now,
            "source": "reexam",
            "note": upd.get("note"),
            "created_at": now,
            "updated_at": now,
            "created_by": user["id"],
        }
        doc = stamp_for_write(doc, ctx)
        await db.clinical_outcome_entries.insert_one(doc)
        emitted.append(oid)
    return emitted
