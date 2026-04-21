"""Initial Exam default template.

One frozen system-default template for Phase 4. The exam stores a full
snapshot of this template at create-time so subsequent template revisions
never change how an already-completed exam renders.

Each section defines the ordered set of fields the UI should render, so the
frontend can iterate the snapshot instead of hard-coding layout logic.
"""
from __future__ import annotations

DEFAULT_TEMPLATE_ID = "default-initial-exam-v1"

DEFAULT_INITIAL_EXAM_TEMPLATE: dict = {
    "id": DEFAULT_TEMPLATE_ID,
    "version": 1,
    "name": "Chiropractic Initial Exam (default)",
    "description": "Default chiropractic initial evaluation template shipped with CCMS.",
    "sections": [
        {
            "id": "history",
            "title": "History",
            "description": "Intake-derived narrative prefilled from the chart.",
            "fields": [
                {"key": "chief_complaint", "label": "Chief complaint", "type": "textarea", "rows": 2, "prefill_key": "chief_complaint"},
                {"key": "history_of_present_illness", "label": "History of present illness (HPI)", "type": "textarea", "rows": 5, "prefill_key": "history_of_present_illness"},
                {"key": "onset_mechanism", "label": "Onset / mechanism", "type": "textarea", "rows": 3, "prefill_key": "mechanism_of_injury"},
                {"key": "medications", "label": "Medications", "type": "textarea", "rows": 2, "prefill_key": "medications"},
                {"key": "allergies", "label": "Allergies", "type": "textarea", "rows": 2, "prefill_key": "allergies"},
                {"key": "past_medical_history", "label": "Past medical history", "type": "textarea", "rows": 3, "prefill_key": "past_medical_history"},
                {"key": "past_surgical_history", "label": "Past surgical history", "type": "textarea", "rows": 2, "prefill_key": "past_surgical_history"},
                {"key": "family_history", "label": "Family history", "type": "textarea", "rows": 2, "prefill_key": "family_history"},
                {"key": "social_history", "label": "Social history", "type": "textarea", "rows": 2, "prefill_key": "social_history"},
                {"key": "occupation_activity", "label": "Occupation / activity context", "type": "textarea", "rows": 2},
                {"key": "review_of_systems", "label": "Review of systems", "type": "textarea", "rows": 4, "prefill_key": "review_of_systems"},
            ],
        },
        {
            "id": "examination",
            "title": "Examination",
            "description": "Structured objective findings.",
            "fields": [
                {"key": "vitals", "label": "Vitals", "type": "vitals"},
                {"key": "observation_inspection", "label": "Observation / inspection", "type": "textarea", "rows": 3},
                {"key": "posture", "label": "Posture", "type": "textarea", "rows": 2},
                {"key": "gait", "label": "Gait", "type": "textarea", "rows": 2},
                {"key": "palpation_findings", "label": "Palpation findings", "type": "textarea", "rows": 3},
                {"key": "segmental_spinal_findings", "label": "Segmental / spinal findings", "type": "textarea", "rows": 3},
                {"key": "range_of_motion", "label": "Range of motion", "type": "rom"},
                {"key": "orthopedic_tests", "label": "Orthopedic tests", "type": "orthopedic_tests"},
                {"key": "neurologic_findings", "label": "Neurologic findings", "type": "textarea", "rows": 3},
                {"key": "muscle_strength", "label": "Muscle strength", "type": "muscle_strength"},
                {"key": "sensory_reflex_findings", "label": "Sensory / reflex findings", "type": "textarea", "rows": 3},
            ],
        },
        {
            "id": "assessment",
            "title": "Assessment & Plan",
            "description": "Clinical impression, diagnoses, treatment recommendations.",
            "fields": [
                {"key": "functional_limitations", "label": "Functional limitations", "type": "textarea", "rows": 3},
                {"key": "assessment_summary", "label": "Assessment summary", "type": "textarea", "rows": 4},
                {"key": "initial_clinical_impression", "label": "Initial clinical impression", "type": "textarea", "rows": 3},
                {"key": "treatment_recommendations", "label": "Treatment recommendations", "type": "textarea", "rows": 4},
                {"key": "diagnoses", "label": "Diagnoses", "type": "diagnoses"},
            ],
        },
    ],
}
