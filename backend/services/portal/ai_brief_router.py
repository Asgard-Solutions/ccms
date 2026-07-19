"""Patient-facing visit-brief endpoint.

Renders a friendly, plain-language preview the patient sees in their
portal before an upcoming visit. Reuses the staff-side AI context
loader and smart-cache, but with:

  * a different system prompt (`PATIENT_VISIT_BRIEF_SYSTEM`) that strips
    PHI such as medication names and ICD codes from the output and
    speaks to the patient in second person.
  * a separate cache surface (`patient_visit_brief`) so a patient
    regenerating their brief never invalidates the clinician's
    chart-prep cache (or vice-versa).

Auth: `role=patient` only. The patient_id is derived from the linked
users row (`linked_patient_id`) — patients never pass IDs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from core.audit import audit_success
from core.deps import get_current_user
from services.ai import cache as ai_cache
from services.ai.client import generate, parse_json_safely
from services.ai.context import format_context_for_prompt, load_patient_context
from services.ai.prompts import PATIENT_VISIT_BRIEF_SYSTEM

router = APIRouter(prefix="/portal", tags=["portal-ai-brief"])

SURFACE = "patient_visit_brief"


def _empty_brief() -> dict:
    return {
        "headline": "Welcome back — here's a quick look at your upcoming visit.",
        "last_visit": "",
        "your_progress": "",
        "this_visit": (
            "Your provider will spend a few minutes catching up with you "
            "and tailor today's care to how you're feeling."
        ),
        "ask_about": [],
        "reminders": [
            "Arrive about 5 minutes early so we can get you settled.",
        ],
    }


async def _require_patient(user: dict) -> tuple[str, str]:
    if user.get("role") != "patient":
        raise HTTPException(403, "Patient role required")
    tenant_id = user.get("tenant_id")
    patient_id = user.get("linked_patient_id")
    if not patient_id:
        # Fallback for password-authed patient users that pre-date the
        # portal-OTP flow (which is what stamps `linked_patient_id`).
        # Resolve the patient row via `patients.user_id == user.id`.
        from core.tenancy import tenant_db
        row = await tenant_db(tenant_id).patients.find_one(
            {"user_id": user.get("id")}, {"_id": 0, "id": 1},
        )
        patient_id = row["id"] if row else None
    if not tenant_id or not patient_id:
        raise HTTPException(403, "Portal session not bound to a patient")
    return tenant_id, patient_id


@router.get("/visit-brief")
async def get_visit_brief(
    request: Request,
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_patient(user)

    context, context_hash = await load_patient_context(
        tenant_id=tenant_id, patient_id=patient_id,
    )

    cached = await ai_cache.get_cached(
        tenant_id=tenant_id, patient_id=patient_id, surface=SURFACE,
    )
    if cached and cached.get("context_hash") == context_hash:
        return {
            "patient_id": patient_id,
            "context_hash": context_hash,
            "brief": cached["payload"],
            "model": cached.get("model"),
            "generated_at": cached.get("generated_at"),
            "cached": True,
        }

    try:
        result = await generate(
            tenant_id=tenant_id, actor=user,
            system_prompt=PATIENT_VISIT_BRIEF_SYSTEM,
            user_text=format_context_for_prompt(context),
            surface=SURFACE,
            response_format="json",
        )
    except Exception:  # noqa: BLE001
        # Patient-facing fallback — never hard-fail the portal.
        return {
            "patient_id": patient_id,
            "context_hash": context_hash,
            "brief": _empty_brief(),
            "cached": False,
            "fallback": True,
        }

    parsed = parse_json_safely(result["text"]) or _empty_brief()
    # Defensive: ensure the shape never breaks the UI.
    parsed.setdefault("headline", _empty_brief()["headline"])
    parsed.setdefault("last_visit", "")
    parsed.setdefault("your_progress", "")
    parsed.setdefault("this_visit", _empty_brief()["this_visit"])
    parsed.setdefault("ask_about", [])
    parsed.setdefault("reminders", _empty_brief()["reminders"])

    await ai_cache.upsert(
        tenant_id=tenant_id, patient_id=patient_id,
        surface=SURFACE, context_hash=context_hash,
        payload=parsed, actor=user,
        provider=result["provider"], model=result["model"],
    )
    await audit_success(
        user, "portal.visit_brief.generated", request,
        entity_type="patient", entity_id=patient_id, phi_accessed=True,
        metadata={"model": result["model"], "surface": SURFACE},
    )
    return {
        "patient_id": patient_id,
        "context_hash": context_hash,
        "brief": parsed,
        "provider": result["provider"],
        "model": result["model"],
        "cached": False,
    }


@router.post("/visit-brief/regenerate")
async def regenerate_visit_brief(
    request: Request,
    user: dict = Depends(get_current_user),
):
    tenant_id, patient_id = await _require_patient(user)
    await ai_cache.invalidate(
        tenant_id=tenant_id, patient_id=patient_id, surface=SURFACE,
    )
    return await get_visit_brief(request, user)
