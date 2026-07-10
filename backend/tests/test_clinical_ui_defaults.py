"""Contract tests for Phase 3 Slice 2 durable clinical UI preferences.

Guarantees under test:
  * `clinical_ui_defaults` is a nested Pydantic model with strict
    `extra="forbid"` at every level — free-text slugs, patient ids, or
    diagnosis codes cannot smuggle through.
  * `timeline_presets[*].filters` accepts only allow-listed slugs.
  * `default_timeline_preset_id` must reference a preset that exists.
  * Duplicate preset ids or names are rejected.
  * Presets survive a round-trip: PATCH then GET `/auth/me` echoes the
    same structure.
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


def _preset_id() -> str:
    return "p_" + uuid.uuid4().hex[:16]


class TestHappyPath:
    def test_patch_and_readback(self):
        s = _login(*DOCTOR)
        pid = _preset_id()
        payload = {
            "clinical_ui_defaults": {
                "default_section": "encounters",
                "timeline_presets": [
                    {
                        "id": pid,
                        "name": "Signed visits, last 90 days",
                        "filters": {
                            "event_kinds": ["visit"],
                            "sources": ["encounter", "note"],
                            "provider_ids": [],
                            "date_window": "last_90d",
                        },
                    }
                ],
                "default_timeline_preset_id": pid,
            }
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 200, r.text

        # Readback via /auth/me (returns the persisted user doc).
        me = s.get(f"{API}/auth/me", timeout=15).json()
        cud = me.get("clinical_ui_defaults")
        assert cud is not None
        assert cud["default_section"] == "encounters"
        assert cud["default_timeline_preset_id"] == pid
        presets = cud["timeline_presets"]
        assert len(presets) == 1
        assert presets[0]["id"] == pid
        assert presets[0]["filters"]["date_window"] == "last_90d"


class TestRejectsPhiOrFreeText:
    def test_patient_id_forbidden_in_filters(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {
                        "id": _preset_id(), "name": "Bad preset",
                        "filters": {
                            "event_kinds": ["visit"],
                            "patient_id": "some-uuid",  # illegal extra
                        },
                    },
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422, r.text
        assert "extra_forbidden" in r.text

    def test_free_text_search_forbidden_in_filters(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {
                        "id": _preset_id(), "name": "Bad preset",
                        "filters": {
                            "event_kinds": ["visit"],
                            "q": "positive fever",  # illegal extra
                        },
                    },
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422, r.text

    def test_diagnosis_code_forbidden(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {
                        "id": _preset_id(), "name": "Bad",
                        "filters": {
                            "event_kinds": ["visit"],
                            "icd10_codes": ["M54.2"],  # illegal extra
                        },
                    },
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422

    def test_dates_of_service_forbidden(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {
                        "id": _preset_id(), "name": "Bad",
                        "filters": {
                            "event_kinds": ["visit"],
                            "date_of_service": "2026-01-15",  # illegal extra
                        },
                    },
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422


class TestValidation:
    def test_default_preset_must_reference_existing_preset(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {"id": _preset_id(), "name": "One", "filters": {"event_kinds": ["visit"]}},
                ],
                "default_timeline_preset_id": _preset_id(),  # dangling reference
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422
        assert "default_timeline_preset_id" in r.text

    def test_duplicate_preset_ids_rejected(self):
        s = _login(*DOCTOR)
        dup = _preset_id()
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {"id": dup, "name": "A", "filters": {"event_kinds": ["visit"]}},
                    {"id": dup, "name": "B", "filters": {"event_kinds": ["outcome_entry"]}},
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422

    def test_duplicate_preset_names_rejected(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {"id": _preset_id(), "name": "Same", "filters": {"event_kinds": ["visit"]}},
                    {"id": _preset_id(), "name": "Same", "filters": {"event_kinds": ["visit"]}},
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422

    def test_bad_preset_id_pattern_rejected(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {"id": "not-p-prefixed", "name": "x", "filters": {"event_kinds": ["visit"]}},
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422

    def test_unknown_event_kind_rejected(self):
        s = _login(*DOCTOR)
        payload = {
            "clinical_ui_defaults": {
                "timeline_presets": [
                    {"id": _preset_id(), "name": "Bad",
                     "filters": {"event_kinds": ["pizza"]}},
                ],
            },
        }
        r = s.patch(f"{API}/auth/me/preferences", json=payload, timeout=15)
        assert r.status_code == 422
