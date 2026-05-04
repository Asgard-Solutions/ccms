"""AI router — four surfaces + admin settings.

All endpoints require a staff role (admin/doctor/staff) and emit
`audit_logs` rows with `action=ai.*`. Zero PHI is logged; the usage
row captures only model, latency, token counts (when provided),
request_id, and actor_id.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context
from services.ai import cache as ai_cache
from services.ai.client import (
    AI_SETTINGS_COLLECTION, DEFAULT_MODEL, DEFAULT_PROVIDER,
    generate, parse_json_safely,
)
from services.ai.context import format_context_for_prompt, load_patient_context
from services.ai.prompts import (
    CHART_BRIEF_SYSTEM, DRAFT_SECTIONS_SYSTEM,
    PRIOR_SECTIONS_SYSTEM, SINCE_LAST_DIFF_SYSTEM,
)

router = APIRouter(prefix="/ai", tags=["ai"])


async def _chart_brief(
    *, tenant_id: str, patient_id: str, actor: dict,
) -> dict:
    ctx, context_hash = await load_patient_context(
        tenant_id=tenant_id, patient_id=patient_id,
    )
    prompt_text = format_context_for_prompt(ctx)
    result = await generate(
        tenant_id=tenant_id, actor=actor,
        system_prompt=CHART_BRIEF_SYSTEM,
        user_text=prompt_text,
        surface="chart_brief",
        response_format="text",
    )
    await ai_cache.upsert(
        tenant_id=tenant_id, patient_id=patient_id,
        surface="chart_brief", context_hash=context_hash,
        payload=result["text"], actor=actor,
        provider=result["provider"], model=result["model"],
    )
    return {
        "patient_id": patient_id,
        "context_hash": context_hash,
        "brief": result["text"],
        "provider": result["provider"],
        "model": result["model"],
        "cached": False,
    }


@router.get("/chart-brief/{patient_id}")
async def get_chart_brief(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    cached = await ai_cache.get_cached(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        surface="chart_brief",
    )
    _, current_hash = await load_patient_context(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
    )
    if cached and cached.get("context_hash") == current_hash:
        await audit_success(
            user, "ai.chart_brief.read", request,
            entity_type="patient", entity_id=patient_id, phi_accessed=True,
            metadata={"cached": True,
                      "model": cached.get("model")},
        )
        return {
            "patient_id": patient_id,
            "context_hash": current_hash,
            "brief": cached["payload"],
            "provider": cached.get("provider"),
            "model": cached.get("model"),
            "cached": True,
            "generated_at": cached.get("generated_at"),
        }
    # Cache stale → regenerate inline.
    out = await _chart_brief(
        tenant_id=ctx.tenant_id, patient_id=patient_id, actor=user,
    )
    await audit_success(
        user, "ai.chart_brief.generated", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"cached": False, "model": out["model"]},
    )
    return out


@router.post("/chart-brief/{patient_id}/regenerate")
async def regenerate_chart_brief(
    patient_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    out = await _chart_brief(
        tenant_id=ctx.tenant_id, patient_id=patient_id, actor=user,
    )
    await audit_success(
        user, "ai.chart_brief.regenerated", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"model": out["model"]},
    )
    return out


# ---------------------------------------------------------------------------
# Encounter-scoped endpoints — /api/ai/encounters/{note_id}/*
# Accept a `note_id` (clinical_notes.id) so the frontend can pass the
# note it's currently editing; we derive the patient_id from that.
# ---------------------------------------------------------------------------
from core.tenancy import tenant_db  # noqa: E402 — kept near usage


async def _note_to_patient(tenant_id: str, note_id: str) -> dict:
    db = tenant_db(tenant_id)
    note = await db.clinical_follow_up_notes.find_one(
        {"tenant_id": tenant_id, "id": note_id},
        {"_id": 0, "patient_id": 1, "id": 1, "date_of_service": 1},
    )
    if not note:
        raise HTTPException(404, "Note not found")
    return note


@router.get("/encounters/{note_id}/prior-sections")
async def get_prior_sections(
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    note = await _note_to_patient(ctx.tenant_id, note_id)
    patient_id = note["patient_id"]
    # Load context *excluding* the current note so we only surface
    # the prior encounter's summary.
    context, context_hash = await load_patient_context(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        exclude_note_id=note_id,
    )
    if not context.get("notes"):
        return {"note_id": note_id, "patient_id": patient_id,
                "prior_sections": None,
                "reason": "No prior signed encounters."}

    surface_key = f"prior_sections:{note_id}"
    cached = await ai_cache.get_cached(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        surface=surface_key,
    )
    if cached and cached.get("context_hash") == context_hash:
        return {"note_id": note_id, "patient_id": patient_id,
                "prior_sections": cached["payload"],
                "cached": True}
    result = await generate(
        tenant_id=ctx.tenant_id, actor=user,
        system_prompt=PRIOR_SECTIONS_SYSTEM,
        user_text=format_context_for_prompt(context),
        surface="prior_sections", response_format="json",
    )
    parsed = parse_json_safely(result["text"]) or {"raw": result["text"]}
    await ai_cache.upsert(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        surface=surface_key, context_hash=context_hash,
        payload=parsed, actor=user,
        provider=result["provider"], model=result["model"],
    )
    await audit_success(
        user, "ai.prior_sections.generated", request,
        entity_type="clinical_note", entity_id=note_id, phi_accessed=True,
        metadata={"model": result["model"]},
    )
    return {"note_id": note_id, "patient_id": patient_id,
            "prior_sections": parsed, "cached": False}


@router.post("/encounters/{note_id}/draft-sections")
async def draft_sections(
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    note = await _note_to_patient(ctx.tenant_id, note_id)
    patient_id = note["patient_id"]
    context, _ = await load_patient_context(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        exclude_note_id=note_id,
    )
    result = await generate(
        tenant_id=ctx.tenant_id, actor=user,
        system_prompt=DRAFT_SECTIONS_SYSTEM,
        user_text=format_context_for_prompt(context),
        surface="draft_sections", response_format="json",
    )
    parsed = parse_json_safely(result["text"]) or {
        "subjective_draft": "", "plan_draft": "",
        "rationale": "AI returned a non-JSON response; please regenerate.",
    }
    await audit_success(
        user, "ai.draft_sections.generated", request,
        entity_type="clinical_note", entity_id=note_id, phi_accessed=True,
        metadata={"model": result["model"]},
    )
    return {
        "note_id": note_id,
        "patient_id": patient_id,
        "drafts": parsed,
        "provider": result["provider"],
        "model": result["model"],
    }


@router.get("/encounters/{note_id}/since-last-diff")
async def since_last_diff(
    note_id: str,
    request: Request,
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    note = await _note_to_patient(ctx.tenant_id, note_id)
    patient_id = note["patient_id"]
    context, context_hash = await load_patient_context(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        exclude_note_id=note_id,
    )
    surface_key = f"since_last_diff:{note_id}"
    cached = await ai_cache.get_cached(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        surface=surface_key,
    )
    if cached and cached.get("context_hash") == context_hash:
        return {"note_id": note_id, "patient_id": patient_id,
                "diff": cached["payload"], "cached": True}
    result = await generate(
        tenant_id=ctx.tenant_id, actor=user,
        system_prompt=SINCE_LAST_DIFF_SYSTEM,
        user_text=format_context_for_prompt(context),
        surface="since_last_diff", response_format="json",
    )
    parsed = parse_json_safely(result["text"]) or {
        "since_iso": None, "callouts": [],
    }
    await ai_cache.upsert(
        tenant_id=ctx.tenant_id, patient_id=patient_id,
        surface=surface_key, context_hash=context_hash,
        payload=parsed, actor=user,
        provider=result["provider"], model=result["model"],
    )
    await audit_success(
        user, "ai.since_last_diff.generated", request,
        entity_type="clinical_note", entity_id=note_id, phi_accessed=True,
        metadata={"model": result["model"]},
    )
    return {"note_id": note_id, "patient_id": patient_id,
            "diff": parsed, "cached": False}


# ---------------------------------------------------------------------------
# AI settings (admin)
# ---------------------------------------------------------------------------
class _AISettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_provider: str = Field(default=DEFAULT_PROVIDER,
                                pattern=r"^(anthropic|openai|gemini)$")
    model_name: str = Field(default=DEFAULT_MODEL, max_length=120)
    enabled: bool = True


@router.get("/settings")
async def settings_get(
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    from core.db import get_db
    doc = await get_db()[AI_SETTINGS_COLLECTION].find_one(
        {"tenant_id": ctx.tenant_id}, {"_id": 0},
    )
    if not doc:
        return {
            "tenant_id": ctx.tenant_id,
            "model_provider": DEFAULT_PROVIDER,
            "model_name": DEFAULT_MODEL,
            "enabled": True, "configured": False,
        }
    return {**doc, "configured": True}


@router.put("/settings")
async def settings_put(
    request: Request,
    payload: _AISettings = Body(...),
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    from core.db import get_db
    db = get_db()
    from services.ai import now_iso as _now
    doc = {
        "tenant_id": ctx.tenant_id,
        "model_provider": payload.model_provider,
        "model_name": payload.model_name,
        "enabled": payload.enabled,
        "updated_at": _now(),
        "updated_by": user.get("email") or user.get("id"),
    }
    await db[AI_SETTINGS_COLLECTION].update_one(
        {"tenant_id": ctx.tenant_id}, {"$set": doc}, upsert=True,
    )
    await audit_success(
        user, "ai.settings.updated", request,
        entity_type="ai_settings", entity_id=ctx.tenant_id,
        metadata={"model": f"{payload.model_provider}/{payload.model_name}"},
    )
    return {**doc, "configured": True}
