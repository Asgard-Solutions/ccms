"""Integration tests for the Phase 3 Slice 3 outcome-suggestion telemetry.

`POST /api/telemetry/ui-action` now accepts a third shape,
`clinical_outcome_suggestion_interaction`, alongside the care-status
and next-action shapes shipped earlier. Each shape MUST remain isolated
— any cross-field mix must return 422.
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")

ALLOWED_INSTRUMENT_KEYS = [
    "ndi",
    "oswestry",
    "pain_vas",
    "pain_scale",
    "functional_index",
    "bournemouth_neck",
]

ALLOWED_INTERACTIONS = ["opened", "dismissed"]


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


def _payload(instrument_key: str = "ndi", interaction: str = "opened") -> dict:
    return {
        "event_name": "clinical_outcome_suggestion_interaction",
        "section_slug": "outcomes",
        "source_surface": "patient-clinical",
        "layout_version": "v2",
        "instrument_key": instrument_key,
        "interaction": interaction,
    }


class TestHappyPath:
    def test_every_allow_listed_key_and_interaction_returns_204(self):
        s = _login(*ADMIN)
        for k in ALLOWED_INSTRUMENT_KEYS:
            for i in ALLOWED_INTERACTIONS:
                r = s.post(f"{API}/telemetry/ui-action", json=_payload(k, i), timeout=15)
                assert r.status_code == 204, f"{k}/{i}: {r.status_code} {r.text}"


class TestVocabularyRejection:
    def test_unknown_instrument_key_rejected(self):
        s = _login(*ADMIN)
        body = _payload()
        body["instrument_key"] = "made_up_measure"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422

    def test_unknown_interaction_rejected(self):
        s = _login(*ADMIN)
        body = _payload()
        body["interaction"] = "recorded"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422

    def test_section_slug_must_be_outcomes(self):
        s = _login(*ADMIN)
        body = _payload()
        body["section_slug"] = "next-actions"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422


class TestShapeIsolation:
    def test_outcome_event_rejects_action_id(self):
        s = _login(*ADMIN)
        body = _payload()
        body["action_id"] = "sign-unsigned-note"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422

    def test_next_action_event_rejects_instrument_key(self):
        s = _login(*ADMIN)
        body = {
            "event_name": "clinical_next_action_interaction",
            "section_slug": "next-actions",
            "source_surface": "patient-clinical",
            "layout_version": "v2",
            "action_id": "sign-unsigned-note",
            "interaction": "opened",
            "instrument_key": "ndi",
        }
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422

    def test_care_status_event_rejects_instrument_key(self):
        s = _login(*ADMIN)
        body = {
            "event_name": "clinical_care_status_action_selected",
            "section_slug": "current-care-status",
            "source_surface": "patient-clinical",
            "layout_version": "v2",
            "action_slug": "schedule-reexam",
            "instrument_key": "ndi",
        }
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422


class TestPhiLeakGuards:
    @pytest.mark.parametrize(
        "leak_field, leak_value",
        [
            ("patient_id", "abc-123"),
            ("captured_at", "2026-02-15T00:00:00Z"),
            ("score", 32),
            ("note", "Patient reports…"),
            ("linked_reexam_id", "rex-1"),
        ],
    )
    def test_extra_fields_forbidden(self, leak_field, leak_value):
        s = _login(*ADMIN)
        body = _payload()
        body[leak_field] = leak_value
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422
        assert "extra_forbidden" in r.text


class TestAuth:
    def test_anonymous_request_rejected(self):
        r = requests.post(
            f"{API}/telemetry/ui-action",
            json=_payload(),
            timeout=15,
        )
        assert r.status_code == 401
