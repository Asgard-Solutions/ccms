"""
Phase 3 Slice 6A — Telemetry PHI probe.

Confirms that every clinical UI event surface enforces `extra=forbid`
on the shipped Pydantic model, so an attacker (or a bug) cannot leak
patient identifiers, dates of service, names, or free-text search
strings through the telemetry firehose.

Focused on schema-layer validation — HTTP-level integration is covered
by the existing `test_telemetry_ui_action.py` suite.
"""
import pytest
from pydantic import ValidationError

from services.telemetry.router import UIEventPayload


PHI_LIKE_KEYS = [
    "patient_id",
    "encounter_id",
    "appointment_id",
    "note_id",
    "diagnosis_code",
    "date_of_service",
    "name",
    "email",
    "search_text",
    "free_text",
    "provider_name",
    "episode_name",
    "record_id",
    "outcome_score",
    "url",
    "chief_complaint",
    "hpi",
    "phone",
    "dob",
    "ssn",
    "mrn",
]


class TestTelemetryPHIProbe:
    @pytest.mark.parametrize("bad_key", PHI_LIKE_KEYS)
    def test_ui_event_rejects_phi_like_key(self, bad_key):
        with pytest.raises(ValidationError):
            UIEventPayload(**{
                "event": "clinical.layout.activated",
                bad_key: "anything",
            })

    def test_ui_event_rejects_unknown_event_name(self):
        with pytest.raises(ValidationError):
            UIEventPayload(event="clinical.exfiltrate_phi")

    def test_ui_event_rejects_unknown_section(self):
        with pytest.raises(ValidationError):
            UIEventPayload(event="clinical.nav.jump", section="ssn_display")

    def test_ui_event_accepts_shipped_allow_list(self):
        # Sanity: the known-good event names still validate.
        p = UIEventPayload(event="clinical.layout.activated", layout="v2")
        assert p.event == "clinical.layout.activated"
        assert p.layout == "v2"

    def test_error_code_length_capped(self):
        # A 65-char error_code trips the max_length=64 guard so we never
        # accept a stack-trace-shaped blob.
        with pytest.raises(ValidationError):
            UIEventPayload(
                event="clinical.section.load_failed",
                error_code="x" * 65,
            )
