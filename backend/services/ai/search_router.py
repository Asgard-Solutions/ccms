"""Natural-language semantic search across patient charts.

Doctor / admin / staff scoped. The search runs in two phases:

  1. **Candidate retrieval** (deterministic, cheap) — pulls structured
     snippets from the patient's chart that are likely to match the
     query: signed follow-up notes, initial-exam summaries, treatment
     plans, diagnoses, and recent outcome entries. Bounded so we never
     ship more than ~30 snippets to the LLM.
  2. **LLM ranking** (Claude Sonnet 4.5) — ranks the candidates against
     the query, returns a short answer with snippet citations, and
     drops any snippet below the 0.4 score floor.

Tenant-scoped, audit-logged, never persists query bodies or full
snippets to `ai_usage` (PHI). The LLM call is keyed by the same smart
cache used by the chart-brief, scoped per (tenant_id, patient_id,
query_hash) so an identical doctor question hits the cache.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.audit import audit_success
from core.clinical_collections import (
    DIAGNOSES_COLL, FOLLOW_UP_NOTES_COLL, INITIAL_EXAMS_COLL,
    OUTCOME_ENTRIES_COLL, TREATMENT_PLANS_COLL,
)
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.ai import cache as ai_cache
from services.ai.client import generate, parse_json_safely
from services.ai.prompts import SEMANTIC_SEARCH_SYSTEM

logger = logging.getLogger("ccms.ai.search")

router = APIRouter(prefix="/ai/search", tags=["ai-search"])

MAX_SNIPPETS = 30
MAX_QUERY_LEN = 400
SURFACE = "semantic_search"


def _hash_query(q: str) -> str:
    return hashlib.sha256(q.strip().lower().encode("utf-8")).hexdigest()[:16]


def _shape_followup(n: dict) -> dict:
    soap_parts = []
    for key, label in (
        ("subjective", "S"), ("objective", "O"),
        ("assessment", "A"), ("plan", "P"),
    ):
        section = n.get(key) or {}
        if isinstance(section, dict):
            for f, v in section.items():
                if isinstance(v, str) and v.strip():
                    soap_parts.append(f"{label}.{f}: {v.strip()[:240]}")
    return {
        "kind": "follow_up_note",
        "id": n.get("id"),
        "date": n.get("date_of_service"),
        "text": "\n".join(soap_parts) or n.get("reassessment_summary") or "",
    }


def _shape_exam(n: dict) -> dict:
    parts = []
    for k in ("chief_complaint", "history", "examination", "assessment"):
        v = n.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(f"{k}: {v.strip()[:240]}")
        elif isinstance(v, dict):
            for f, fv in v.items():
                if isinstance(fv, str) and fv.strip():
                    parts.append(f"{k}.{f}: {fv.strip()[:240]}")
    return {
        "kind": "initial_exam",
        "id": n.get("id"),
        "date": n.get("date_of_service") or n.get("created_at"),
        "text": "\n".join(parts),
    }


def _shape_dx(d: dict) -> dict:
    return {
        "kind": "diagnosis",
        "id": d.get("id"),
        "date": d.get("created_at"),
        "text": f"{d.get('icd10_code', '')} — {d.get('label', '')} "
                f"({'active' if d.get('is_active') else 'resolved'})",
    }


def _shape_plan(p: dict) -> dict:
    return {
        "kind": "treatment_plan",
        "id": p.get("id"),
        "date": p.get("created_at"),
        "text": (p.get("narrative") or "")[:600],
    }


def _shape_outcome(o: dict) -> dict:
    return {
        "kind": "outcome_entry",
        "id": o.get("id"),
        "date": o.get("recorded_at") or o.get("created_at"),
        "text": (
            f"{o.get('measure', '')}: {o.get('value', '')} "
            f"{o.get('unit', '')}".strip()
        ),
    }


async def _gather_snippets(tenant_id: str, patient_id: str) -> list[dict]:
    db = tenant_db(tenant_id)
    base = {"tenant_id": tenant_id, "patient_id": patient_id}

    snippets: list[dict] = []
    # Recent signed follow-ups (up to 8)
    cur = db[FOLLOW_UP_NOTES_COLL].find(
        {**base, "status": {"$in": ["signed", "locked"]}}, {"_id": 0},
    ).sort("date_of_service", -1).limit(8)
    snippets.extend([_shape_followup(n) async for n in cur])
    # Initial exams (up to 2)
    cur = db[INITIAL_EXAMS_COLL].find(base, {"_id": 0}).sort("created_at", -1).limit(2)
    snippets.extend([_shape_exam(n) async for n in cur])
    # Active + resolved diagnoses (up to 8)
    cur = db[DIAGNOSES_COLL].find(base, {"_id": 0}).sort("created_at", -1).limit(8)
    snippets.extend([_shape_dx(d) async for d in cur])
    # Treatment plans (up to 3)
    cur = db[TREATMENT_PLANS_COLL].find(base, {"_id": 0}).sort("created_at", -1).limit(3)
    snippets.extend([_shape_plan(p) async for p in cur])
    # Outcome entries (up to 9)
    cur = db[OUTCOME_ENTRIES_COLL].find(base, {"_id": 0}).sort("recorded_at", -1).limit(9)
    snippets.extend([_shape_outcome(o) async for o in cur])

    # Drop snippets with no usable text
    snippets = [s for s in snippets if (s.get("text") or "").strip()]
    return snippets[:MAX_SNIPPETS]


def _format_snippets_for_prompt(snippets: list[dict]) -> str:
    lines: list[str] = []
    for i, s in enumerate(snippets, 1):
        sid = f"s{i}"
        s["snippet_id"] = sid
        date = s.get("date") or "—"
        lines.append(
            f"[{sid}] ({s['kind']}, {date})\n{s['text']}"
        )
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
class _SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str = Field(min_length=1)
    query: str = Field(min_length=2, max_length=MAX_QUERY_LEN)


@router.post("")
async def semantic_search(
    request: Request,
    body: _SearchRequest = Body(...),
    user: dict = Depends(require_role("admin", "doctor", "staff")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    query = body.query.strip()
    qhash = _hash_query(query)

    cached = await ai_cache.get_cached(
        tenant_id=ctx.tenant_id, patient_id=body.patient_id, surface=SURFACE,
    )
    if cached and cached.get("context_hash") == qhash:
        payload = cached["payload"]
        return {
            "query": query,
            "patient_id": body.patient_id,
            "answer": payload.get("answer", ""),
            "results": payload.get("results", []),
            "cached": True,
        }

    snippets = await _gather_snippets(ctx.tenant_id, body.patient_id)
    if not snippets:
        return {
            "query": query, "patient_id": body.patient_id,
            "answer": "Not documented in the available chart records.",
            "results": [], "cached": False,
        }

    prompt_body = (
        f"# Doctor's question\n{query}\n\n"
        f"# Candidate snippets\n{_format_snippets_for_prompt(snippets)}"
    )
    try:
        result = await generate(
            tenant_id=ctx.tenant_id, actor=user,
            system_prompt=SEMANTIC_SEARCH_SYSTEM,
            user_text=prompt_body,
            surface=SURFACE,
            response_format="json",
            max_tokens=1200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("semantic search failed: %s", str(exc)[:200])
        raise HTTPException(502, "AI search failed")

    parsed: dict[str, Any] = parse_json_safely(result["text"]) or {}
    answer = (parsed.get("answer") or "").strip()
    raw_results = parsed.get("results") or []

    by_id = {s["snippet_id"]: s for s in snippets}
    enriched: list[dict] = []
    for r in raw_results:
        sid = r.get("snippet_id")
        score = float(r.get("score") or 0)
        if score < 0.4 or sid not in by_id:
            continue
        s = by_id[sid]
        enriched.append({
            "snippet_id": sid,
            "kind": s.get("kind"),
            "id": s.get("id"),
            "date": s.get("date"),
            "text": s.get("text"),
            "score": round(score, 3),
            "reason": (r.get("reason") or "").strip(),
        })
    enriched.sort(key=lambda x: x["score"], reverse=True)

    payload = {"answer": answer, "results": enriched}
    await ai_cache.upsert(
        tenant_id=ctx.tenant_id, patient_id=body.patient_id,
        surface=SURFACE, context_hash=qhash, payload=payload, actor=user,
        provider=result["provider"], model=result["model"],
    )
    await audit_success(
        user, "ai.search.queried", request,
        entity_type="patient", entity_id=body.patient_id, phi_accessed=True,
        metadata={
            "model": result["model"],
            "candidates": len(snippets), "results": len(enriched),
            "query_hash": qhash,
        },
    )
    return {
        "query": query,
        "patient_id": body.patient_id,
        "answer": answer,
        "results": enriched,
        "model": result["model"],
        "cached": False,
    }
