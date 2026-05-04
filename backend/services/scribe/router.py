"""AI scribe + SOAP-generation surface.

Two narrow capabilities live here:

1. **Voice-to-text** — `POST /api/scribe/audio` accepts a multipart audio
   chunk recorded in the encounter editor (any of mp3, mp4, m4a, mpeg,
   mpga, wav, webm; ≤25 MB per chunk per OpenAI Whisper limit). The
   audio is uploaded to Emergent object storage and immediately
   transcribed via Whisper. Both the storage path and the transcript
   are persisted on the `scribe_audio` collection.

2. **SOAP draft** — `POST /api/scribe/encounters/{note_type}/{note_id}/soap/draft`
   takes the concatenated transcripts (plus an optional free-text
   addendum) and asks Claude Sonnet 4.5 to output structured S/O/A/P
   JSON the editor can apply per-section or in bulk.

Audio retention follows the user's chosen policy: keep until the host
note is signed, then auto-delete (handled by the sign endpoints calling
`delete_audio_for_note()`).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Literal

from fastapi import (
    APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile,
)
from pydantic import BaseModel

from core import object_storage
from core.audit import audit_success
from core.clinical_collections import NOTE_TYPE_TO_COLL
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.ai.client import generate, parse_json_safely
from services.ai.prompts import CODING_SUGGEST_SYSTEM
from services.ai.router import resolve_template_instructions
from services.scribe.prompts import SCRIBE_SOAP_SYSTEM
from services.scribe.transcribe import transcribe_audio_bytes

logger = logging.getLogger("ccms.scribe")

router = APIRouter(prefix="/scribe", tags=["scribe"])

NoteType = Literal["follow_up", "initial_exam", "reexam"]
COLLECTION = "scribe_audio"
MAX_BYTES = 25 * 1024 * 1024  # Whisper's hard cap
ALLOWED_MIMES = {
    "audio/mp3", "audio/mpeg", "audio/mpga", "audio/mp4",
    "audio/m4a", "audio/x-m4a", "audio/wav", "audio/x-wav",
    "audio/webm", "video/webm",  # Chrome MediaRecorder labels webm/audio as video/webm
}
NOTE_COLLECTIONS = NOTE_TYPE_TO_COLL


def _ext_for(mime: str) -> str:
    return {
        "audio/mp3": "mp3", "audio/mpeg": "mp3", "audio/mpga": "mp3",
        "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a",
        "audio/wav": "wav", "audio/x-wav": "wav",
        "audio/webm": "webm", "video/webm": "webm",
    }.get(mime, "bin")


async def _resolve_note(
    tenant_id: str, note_type: NoteType, note_id: str,
) -> dict:
    coll = NOTE_COLLECTIONS.get(note_type)
    if not coll:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    note = await tenant_db(tenant_id)[coll].find_one(
        {"tenant_id": tenant_id, "id": note_id},
        {"_id": 0, "id": 1, "patient_id": 1, "status": 1},
    )
    if not note:
        raise HTTPException(404, f"{note_type} not found")
    if note.get("status") == "signed":
        raise HTTPException(409, "Cannot scribe to a signed note")
    return note


# ---------------------------------------------------------------------------
# POST /scribe/audio  — upload + transcribe
# ---------------------------------------------------------------------------
@router.post("/audio")
async def upload_audio_chunk(
    request: Request,
    note_id: str = Form(...),
    note_type: str = Form(...),
    audio: UploadFile = File(...),
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    if note_type not in NOTE_COLLECTIONS:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    note = await _resolve_note(ctx.tenant_id, note_type, note_id)

    declared = (audio.content_type or "").lower().split(";")[0].strip()
    if declared not in ALLOWED_MIMES:
        raise HTTPException(400, f"Unsupported audio type `{declared}`")

    payload = await audio.read()
    size = len(payload)
    if size == 0:
        raise HTTPException(400, "Empty audio payload")
    if size > MAX_BYTES:
        raise HTTPException(
            413,
            f"Audio chunk exceeds 25 MB Whisper limit (got {size} bytes). "
            "Stop and start a new chunk.",
        )

    audio_id = str(uuid.uuid4())
    ext = _ext_for(declared)
    storage_path = object_storage.storage_path_for(
        ctx.tenant_id or "default", note["patient_id"], audio_id, ext,
    )
    try:
        object_storage.put_object(storage_path, payload, declared)
    except object_storage.StorageUnavailable as exc:
        logger.error("scribe storage unavailable: %s", exc)
        raise HTTPException(503, "Audio storage unavailable")

    # Transcribe synchronously — Whisper is fast enough at ≤25 MB.
    transcript_text = ""
    transcribe_status = "pending"
    transcribe_error: str | None = None
    try:
        transcript_text = await transcribe_audio_bytes(
            payload=payload, filename=f"{audio_id}.{ext}",
        )
        transcribe_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("scribe transcribe failed: %s", str(exc)[:200])
        transcribe_status = "error"
        transcribe_error = str(exc)[:300]

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": audio_id,
        "tenant_id": ctx.tenant_id,
        "patient_id": note["patient_id"],
        "note_id": note_id,
        "note_type": note_type,
        "storage_path": storage_path,
        "mime_type": declared,
        "size_bytes": size,
        "transcript": transcript_text,
        "transcribe_status": transcribe_status,
        "transcribe_error": transcribe_error,
        "is_deleted": False,
        "created_at": now,
        "created_by": user["id"],
    }
    await tenant_db(ctx.tenant_id)[COLLECTION].insert_one(doc)
    await audit_success(
        user, "scribe.audio.uploaded", request,
        entity_type="scribe_audio", entity_id=audio_id, phi_accessed=True,
        metadata={
            "note_id": note_id, "note_type": note_type,
            "size": size, "status": transcribe_status,
        },
    )
    return {
        "audio_id": audio_id,
        "note_id": note_id,
        "note_type": note_type,
        "transcript": transcript_text,
        "transcribe_status": transcribe_status,
        "transcribe_error": transcribe_error,
        "size_bytes": size,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# GET /scribe/encounters/{note_type}/{note_id}/audio  — list chunks
# ---------------------------------------------------------------------------
@router.get("/encounters/{note_type}/{note_id}/audio")
async def list_audio(
    note_type: str,
    note_id: str,
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    if note_type not in NOTE_COLLECTIONS:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    cur = tenant_db(ctx.tenant_id)[COLLECTION].find(
        {
            "tenant_id": ctx.tenant_id,
            "note_id": note_id, "note_type": note_type,
            "is_deleted": False,
        },
        {"_id": 0, "storage_path": 0},
    ).sort("created_at", 1)
    rows = [r async for r in cur]
    full_transcript = "\n\n".join(
        (r.get("transcript") or "").strip()
        for r in rows
        if r.get("transcribe_status") == "ok" and r.get("transcript")
    )
    return {
        "note_id": note_id, "note_type": note_type,
        "chunks": rows,
        "full_transcript": full_transcript,
    }


# ---------------------------------------------------------------------------
# DELETE /scribe/audio/{audio_id}  — explicit delete (soft)
# ---------------------------------------------------------------------------
@router.delete("/audio/{audio_id}")
async def delete_audio(
    audio_id: str,
    request: Request,
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    db = tenant_db(ctx.tenant_id)
    row = await db[COLLECTION].find_one(
        {"tenant_id": ctx.tenant_id, "id": audio_id, "is_deleted": False},
        {"_id": 0, "id": 1, "note_id": 1},
    )
    if not row:
        raise HTTPException(404, "Audio not found")
    now = datetime.now(timezone.utc).isoformat()
    await db[COLLECTION].update_one(
        {"id": audio_id, "tenant_id": ctx.tenant_id},
        {"$set": {
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": user["id"],
        }},
    )
    await audit_success(
        user, "scribe.audio.deleted", request,
        entity_type="scribe_audio", entity_id=audio_id, phi_accessed=True,
        metadata={"note_id": row.get("note_id")},
    )
    return {"deleted": True, "id": audio_id}


# ---------------------------------------------------------------------------
# POST /scribe/encounters/{note_type}/{note_id}/soap/draft
# ---------------------------------------------------------------------------
class SoapDraftRequest(BaseModel):
    transcript: str | None = None
    addendum: str | None = None


@router.post("/encounters/{note_type}/{note_id}/soap/draft")
async def draft_soap_from_scribe(
    note_type: str,
    note_id: str,
    request: Request,
    body: SoapDraftRequest = Body(default_factory=SoapDraftRequest),
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    if note_type not in NOTE_COLLECTIONS:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    note = await _resolve_note(ctx.tenant_id, note_type, note_id)

    transcript = (body.transcript or "").strip()
    if not transcript:
        # Fall back to whatever transcripts we already have on file.
        cur = tenant_db(ctx.tenant_id)[COLLECTION].find(
            {
                "tenant_id": ctx.tenant_id,
                "note_id": note_id, "note_type": note_type,
                "is_deleted": False, "transcribe_status": "ok",
            },
            {"_id": 0, "transcript": 1, "created_at": 1},
        ).sort("created_at", 1)
        chunks = [c async for c in cur]
        transcript = "\n\n".join(
            (c.get("transcript") or "").strip() for c in chunks
        ).strip()

    if not transcript and not (body.addendum or "").strip():
        raise HTTPException(
            422,
            "No transcript or addendum supplied — record audio or write a "
            "free-text note before drafting SOAP.",
        )

    user_text_parts = [f"Note type: {note_type}", f"Patient ID: {note['patient_id']}"]
    if transcript:
        user_text_parts.append(f"\n# Visit transcript\n{transcript}")
    if body.addendum and body.addendum.strip():
        user_text_parts.append(f"\n# Doctor's free-text addendum\n{body.addendum.strip()}")
    user_text = "\n\n".join(user_text_parts)

    # Honour SOAP-template overrides per location/provider/tenant.
    note_full = await tenant_db(ctx.tenant_id)[NOTE_TYPE_TO_COLL[note_type]].find_one(
        {"tenant_id": ctx.tenant_id, "id": note_id},
        {"_id": 0, "location_id": 1, "provider_id": 1},
    ) or {}
    extra = await resolve_template_instructions(
        tenant_id=ctx.tenant_id, surface="scribe_soap",
        location_id=note_full.get("location_id"),
        provider_id=note_full.get("provider_id"),
    )
    system_prompt = (
        SCRIBE_SOAP_SYSTEM + "\n\n# Clinic-specific overrides\n" + extra
        if extra else SCRIBE_SOAP_SYSTEM
    )

    try:
        result = await generate(
            tenant_id=ctx.tenant_id, actor=user,
            system_prompt=system_prompt,
            user_text=user_text,
            surface="scribe_soap_draft",
            response_format="json",
            max_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scribe soap draft failed: %s", str(exc)[:200])
        raise HTTPException(502, "AI draft generation failed")

    parsed = parse_json_safely(result["text"]) or {}
    drafts = {
        "subjective": (parsed.get("subjective") or "").strip(),
        "objective": (parsed.get("objective") or "").strip(),
        "assessment": (parsed.get("assessment") or "").strip(),
        "plan": (parsed.get("plan") or "").strip(),
    }
    rationale = (parsed.get("rationale") or "").strip()

    await audit_success(
        user, "scribe.soap.drafted", request,
        entity_type=f"clinical_{note_type}_note", entity_id=note_id,
        phi_accessed=True,
        metadata={
            "model": result["model"],
            "transcript_chars": len(transcript),
            "addendum_chars": len((body.addendum or "").strip()),
        },
    )
    return {
        "note_id": note_id,
        "note_type": note_type,
        "drafts": drafts,
        "rationale": rationale,
        "model": result["model"],
        "provider": result["provider"],
    }


# ---------------------------------------------------------------------------
# POST /scribe/encounters/{note_type}/{note_id}/coding-suggest
# Inline CPT/ICD coding hints based on the SOAP draft. Closes the
# documentation→billing loop so the doctor can adjust diagnoses /
# procedures right after applying a draft, before the readiness check.
# ---------------------------------------------------------------------------
class _CodingSuggestRequest(BaseModel):
    drafts: dict | None = None  # {subjective,objective,assessment,plan}
    addendum: str | None = None


@router.post("/encounters/{note_type}/{note_id}/coding-suggest")
async def suggest_codes(
    note_type: str,
    note_id: str,
    request: Request,
    body: _CodingSuggestRequest = Body(default_factory=_CodingSuggestRequest),
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    if note_type not in NOTE_TYPE_TO_COLL:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    note = await _resolve_note(ctx.tenant_id, note_type, note_id)

    drafts = body.drafts or {}
    soap_text_parts = []
    for k in ("subjective", "objective", "assessment", "plan"):
        v = (drafts.get(k) or "").strip()
        if v:
            soap_text_parts.append(f"## {k.title()}\n{v}")
    if body.addendum and body.addendum.strip():
        soap_text_parts.append(f"## Doctor's addendum\n{body.addendum.strip()}")
    if not soap_text_parts:
        raise HTTPException(
            422, "Provide drafts (S/O/A/P) or an addendum to suggest codes from.",
        )

    # Pull the active diagnosis list so the model can prefer existing
    # ICD-10s over newly invented ones.
    db = tenant_db(ctx.tenant_id)
    dx_cur = db.clinical_diagnoses.find(
        {
            "tenant_id": ctx.tenant_id,
            "patient_id": note["patient_id"],
            "is_active": True,
        },
        {"_id": 0, "icd10_code": 1, "label": 1},
    ).sort("created_at", -1).limit(10)
    active_dx = [d async for d in dx_cur]
    dx_block = (
        "## Active diagnoses on file\n"
        + "\n".join(f"- {d.get('icd10_code', '')}: {d.get('label', '')}" for d in active_dx)
        if active_dx else ""
    )

    user_text = (
        f"Note type: {note_type}\n"
        f"Patient ID: {note['patient_id']}\n\n"
        + "\n\n".join(soap_text_parts)
        + (f"\n\n{dx_block}" if dx_block else "")
    )

    try:
        result = await generate(
            tenant_id=ctx.tenant_id, actor=user,
            system_prompt=CODING_SUGGEST_SYSTEM,
            user_text=user_text,
            surface="scribe_coding_suggest",
            response_format="json",
            max_tokens=1500,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("coding-suggest failed: %s", str(exc)[:200])
        raise HTTPException(502, "AI coding suggestion failed")

    parsed = parse_json_safely(result["text"]) or {}
    cpt = parsed.get("cpt_suggestions") or []
    icd = parsed.get("icd_suggestions") or []
    warnings = parsed.get("documentation_warnings") or []

    await audit_success(
        user, "scribe.coding.suggested", request,
        entity_type=f"clinical_{note_type}_note", entity_id=note_id,
        phi_accessed=True,
        metadata={
            "model": result["model"],
            "cpt_count": len(cpt), "icd_count": len(icd),
            "warning_count": len(warnings),
        },
    )
    return {
        "note_id": note_id,
        "note_type": note_type,
        "cpt_suggestions": cpt,
        "icd_suggestions": icd,
        "documentation_warnings": warnings,
        "active_diagnoses": active_dx,
        "model": result["model"],
        "provider": result["provider"],
    }


# ---------------------------------------------------------------------------
# Helper called by the clinical sign endpoints so audio auto-deletes
# when the host note is signed.
# ---------------------------------------------------------------------------
async def delete_audio_for_note(
    *, tenant_id: str, note_id: str, note_type: str, actor_id: str | None = None,
) -> int:
    if note_type not in NOTE_COLLECTIONS:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    res = await tenant_db(tenant_id)[COLLECTION].update_many(
        {
            "tenant_id": tenant_id,
            "note_id": note_id, "note_type": note_type,
            "is_deleted": False,
        },
        {"$set": {
            "is_deleted": True,
            "deleted_at": now,
            "deleted_by": actor_id,
            "deleted_reason": "note_signed",
        }},
    )
    return res.modified_count



# ---------------------------------------------------------------------------
# POST /scribe/encounters/{note_type}/{note_id}/send-to-claim
# Closes the documentation→billing loop. Takes the doctor-accepted
# CPT/ICD suggestions and creates a `draft` claim. New ICD-10s are
# materialised into `clinical_diagnoses` so downstream readiness +
# scrubber + 837P emission see them. CPT codes flow straight onto
# claim lines.
# ---------------------------------------------------------------------------
class _AcceptedCPT(BaseModel):
    code: str
    units: int = 1
    modifiers: list[str] = []
    billed_cents: int = 0


class _AcceptedICD(BaseModel):
    code: str
    label: str | None = None
    is_primary: bool = False


class _SendToClaimRequest(BaseModel):
    cpt: list[_AcceptedCPT]
    icd: list[_AcceptedICD]
    payer_id: str
    policy_id: str | None = None
    place_of_service: str = "11"


@router.post("/encounters/{note_type}/{note_id}/send-to-claim")
async def send_to_claim(
    note_type: str,
    note_id: str,
    request: Request,
    body: _SendToClaimRequest = Body(...),
    user: dict = Depends(require_role("doctor")),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Materialise a draft claim from doctor-accepted CPT/ICD suggestions.

    Bypasses the normal readiness gate because the doctor is explicitly
    authoring this claim from the AI scribe — readiness is about
    *documentation* completeness, and we have a real SOAP draft sitting
    on the host note. The claim still goes to `draft` status so the
    billing scrubber + manual review run before submission.
    """
    if note_type not in NOTE_TYPE_TO_COLL:
        raise HTTPException(400, f"Unsupported note_type `{note_type}`")
    if not body.cpt:
        raise HTTPException(422, "At least one accepted CPT code is required")
    if not body.icd:
        raise HTTPException(422, "At least one accepted ICD-10 code is required")

    db = tenant_db(ctx.tenant_id)
    coll = NOTE_TYPE_TO_COLL[note_type]
    note = await db[coll].find_one(
        {"tenant_id": ctx.tenant_id, "id": note_id},
        {
            "_id": 0, "id": 1, "patient_id": 1, "encounter_id": 1,
            "episode_id": 1, "date_of_service": 1, "location_id": 1,
            "provider_id": 1,
        },
    )
    if not note:
        raise HTTPException(404, f"{note_type} not found")

    # Validate payer + optional policy in tenant.
    payer = await db.billing_payers.find_one(
        {"tenant_id": ctx.tenant_id, "id": body.payer_id}, {"_id": 0, "id": 1},
    )
    if not payer:
        raise HTTPException(404, "Payer not found")
    if body.policy_id:
        pol = await db.patient_insurance_policies.find_one(
            {
                "tenant_id": ctx.tenant_id, "id": body.policy_id,
                "patient_id": note["patient_id"],
            },
            {"_id": 0, "id": 1},
        )
        if not pol:
            raise HTTPException(404, "Policy not found for this patient")

    dos_full = note.get("date_of_service")
    if not dos_full:
        raise HTTPException(409, "Note has no date_of_service")
    dos = dos_full[:10]  # YYYY-MM-DD

    # Materialise any ICD-10 codes that don't already exist on the
    # patient's active diagnosis list for this episode. We only insert
    # what's missing — existing diagnoses stay untouched.
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_dx: set[str] = set()
    primary_seen = False
    diagnoses_payload: list[dict] = []
    for d in body.icd:
        code = (d.code or "").strip().upper()
        if not code or code in seen_dx:
            continue
        seen_dx.add(code)
        is_primary = bool(d.is_primary and not primary_seen)
        if is_primary:
            primary_seen = True
        # Upsert into clinical_diagnoses (so readiness + future claims see it).
        existing = await db.clinical_diagnoses.find_one(
            {
                "tenant_id": ctx.tenant_id,
                "patient_id": note["patient_id"],
                "icd10_code": code, "status": "active",
            },
            {"_id": 0, "id": 1},
        )
        if not existing:
            await db.clinical_diagnoses.insert_one({
                "id": str(uuid.uuid4()),
                "tenant_id": ctx.tenant_id,
                "patient_id": note["patient_id"],
                "episode_id": note.get("episode_id"),
                "encounter_id": note.get("encounter_id"),
                "icd10_code": code,
                "label": d.label or code,
                "is_primary": is_primary,
                "status": "active",
                "source": "ai_scribe",
                "created_at": now_iso,
                "created_by": user["id"],
                "updated_at": now_iso,
            })
        diagnoses_payload.append({
            "sequence": len(diagnoses_payload) + 1, "code": code,
        })
        if len(diagnoses_payload) >= 12:
            break

    # CPT lines.
    lines_payload: list[dict] = []
    billed_total = 0
    seen_lines: set[tuple[str, tuple[str, ...]]] = set()
    for c in body.cpt:
        code = (c.code or "").strip().upper()
        if not code:
            continue
        mods = tuple(sorted(m.strip().upper() for m in (c.modifiers or []) if m.strip()))
        key = (code, mods)
        if key in seen_lines:
            continue
        seen_lines.add(key)
        units = max(int(c.units or 1), 1)
        billed_cents = max(int(c.billed_cents or 0), 0)
        billed_total += billed_cents * units
        lines_payload.append({
            "sequence": len(lines_payload) + 1,
            "service_date": dos,
            "code_type": "cpt",
            "code": code,
            "units": units,
            "billed_cents": billed_cents,
            "modifiers": list(mods),
            "diagnosis_pointers": [1] if diagnoses_payload else [],
        })
        if len(lines_payload) >= 50:
            break

    # Persist claim + child rows directly. We mirror the schema used by
    # /api/billing/claims/from-encounter so the scrubber and submission
    # endpoints work the same.
    claim_id = str(uuid.uuid4())
    claim_doc = {
        "id": claim_id,
        "tenant_id": ctx.tenant_id,
        "location_id": note.get("location_id"),
        "patient_id": note["patient_id"],
        "payer_id": body.payer_id,
        "policy_id": body.policy_id,
        "source_invoice_id": None,
        "source_encounter_id": note.get("encounter_id"),
        "claim_type": "professional",
        "place_of_service": body.place_of_service,
        "frequency_code": "1",
        "billing_provider_id": None,
        "rendering_provider_id": note.get("provider_id"),
        "facility_id": None,
        "authorization_number": None,
        "referral_number": None,
        "status": "draft",
        "service_date_from": dos,
        "service_date_to": dos,
        "billed_cents": billed_total,
        "paid_cents": 0,
        "submitted_at": None,
        "accepted_at": None,
        "last_denial_code": None,
        "notes": (
            f"Auto-generated by AI scribe send-to-claim from "
            f"{note_type} note {note_id}. CPT count={len(lines_payload)}, "
            f"ICD count={len(diagnoses_payload)}."
        ),
        "validation_error_count": 0,
        "validation_warning_count": 0,
        "validation_last_run_at": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": user["id"],
        "updated_by": user["id"],
        "history": [{
            "at": now_iso, "by": user["id"],
            "action": "created_from_scribe",
            "metadata": {
                "note_id": note_id, "note_type": note_type,
                "lines": len(lines_payload),
                "diagnoses": len(diagnoses_payload),
            },
        }],
    }
    diag_docs = [
        {
            "id": str(uuid.uuid4()),
            "tenant_id": ctx.tenant_id,
            "claim_id": claim_id,
            "sequence": d["sequence"],
            "code": d["code"],
            "created_at": now_iso,
        }
        for d in diagnoses_payload
    ]
    line_docs = [
        {
            "id": str(uuid.uuid4()),
            "tenant_id": ctx.tenant_id,
            "claim_id": claim_id,
            "sequence": ln["sequence"],
            "invoice_line_id": None,
            "service_date": ln["service_date"],
            "code_type": ln["code_type"],
            "code": ln["code"],
            "units": ln["units"],
            "billed_cents": ln["billed_cents"],
            "diagnosis_pointers": ln["diagnosis_pointers"],
            "modifiers": ln["modifiers"],
            "created_at": now_iso,
        }
        for ln in lines_payload
    ]

    if diag_docs:
        await db.claim_diagnoses.insert_many(diag_docs)
    if line_docs:
        await db.claim_lines.insert_many(line_docs)
    await db.claims.insert_one(claim_doc)

    await audit_success(
        user, "scribe.claim.created_from_suggestions", request,
        entity_type="claim", entity_id=claim_id,
        phi_accessed=True,
        metadata={
            "note_id": note_id, "note_type": note_type,
            "patient_id": note["patient_id"], "payer_id": body.payer_id,
            "lines": len(line_docs), "diagnoses": len(diag_docs),
            "billed_cents": billed_total,
        },
    )
    return {
        "claim_id": claim_id,
        "status": "draft",
        "billed_cents": billed_total,
        "lines": len(line_docs),
        "diagnoses": len(diag_docs),
        "patient_id": note["patient_id"],
        "encounter_id": note.get("encounter_id"),
    }
