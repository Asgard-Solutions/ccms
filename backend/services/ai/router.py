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
from core.clinical_collections import FOLLOW_UP_NOTES_COLL  # noqa: E402


async def _note_to_patient(tenant_id: str, note_id: str) -> dict:
    db = tenant_db(tenant_id)
    note = await db[FOLLOW_UP_NOTES_COLL].find_one(
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


# ---------------------------------------------------------------------------
# SOAP-template overrides — per location and/or per provider.
# Stored in `ai_template_overrides` keyed on (tenant_id, scope_type,
# scope_id). Resolution order at runtime: provider → location → tenant
# default. Empty / unset == fall back to system prompt.
# ---------------------------------------------------------------------------
TEMPLATE_OVERRIDES_COLL = "ai_template_overrides"


class _TemplateOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope_type: str = Field(pattern=r"^(tenant|location|provider)$")
    scope_id: str | None = None  # None ⇒ tenant-level default
    surface: str = Field(
        default="scribe_soap",
        pattern=r"^(scribe_soap|chart_brief|prior_sections|draft_sections)$",
    )
    instructions: str = Field(default="", max_length=4000)
    enabled: bool = True


@router.get("/templates")
async def list_templates(
    user: dict = Depends(require_role("admin", "doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    from core.db import get_db
    cur = get_db()[TEMPLATE_OVERRIDES_COLL].find(
        {"tenant_id": ctx.tenant_id}, {"_id": 0},
    ).sort([("scope_type", 1), ("scope_id", 1)])
    rows = [r async for r in cur]
    return {"templates": rows}


@router.put("/templates")
async def upsert_template(
    request: Request,
    payload: _TemplateOverride = Body(...),
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    from core.db import get_db
    from services.ai import now_iso as _now
    if payload.scope_type != "tenant" and not payload.scope_id:
        raise HTTPException(400, "scope_id required for location/provider scope")
    if payload.scope_type == "tenant":
        scope_id = ctx.tenant_id
    else:
        scope_id = payload.scope_id
    doc = {
        "tenant_id": ctx.tenant_id,
        "scope_type": payload.scope_type,
        "scope_id": scope_id,
        "surface": payload.surface,
        "instructions": payload.instructions,
        "enabled": payload.enabled,
        "updated_at": _now(),
        "updated_by": user.get("email") or user.get("id"),
    }
    await get_db()[TEMPLATE_OVERRIDES_COLL].update_one(
        {
            "tenant_id": ctx.tenant_id, "scope_type": payload.scope_type,
            "scope_id": scope_id, "surface": payload.surface,
        },
        {"$set": doc, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )
    await audit_success(
        user, "ai.template.upserted", request,
        entity_type="ai_template_override",
        entity_id=f"{payload.scope_type}:{scope_id}:{payload.surface}",
        metadata={"surface": payload.surface, "scope": payload.scope_type},
    )
    return {**doc}


@router.delete("/templates")
async def delete_template(
    request: Request,
    scope_type: str,
    scope_id: str,
    surface: str = "scribe_soap",
    user: dict = Depends(require_role("admin")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    from core.db import get_db
    res = await get_db()[TEMPLATE_OVERRIDES_COLL].delete_one({
        "tenant_id": ctx.tenant_id,
        "scope_type": scope_type, "scope_id": scope_id, "surface": surface,
    })
    if not res.deleted_count:
        raise HTTPException(404, "Template not found")
    await audit_success(
        user, "ai.template.deleted", request,
        entity_type="ai_template_override",
        entity_id=f"{scope_type}:{scope_id}:{surface}",
    )
    return {"deleted": True}


async def resolve_template_instructions(
    *, tenant_id: str, surface: str,
    location_id: str | None = None, provider_id: str | None = None,
) -> str:
    """Resolve the most-specific template override for a given context.
    Returns the merged instruction text (provider → location → tenant)
    or empty string if no overrides exist.
    """
    from core.db import get_db
    db = get_db()
    parts: list[str] = []
    # Tenant default
    base = await db[TEMPLATE_OVERRIDES_COLL].find_one(
        {
            "tenant_id": tenant_id, "scope_type": "tenant",
            "surface": surface, "enabled": True,
        },
        {"_id": 0, "instructions": 1},
    )
    if base and (base.get("instructions") or "").strip():
        parts.append(base["instructions"].strip())
    # Location override
    if location_id:
        loc = await db[TEMPLATE_OVERRIDES_COLL].find_one(
            {
                "tenant_id": tenant_id, "scope_type": "location",
                "scope_id": location_id, "surface": surface, "enabled": True,
            },
            {"_id": 0, "instructions": 1},
        )
        if loc and (loc.get("instructions") or "").strip():
            parts.append(loc["instructions"].strip())
    # Provider override (most specific — wins last)
    if provider_id:
        prov = await db[TEMPLATE_OVERRIDES_COLL].find_one(
            {
                "tenant_id": tenant_id, "scope_type": "provider",
                "scope_id": provider_id, "surface": surface, "enabled": True,
            },
            {"_id": 0, "instructions": 1},
        )
        if prov and (prov.get("instructions") or "").strip():
            parts.append(prov["instructions"].strip())
    return "\n\n".join(parts).strip()
