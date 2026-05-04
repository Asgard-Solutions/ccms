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
from core.deps import require_role
from core.tenancy import TenantContext, get_tenant_context, tenant_db
from services.ai.client import generate, parse_json_safely
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
NOTE_COLLECTIONS = {
    "follow_up": "clinical_follow_up_notes",
    "initial_exam": "clinical_initial_exams",
    "reexam": "clinical_reexams",
}


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

    try:
        result = await generate(
            tenant_id=ctx.tenant_id, actor=user,
            system_prompt=SCRIBE_SOAP_SYSTEM,
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
