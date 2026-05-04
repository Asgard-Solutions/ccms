"""Smart cache for AI-generated artefacts.

Collection: ``ai_brief_cache``.
    { id, tenant_id, patient_id, surface, context_hash, payload,
      model, provider, generated_at, generated_by }

Surface is one of ``chart_brief | prior_sections | since_last_diff``.
``draft_sections`` is never cached — it runs once per encounter-init
and is the output of a deliberate user action.

Lookup: (tenant_id, patient_id, surface) returns the row; callers check
the stored ``context_hash`` against the current ``load_patient_context``
hash and regenerate when they differ.
"""
from __future__ import annotations

import uuid
from typing import Optional

from core.tenancy import tenant_db
from services.ai import now_iso

COLLECTION = "ai_brief_cache"


async def get_cached(
    *, tenant_id: str, patient_id: str, surface: str,
) -> Optional[dict]:
    return await tenant_db(tenant_id)[COLLECTION].find_one(
        {"tenant_id": tenant_id, "patient_id": patient_id,
         "surface": surface},
        {"_id": 0},
    )


async def upsert(
    *, tenant_id: str, patient_id: str, surface: str,
    context_hash: str, payload: dict | str, actor: dict,
    provider: str, model: str,
) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "surface": surface,
        "context_hash": context_hash,
        "payload": payload,
        "provider": provider,
        "model": model,
        "generated_at": now_iso(),
        "generated_by": actor.get("email") or actor.get("id"),
    }
    await tenant_db(tenant_id)[COLLECTION].update_one(
        {"tenant_id": tenant_id, "patient_id": patient_id,
         "surface": surface},
        {"$set": row, "$setOnInsert": {"created_at": now_iso()}},
        upsert=True,
    )
    return row


async def invalidate(
    *, tenant_id: str, patient_id: str, surface: str | None = None,
) -> int:
    q: dict = {"tenant_id": tenant_id, "patient_id": patient_id}
    if surface:
        q["surface"] = surface
    res = await tenant_db(tenant_id)[COLLECTION].delete_many(q)
    return res.deleted_count
