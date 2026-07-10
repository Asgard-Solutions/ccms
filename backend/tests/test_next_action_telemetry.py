"""Integration tests for the Phase 3 next-action telemetry surface.

`POST /api/telemetry/ui-action` accepts two event shapes on one endpoint:

  1. `clinical_care_status_action_selected`  (legacy, exercised in
     `test_telemetry_ui_action.py`).
  2. `clinical_next_action_interaction`       (Phase 3 Slice 1 — this
     file).

The shapes must be mutually exclusive: cross-field mixes must 422 so a
misbehaving client cannot smuggle PHI or widen the schema by accident.
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

ALLOWED_NEXT_ACTION_IDS = [
    "sign-unsigned-note",
    "complete-missing-documentation",
    "attach-or-link-diagnosis",
    "open-blocked-billing-readiness",
    "review-billing-warning",
    "schedule-due-or-overdue-reexam",
    "schedule-remaining-planned-visits",
    "review-missing-required-intake",
    "record-configured-outcome-measure",
]

ALLOWED_INTERACTIONS = ["opened", "dismissed"]


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


def _next_action_payload(action_id: str = "sign-unsigned-note", interaction: str = "opened") -> dict:
    return {
        "event_name": "clinical_next_action_interaction",
        "section_slug": "next-actions",
        "source_surface": "patient-clinical",
        "layout_version": "v2",
        "action_id": action_id,
        "interaction": interaction,
    }


class TestNextActionHappyPath:
    def test_every_allow_listed_action_id_returns_204(self):
        s = _login(*ADMIN)
        for aid in ALLOWED_NEXT_ACTION_IDS:
            for interaction in ALLOWED_INTERACTIONS:
                r = s.post(
                    f"{API}/telemetry/ui-action",
                    json=_next_action_payload(aid, interaction),
                    timeout=15,
                )
                assert r.status_code == 204, f"{aid}/{interaction}: {r.status_code} {r.text}"
                assert r.content == b""


class TestNextActionRejectsUnknownVocabulary:
    def test_unknown_action_id_rejected(self):
        s = _login(*ADMIN)
        body = _next_action_payload()
        body["action_id"] = "order-imaging"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "action_id" in r.text

    def test_unknown_interaction_rejected(self):
        s = _login(*ADMIN)
        body = _next_action_payload()
        body["interaction"] = "completed"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        assert "interaction" in r.text

    def test_section_slug_must_be_next_actions(self):
        s = _login(*ADMIN)
        body = _next_action_payload()
        body["section_slug"] = "current-care-status"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text


class TestNextActionShapeIsolation:
    """A next-action event may not carry care-status fields, and vice versa."""

    def test_next_action_event_rejects_action_slug(self):
        s = _login(*ADMIN)
        body = _next_action_payload()
        body["action_slug"] = "schedule-reexam"
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text

    def test_care_status_event_rejects_next_action_fields(self):
        s = _login(*ADMIN)
        body = {
            "event_name": "clinical_care_status_action_selected",
            "section_slug": "current-care-status",
            "action_slug": "schedule-reexam",
            "source_surface": "patient-clinical",
            "layout_version": "v2",
            "action_id": "sign-unsigned-note",  # illegal cross-mix
        }
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text

    def test_next_action_event_requires_action_id_and_interaction(self):
        s = _login(*ADMIN)
        # missing action_id
        body = _next_action_payload()
        body.pop("action_id")
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text
        # missing interaction
        body = _next_action_payload()
        body.pop("interaction")
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, r.text


class TestNextActionRejectsPhiLeakAttempts:
    @pytest.mark.parametrize(
        "leak_field, leak_value",
        [
            ("patient_id", "b99e7285-6efa-47b4-b552-2ad2920657dc"),
            ("encounter_id", "some-encounter-uuid"),
            ("diagnosis_code", "M54.2"),
            ("free_text", "positive fever"),
            ("url", "/patients/abc/clinical#history"),
        ],
    )
    def test_extra_fields_are_forbidden(self, leak_field, leak_value):
        s = _login(*ADMIN)
        body = _next_action_payload()
        body[leak_field] = leak_value
        r = s.post(f"{API}/telemetry/ui-action", json=body, timeout=15)
        assert r.status_code == 422, f"{leak_field}: {r.status_code} {r.text}"
        assert "extra_forbidden" in r.text


class TestNextActionRequiresAuth:
    def test_anonymous_request_rejected(self):
        r = requests.post(
            f"{API}/telemetry/ui-action",
            json=_next_action_payload(),
            timeout=15,
        )
        assert r.status_code == 401, r.text
