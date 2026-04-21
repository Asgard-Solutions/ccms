"""Clinical router — Phase 1 endpoints.

All endpoints are mounted under `/api/patients/{patient_id}/clinical/*` to
keep the patient chart as the authoritative home of clinical data.

Phase 1 surface:
    GET    /patients/{pid}/clinical/summary
    GET    /patients/{pid}/clinical/episodes
    POST   /patients/{pid}/clinical/episodes
    GET    /patients/{pid}/clinical/episodes/{id}
    PATCH  /patients/{pid}/clinical/episodes/{id}
    POST   /patients/{pid}/clinical/episodes/{id}/close
    POST   /patients/{pid}/clinical/episodes/{id}/reopen

Guards:
    - `require_role("admin", "doctor", "staff")` for reads (patients use the
      separate patient-portal read path shipped in earlier iterations).
    - `require_role("admin", "doctor")` for writes. Writes additionally call
      `require_reauth` to match the clinical-chart reauth posture applied to
      medical records.

Every read/write emits an audit row (`clinical.*`) and a `clinical_audit_events`
row scoped to the patient chart so Phase 2 "chart history" can be fetched
fast without scanning the global audit stream.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.audit import audit_success
from core.db import get_db_write
from core.deps import require_role
from core.reauth import require_reauth
from core.tenancy import TenantContext, get_tenant_context
from core.tenant_scope import scoped_filter, stamp_for_write
from services.clinical.models import (
    CASE_TYPE,
    ClinicalSectionCount,
    ClinicalSummary,
    EpisodeCaseClose,
    EpisodeCaseCreate,
    EpisodeCasePublic,
    EpisodeCaseUpdate,
    now_iso,
)

router = APIRouter(prefix="/patients", tags=["clinical"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
async def _load_patient(db, patient_id: str, ctx: TenantContext) -> dict:
    """Look up the patient under the caller's tenant/location scope.

    Returns the patient doc (minus PHI) on success; raises 404 otherwise.
    Cross-tenant / cross-location probes always return 404 so we never leak
    patient existence.
    """
    q = scoped_filter({"id": patient_id}, ctx, location_scoped=True)
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    patient = await db.patients.find_one(
        q, {"_id": 0, "id": 1, "location_id": 1, "status": 1}
    )
    if not patient:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


async def _load_episode(db, patient_id: str, episode_id: str, ctx: TenantContext) -> dict:
    q = scoped_filter(
        {"id": episode_id, "patient_id": patient_id}, ctx, location_scoped=False
    )
    if q.get("__deny__"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Episode not found")
    doc = await db.clinical_episode_cases.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Episode not found")
    return doc


def _public(doc: dict, provider_name_map: dict[str, str] | None = None) -> dict:
    """Strip Mongo internals + hydrate the responsible provider's display
    name when we have it. The front-end shell relies on the name being
    present to avoid an extra round-trip per row."""
    out = {k: v for k, v in doc.items() if k not in {"_id", "history"}}
    pid = out.get("responsible_provider_id")
    if pid and provider_name_map:
        out["responsible_provider_name"] = provider_name_map.get(pid)
    return out


async def _log_clinical_event(
    db,
    ctx: TenantContext,
    *,
    actor: dict,
    patient_id: str,
    episode_id: str | None,
    event_type: str,
    entity_type: str,
    entity_id: str | None,
    metadata: dict | None = None,
) -> None:
    """Append a row to `clinical_audit_events` for fast patient-chart history.

    The global `audit_logs` stream is still the system-of-record for compliance;
    this collection is a patient-scoped projection to keep chart-history UI
    snappy in Phase 2 without forcing downstream consumers through the global
    stream's tenant+patient filter path.
    """
    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "episode_id": episode_id,
        "actor_id": actor.get("id"),
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metadata": metadata or {},
        "created_at": now_iso(),
    }
    doc = stamp_for_write(doc, ctx)
    await db.clinical_audit_events.insert_one(doc)


async def _hydrate_providers(db, tenant_id: str, provider_ids: set[str]) -> dict[str, str]:
    ids = [p for p in provider_ids if p]
    if not ids:
        return {}
    cursor = db.users.find(
        {"id": {"$in": ids}, "tenant_id": tenant_id},
        {"_id": 0, "id": 1, "name": 1, "email": 1},
    )
    return {
        u["id"]: (u.get("name") or u.get("email") or u["id"])
        async for u in cursor
    }


# ---------------------------------------------------------------------------
# Summary — aggregated patient-chart snapshot
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/summary",
    response_model=ClinicalSummary,
)
async def get_clinical_summary(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    tenant_q = {"tenant_id": ctx.tenant_id, "patient_id": patient_id}
    ep_total = await db.clinical_episode_cases.count_documents(tenant_q)
    ep_open = await db.clinical_episode_cases.count_documents(
        {**tenant_q, "status": {"$in": ["active", "on_hold"]}}
    )

    dx_total = await db.clinical_diagnoses.count_documents(tenant_q)
    dx_open = await db.clinical_diagnoses.count_documents(
        {**tenant_q, "status": "active"}
    )
    enc_total = await db.clinical_encounters.count_documents(tenant_q)
    enc_open = await db.clinical_encounters.count_documents(
        {**tenant_q, "status": "in_progress"}
    )
    exam_total = await db.clinical_initial_exams.count_documents(tenant_q)
    exam_open = await db.clinical_initial_exams.count_documents(
        {**tenant_q, "status": {"$in": ["draft", "sign_ready"]}}
    )
    history_doc = await db.clinical_history.find_one(tenant_q, {"_id": 0, "id": 1})
    history_present = 1 if history_doc else 0

    await audit_success(
        user, "clinical.summary_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"tenant_id": ctx.tenant_id},
    )

    return {
        "patient_id": patient_id,
        "tenant_id": ctx.tenant_id,
        "episodes": ClinicalSectionCount(total=ep_total, open=ep_open).model_dump(),
        "notes": ClinicalSectionCount().model_dump(),
        "diagnoses": ClinicalSectionCount(total=dx_total, open=dx_open).model_dump(),
        "treatment_plans": ClinicalSectionCount().model_dump(),
        "outcomes": ClinicalSectionCount().model_dump(),
        "media": ClinicalSectionCount().model_dump(),
        "encounter_links": ClinicalSectionCount().model_dump(),
        "encounters": ClinicalSectionCount(total=enc_total, open=enc_open).model_dump(),
        "initial_exams": ClinicalSectionCount(total=exam_total, open=exam_open).model_dump(),
        "history_present": history_present,
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Episode CRUD
# ---------------------------------------------------------------------------
@router.get(
    "/{patient_id}/clinical/episodes",
    response_model=list[EpisodeCasePublic],
)
async def list_episodes(
    patient_id: str,
    request: Request,
    status_in: str | None = Query(
        default=None,
        description="Comma-separated filter (active,on_hold,closed,archived).",
    ),
    case_type: CASE_TYPE | None = Query(default=None),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)

    q: dict = scoped_filter({"patient_id": patient_id}, ctx, location_scoped=False)
    if q.get("__deny__"):
        return []
    if case_type:
        q["case_type"] = case_type
    if status_in:
        statuses = [s.strip() for s in status_in.split(",") if s.strip()]
        if statuses:
            q["status"] = {"$in": statuses}

    cursor = db.clinical_episode_cases.find(q, {"_id": 0}).sort("start_date", -1)
    docs = [d async for d in cursor]

    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {d.get("responsible_provider_id") for d in docs if d.get("responsible_provider_id")},
    )

    await audit_success(
        user, "clinical.episode.list_viewed", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"count": len(docs)},
    )
    return [_public(d, provider_name_map) for d in docs]


@router.post(
    "/{patient_id}/clinical/episodes",
    response_model=EpisodeCasePublic,
    status_code=201,
)
async def create_episode(
    patient_id: str,
    payload: EpisodeCaseCreate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    patient = await _load_patient(db, patient_id, ctx)

    # If a responsible provider was supplied, verify they exist under the
    # same tenant. This keeps cross-tenant provider references from landing
    # on the chart.
    if payload.responsible_provider_id:
        prov = await db.users.find_one(
            {"id": payload.responsible_provider_id, "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1},
        )
        if not prov:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Responsible provider not found in this tenant",
            )

    now = now_iso()
    start_date = payload.start_date or now
    loc_id = payload.location_id or patient.get("location_id")

    doc = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "case_type": payload.case_type,
        "status": "active",
        "title": payload.title.strip(),
        "chief_complaint": payload.chief_complaint,
        "mechanism_of_injury": payload.mechanism_of_injury,
        "onset_date": payload.onset_date,
        "start_date": start_date,
        "end_date": None,
        "closed_reason": None,
        "responsible_provider_id": payload.responsible_provider_id,
        "tags": list(payload.tags or []),
        "metadata": {},
        "created_at": now,
        "updated_at": now,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [{"at": now, "by": user["id"], "action": "created"}],
    }
    doc = stamp_for_write(doc, ctx, location_id=loc_id)

    await db.clinical_episode_cases.insert_one(doc)

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=doc["id"],
        event_type="episode.created", entity_type="episode", entity_id=doc["id"],
        metadata={"case_type": payload.case_type, "title": doc["title"]},
    )
    await audit_success(
        user, "clinical.episode.created", request,
        entity_type="clinical_episode", entity_id=doc["id"], phi_accessed=True,
        metadata={
            "patient_id": patient_id,
            "case_type": payload.case_type,
            "title": doc["title"],
        },
    )

    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {payload.responsible_provider_id} if payload.responsible_provider_id else set(),
    )
    return _public(doc, provider_name_map)


@router.get(
    "/{patient_id}/clinical/episodes/{episode_id}",
    response_model=EpisodeCasePublic,
)
async def get_episode(
    patient_id: str,
    episode_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    doc = await _load_episode(db, patient_id, episode_id, ctx)
    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {doc.get("responsible_provider_id")} if doc.get("responsible_provider_id") else set(),
    )
    await audit_success(
        user, "clinical.episode.read", request,
        entity_type="clinical_episode", entity_id=episode_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )
    return _public(doc, provider_name_map)


@router.patch(
    "/{patient_id}/clinical/episodes/{episode_id}",
    response_model=EpisodeCasePublic,
)
async def update_episode(
    patient_id: str,
    episode_id: str,
    payload: EpisodeCaseUpdate,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_episode(db, patient_id, episode_id, ctx)

    if current["status"] in ("closed", "archived"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Episode is {current['status']}; reopen it before editing",
        )

    dumped = payload.model_dump(exclude_unset=True)
    if not dumped:
        return _public(current)

    if "responsible_provider_id" in dumped and dumped["responsible_provider_id"]:
        prov = await db.users.find_one(
            {"id": dumped["responsible_provider_id"], "tenant_id": ctx.tenant_id},
            {"_id": 0, "id": 1},
        )
        if not prov:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Responsible provider not found in this tenant",
            )

    if "title" in dumped and dumped["title"] is not None:
        dumped["title"] = dumped["title"].strip()

    now = now_iso()
    dumped["updated_at"] = now
    dumped["updated_by"] = user["id"]
    fields_changed = sorted(k for k in dumped.keys() if k not in {"updated_at", "updated_by"})

    await db.clinical_episode_cases.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {
            "$set": dumped,
            "$push": {
                "history": {
                    "at": now,
                    "by": user["id"],
                    "action": "updated",
                    "fields": fields_changed,
                }
            },
        },
    )
    fresh = await db.clinical_episode_cases.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=episode_id,
        event_type="episode.updated", entity_type="episode", entity_id=episode_id,
        metadata={"fields": fields_changed},
    )
    await audit_success(
        user, "clinical.episode.updated", request,
        entity_type="clinical_episode", entity_id=episode_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "fields": fields_changed},
    )

    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {fresh.get("responsible_provider_id")} if fresh.get("responsible_provider_id") else set(),
    )
    return _public(fresh, provider_name_map)


@router.post(
    "/{patient_id}/clinical/episodes/{episode_id}/close",
    response_model=EpisodeCasePublic,
)
async def close_episode(
    patient_id: str,
    episode_id: str,
    payload: EpisodeCaseClose,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_episode(db, patient_id, episode_id, ctx)

    if current["status"] == "closed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Episode is already closed"
        )
    if current["status"] == "archived":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Episode is archived; cannot close"
        )

    now = now_iso()
    updates = {
        "status": "closed",
        "end_date": payload.end_date or now,
        "closed_reason": payload.closed_reason,
        "updated_at": now,
        "updated_by": user["id"],
    }
    await db.clinical_episode_cases.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {
            "$set": updates,
            "$push": {
                "history": {
                    "at": now, "by": user["id"], "action": "closed",
                    "reason": payload.closed_reason,
                }
            },
        },
    )
    fresh = await db.clinical_episode_cases.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=episode_id,
        event_type="episode.closed", entity_type="episode", entity_id=episode_id,
        metadata={"reason": payload.closed_reason},
    )
    await audit_success(
        user, "clinical.episode.closed", request,
        entity_type="clinical_episode", entity_id=episode_id, phi_accessed=True,
        metadata={"patient_id": patient_id, "reason": payload.closed_reason},
    )

    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {fresh.get("responsible_provider_id")} if fresh.get("responsible_provider_id") else set(),
    )
    return _public(fresh, provider_name_map)


@router.post(
    "/{patient_id}/clinical/episodes/{episode_id}/reopen",
    response_model=EpisodeCasePublic,
)
async def reopen_episode(
    patient_id: str,
    episode_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    require_reauth(request, user)

    db = get_db_write()
    await _load_patient(db, patient_id, ctx)
    current = await _load_episode(db, patient_id, episode_id, ctx)

    if current["status"] not in ("closed", "archived"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Episode is {current['status']}; only closed/archived episodes can be reopened",
        )

    now = now_iso()
    await db.clinical_episode_cases.update_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]},
        {
            "$set": {
                "status": "active",
                "end_date": None,
                "closed_reason": None,
                "updated_at": now,
                "updated_by": user["id"],
            },
            "$push": {
                "history": {"at": now, "by": user["id"], "action": "reopened"}
            },
        },
    )
    fresh = await db.clinical_episode_cases.find_one(
        {"id": current["id"], "tenant_id": current["tenant_id"]}, {"_id": 0}
    )

    await _log_clinical_event(
        db, ctx,
        actor=user, patient_id=patient_id, episode_id=episode_id,
        event_type="episode.reopened", entity_type="episode", entity_id=episode_id,
    )
    await audit_success(
        user, "clinical.episode.reopened", request,
        entity_type="clinical_episode", entity_id=episode_id, phi_accessed=True,
        metadata={"patient_id": patient_id},
    )

    provider_name_map = await _hydrate_providers(
        db, ctx.tenant_id,
        {fresh.get("responsible_provider_id")} if fresh.get("responsible_provider_id") else set(),
    )
    return _public(fresh, provider_name_map)
