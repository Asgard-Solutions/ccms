"""
Phase 3 Slice 5C — durable preferences (backend contract tests).

Confirms that `PreferencesUpdate` / `ClinicalUIDefaults` accept the new
Slice 5 durable fields (workspace mode, summary_module_order, default
encounter filter, default outcome view, collapsed modules) and reject
any patient-scoped identifier at 422 via `extra=forbid`.
"""
import pytest
from pydantic import ValidationError

from services.identity.models import (
    ClinicalUIDefaults,
    PreferencesUpdate,
)


def _defaults(**kwargs) -> ClinicalUIDefaults:
    return ClinicalUIDefaults(**kwargs)


class TestSlice5AllowedFields:
    def test_default_workspace_mode_provider(self):
        d = _defaults(default_workspace_mode="provider")
        assert d.default_workspace_mode == "provider"

    def test_default_workspace_mode_administrator(self):
        d = _defaults(default_workspace_mode="administrator")
        assert d.default_workspace_mode == "administrator"

    def test_summary_module_order_valid(self):
        d = _defaults(summary_module_order=[
            "next_actions", "active_episode", "primary_diagnosis",
        ])
        assert d.summary_module_order == [
            "next_actions", "active_episode", "primary_diagnosis",
        ]

    def test_default_encounter_filter_needs_action(self):
        d = _defaults(default_encounter_filter="needs_action")
        assert d.default_encounter_filter == "needs_action"

    def test_default_outcome_view_chart(self):
        d = _defaults(default_outcome_view="chart")
        assert d.default_outcome_view == "chart"

    def test_collapsed_modules_valid(self):
        d = _defaults(collapsed_modules=["safety_summary", "recent_imaging"])
        assert d.collapsed_modules == ["safety_summary", "recent_imaging"]


class TestSlice5UnknownEnumRejection:
    def test_unknown_workspace_mode_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(default_workspace_mode="ceo")

    def test_unknown_module_slug_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(summary_module_order=["not_a_real_module"])

    def test_unknown_encounter_filter_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(default_encounter_filter="not_a_filter")

    def test_unknown_outcome_view_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(default_outcome_view="pie_chart")

    def test_duplicate_module_order_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(summary_module_order=[
                "next_actions", "next_actions", "active_episode",
            ])

    def test_duplicate_collapsed_modules_rejected(self):
        with pytest.raises(ValidationError):
            _defaults(collapsed_modules=["active_episode", "active_episode"])


class TestSlice5ExtraForbidGuards:
    """extra=forbid rejects every patient/record identifier we know of."""

    FORBIDDEN_KEYS = [
        "patient_id",
        "encounter_id",
        "appointment_id",
        "note_id",
        "diagnosis_code",
        "date_of_service",
        "name",
        "search_text",
        "episode_id",
        "record_id",
        "free_text",
        "clinical_value",
        "imaging_filter_patient",
        "scroll_position",
        "expanded_rows",
    ]

    @pytest.mark.parametrize("bad_key", FORBIDDEN_KEYS)
    def test_forbidden_key_rejected_at_defaults(self, bad_key):
        with pytest.raises(ValidationError):
            _defaults(**{bad_key: "anything"})

    @pytest.mark.parametrize("bad_key", FORBIDDEN_KEYS)
    def test_forbidden_key_rejected_at_preferences_update(self, bad_key):
        # An attacker could try to smuggle a patient field one level up.
        with pytest.raises(ValidationError):
            PreferencesUpdate(**{bad_key: "anything"})

    def test_preferences_update_accepts_clinical_ui_defaults(self):
        pu = PreferencesUpdate(clinical_ui_defaults=ClinicalUIDefaults(
            default_workspace_mode="provider",
            default_encounter_filter="needs_action",
            default_outcome_view="chart",
        ))
        assert pu.clinical_ui_defaults.default_workspace_mode == "provider"


class TestSlice5MaxLengthGuards:
    def test_summary_module_order_max_length_enforced(self):
        # Registry has 13 modules; 20-max lets us grow without breaking.
        # Attempting >20 unique-shaped slugs still trips max_length.
        with pytest.raises(ValidationError):
            _defaults(summary_module_order=["active_episode"] * 21)

    def test_collapsed_modules_max_length_enforced(self):
        with pytest.raises(ValidationError):
            _defaults(collapsed_modules=["active_episode"] * 21)
