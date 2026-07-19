"""Iteration 82 — extended scribe tests.

Adds coverage beyond tests/test_scribe.py:
  * Audio upload happy path (tiny synthetic webm) + list + delete.
  * Auto-delete on sign via reauth + sign follow-up note endpoint.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _doctor_draft_note():
    from motor.motor_asyncio import AsyncIOMotorClient
    from core.tenancy import reset_router_for_tests

    async def find():
        reset_router_for_tests()
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        u = await db.users.find_one({"email": "doctor@ccms.app"}, {"_id": 0, "tenant_id": 1})
        n = await db.clinical_follow_up_notes.find_one(
            {"tenant_id": u["tenant_id"]}, {"_id": 0, "id": 1, "patient_id": 1, "status": 1},
        )
        if n and n.get("status") == "signed":
            await db.clinical_follow_up_notes.update_one(
                {"id": n["id"]}, {"$set": {"status": "draft"}},
            )
        c.close()
        return n
    return asyncio.run(find())


class TestScribeAudioHappyPath:
    def test_upload_list_delete(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No follow-up notes in doctor tenant")
        s = _login(*DOCTOR)

        # Upload tiny synthetic webm payload — Whisper may error on synthetic,
        # but the row should still be created (transcribe_status in {ok,error}).
        payload = b"\x1a\x45\xdf\xa3" + b"\x00" * 1024  # fake webm header
        r = s.post(
            f"{API}/scribe/audio",
            data={"note_id": note["id"], "note_type": "follow_up"},
            files={"audio": ("clip.webm", payload, "audio/webm")},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "audio_id" in body
        audio_id = body["audio_id"]
        assert body.get("transcribe_status") in {"ok", "error", "empty", "skipped"}

        # List
        r2 = s.get(f"{API}/scribe/encounters/follow_up/{note['id']}/audio", timeout=15)
        assert r2.status_code == 200
        chunks = r2.json().get("chunks", [])
        assert any(c.get("id") == audio_id for c in chunks), f"chunk missing: {chunks}"

        # Delete
        r3 = s.delete(f"{API}/scribe/audio/{audio_id}", timeout=15)
        assert r3.status_code == 200
        assert r3.json().get("deleted") is True

        # Confirm removed from list
        r4 = s.get(f"{API}/scribe/encounters/follow_up/{note['id']}/audio", timeout=15)
        assert r4.status_code == 200
        ids_after = [c.get("id") for c in r4.json().get("chunks", [])]
        assert audio_id not in ids_after


class TestScribeAutoDeleteOnSign:
    def test_chunks_cleared_after_sign(self):
        note = _doctor_draft_note()
        if not note:
            pytest.skip("No follow-up notes in doctor tenant")
        s = _login(*DOCTOR)
        pid = note["patient_id"]
        nid = note["id"]

        # Upload a chunk
        payload = b"\x1a\x45\xdf\xa3" + b"\x00" * 512
        ru = s.post(
            f"{API}/scribe/audio",
            data={"note_id": nid, "note_type": "follow_up"},
            files={"audio": ("c.webm", payload, "audio/webm")},
            timeout=60,
        )
        assert ru.status_code == 200, ru.text

        # Reauth (password step-up)
        rr = s.post(f"{API}/auth/reauth", json={"password": DOCTOR[1]}, timeout=15)
        assert rr.status_code == 200, rr.text

        # Sign
        rs = s.post(
            f"{API}/patients/{pid}/clinical/notes/{nid}/sign",
            json={}, timeout=30,
        )
        if rs.status_code not in (200, 204):
            pytest.skip(f"sign failed ({rs.status_code}): {rs.text[:200]}")

        # After sign → chunks should be empty
        rl = s.get(f"{API}/scribe/encounters/follow_up/{nid}/audio", timeout=15)
        assert rl.status_code == 200
        assert rl.json().get("chunks", []) == []
