"""Canonical Mongo collection names shared by `services/clinical/*`,
`services/ai/*`, and `services/scribe/*`.

Single source of truth so a renamed collection only needs touching here.
The bug that bit iteration_78 (AI service querying the wrong collection)
is exactly what this module exists to prevent.
"""
from __future__ import annotations

# Clinical artefact collections — keep these strings in lock-step with
# the indexes declared in `core/db.py::create_indexes`.
FOLLOW_UP_NOTES_COLL = "clinical_follow_up_notes"
INITIAL_EXAMS_COLL = "clinical_initial_exams"
REEXAMS_COLL = "clinical_reexams"
DIAGNOSES_COLL = "clinical_diagnoses"
TREATMENT_PLANS_COLL = "clinical_treatment_plans"
ENCOUNTERS_COLL = "clinical_encounters"
OUTCOME_ENTRIES_COLL = "clinical_outcome_entries"

# Mapping from `note_type` form-value → collection. Used by the AI
# scribe + billing-readiness coding suggester so the same vocabulary
# flows from frontend → backend.
NOTE_TYPE_TO_COLL: dict[str, str] = {
    "follow_up": FOLLOW_UP_NOTES_COLL,
    "initial_exam": INITIAL_EXAMS_COLL,
    "reexam": REEXAMS_COLL,
}
