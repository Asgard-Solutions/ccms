"""Realistic chart-level seed for Riverbend personas.

Every persona that has a visit on the schedule also gets an **episode**,
a **problem list** (diagnoses), an **active treatment plan** where
clinically appropriate, an **intake history snapshot**, and a couple of
**outcome entries** so the Intake + Clinical tabs feel lived-in.

Storage targets:
    - `clinical_episode_cases`
    - `clinical_diagnoses`
    - `clinical_history`
    - `clinical_treatment_plans`
    - `clinical_outcome_entries`

And on the patient document itself (the legacy grouped sections that
the Intake wizard reads):
    - `patients.clinical_intake`
    - `patients.case_details`

Every write is idempotent on a deterministic key so reseeding (or
running pytest that creates junk clinical rows) restores exactly the
curated demo state.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from core.db import get_db_write
from services.patient._shared import encrypt_patient_doc

logger = logging.getLogger("ccms.demo.clinical_seed")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> str:
    return _iso(datetime.now(timezone.utc))


def _days_ago(n: int) -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(days=n))


def _date_ago(n: int) -> str:
    """ISO date (not datetime) n days in the past."""
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


# ---------------------------------------------------------------------------
# Per-persona chart blueprint.
# Keyed by (first_name, last_name) — matches the _PERSONAS spec in seed.py.
# ---------------------------------------------------------------------------
CHART_BLUEPRINT: dict[tuple[str, str], dict] = {
    ("Hannah", "Whitaker"): {
        "episode": {
            "case_type": "injury_episode",
            "title": "Acute low-back strain — lifting injury",
            "chief_complaint": "Low back pain with right gluteal referral after lifting a moving box.",
            "mechanism_of_injury": "Lifted a 40 lb box with poor form; felt immediate lumbar pop.",
            "onset_date": _date_ago(21),
            "start_date_offset_days": -21,
            "status": "active",
            "tags": ["acute", "lumbar", "lifting-injury"],
        },
        "diagnoses": [
            {"icd10_code": "M54.50", "label": "Low back pain, unspecified",
             "body_region": "lumbar", "chronicity": "acute",
             "is_primary": True, "laterality": None},
            {"icd10_code": "M54.16", "label": "Radiculopathy, lumbar region",
             "body_region": "lumbar", "chronicity": "acute",
             "is_primary": False, "laterality": "right"},
        ],
        "treatment_plan": {
            "title": "Acute lumbar strain — 6-week rehab",
            "frequency_visits_per_week": 3,
            "expected_duration_weeks": 6,
            "frequency_total_visits": 18,
            "target_body_regions": ["lumbar", "pelvis"],
            "start_date_offset_days": -21,
            "re_exam_offset_days": 21,  # re-exam 3 weeks from now
            "baselines": {
                "pain_scale_0_10": 7,
                "key_rom_summary": "Lumbar flexion 40° (WNL 60°); extension 10° (WNL 25°); R SLR +30°.",
                "functional_measures": [
                    {"label": "Oswestry Disability Index", "value": 42, "unit": "%"},
                ],
                "notes": "Unable to sit > 20 min; interrupted sleep.",
            },
            "goals": [
                {"description": "Reduce pain from 7/10 to ≤ 2/10 at rest",
                 "measure_type": "pain_scale", "unit": "/10",
                 "baseline_value": 7, "target_value": 2, "status": "active"},
                {"description": "Restore lumbar flexion to ≥ 55°",
                 "measure_type": "rom", "unit": "°",
                 "baseline_value": 40, "target_value": 55, "status": "active"},
                {"description": "Oswestry ≤ 15%",
                 "measure_type": "outcome_score", "unit": "%",
                 "baseline_value": 42, "target_value": 15, "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Diversified lumbar + SI adjustment", "frequency": "2–3×/wk"},
                {"kind": "soft_tissue", "description": "QL & gluteal IASTM + trigger-point release", "frequency": "each visit"},
                {"kind": "exercise", "description": "McKenzie extension + core stabilization (bird-dog, dead-bug)", "frequency": "home daily"},
                {"kind": "education", "description": "Neutral-spine lifting mechanics; ice 15 min x3/day first 72h"},
            ],
            "home_care_recommendations": "Ice 15 min x 3/day first 72 hrs, then heat before exercise. Daily McKenzie press-ups. Avoid sitting > 30 min.",
            "activity_work_recommendations": "Modified duty: no lifting > 15 lb, no repetitive bending for 2 weeks.",
            "discharge_criteria": "Pain ≤ 2/10, Oswestry ≤ 15%, full pain-free lumbar ROM.",
        },
        "history": {
            "chief_complaint": "Low back pain with right-sided gluteal radiation.",
            "history_of_present_illness": (
                "35 y/o female presents with acute LBP following a lifting "
                "injury 3 weeks ago. Pain sharp, constant, 7/10, worse "
                "with sitting and forward flexion. Intermittent right "
                "gluteal ache, no frank radiation below the knee. Denies "
                "bowel/bladder changes, saddle anesthesia, or lower-"
                "extremity weakness."
            ),
            "onset_date": _date_ago(21),
            "mechanism_of_injury": "Lifting a 40 lb box with flexed lumbar spine.",
            "pain_locations": ["lumbar spine", "right gluteal region"],
            "pain_radiation": "Right gluteal, does not cross the knee.",
            "aggravating_factors": ["sitting", "forward bending", "sneezing"],
            "relieving_factors": ["lying supine", "walking", "ice"],
            "severity": 7,
            "prior_treatment": "NSAIDs x 10 days with partial relief.",
            "prior_chiropractic_care": True,
            "medications": "Ibuprofen 400 mg TID prn.",
            "allergies": "NKDA",
            "past_medical_history": "HTN — well controlled on lisinopril.",
            "past_surgical_history": "None relevant.",
            "family_history": "Non-contributory.",
            "social_history": "Non-smoker, occasional wine. Desk worker, runs 3x/week.",
            "occupation": "Marketing manager (desk-based)",
            "activity_level": "moderate",
            "review_of_systems": "ROS negative except for MSK as noted.",
            "red_flag_screening": {
                "night_pain": False, "saddle_anesthesia": False,
                "bowel_bladder": False, "fever": False, "trauma": True,
            },
        },
        "outcomes": [
            {"measure": "Oswestry Disability Index", "score": 42, "max_score": 100,
             "recorded_offset_days": -21, "notes": "Baseline at initial visit."},
            {"measure": "Numeric Pain Rating Scale", "score": 7, "max_score": 10,
             "recorded_offset_days": -21, "notes": "Baseline."},
            {"measure": "Numeric Pain Rating Scale", "score": 4, "max_score": 10,
             "recorded_offset_days": -7, "notes": "Improving with care."},
        ],
        "patient_intake": {
            "chief_complaint": "Lower back pain, onset 3 weeks ago.",
            "pain_scale_current": 4,
            "pain_scale_worst": 8,
            "pain_quality": ["sharp", "aching"],
            "injury_mechanism": "Lifting a heavy box at home.",
            "functional_limitations": ["prolonged sitting", "bending"],
        },
        "case_details": {
            "case_type": "injury",
            "date_of_injury": _date_ago(21),
            "referring_provider": None,
        },
    },

    ("Marcus", "Reid"): {
        "episode": {
            "case_type": "new_patient_eval",
            "title": "New patient — cervicogenic headache & neck stiffness",
            "chief_complaint": "Posterior headaches with neck stiffness, progressive over 4 weeks.",
            "mechanism_of_injury": "Insidious onset; long-haul trucking + poor sleep posture.",
            "onset_date": _date_ago(28),
            "start_date_offset_days": -1,
            "status": "active",
            "tags": ["cervical", "headache", "insidious"],
        },
        "diagnoses": [
            {"icd10_code": "M54.2", "label": "Cervicalgia",
             "body_region": "cervical", "chronicity": "subacute",
             "is_primary": True},
            {"icd10_code": "G44.86", "label": "Cervicogenic headache",
             "body_region": "head", "chronicity": "subacute",
             "is_primary": False},
        ],
        "treatment_plan": {
            "title": "Cervicogenic headache — 6-wk care plan",
            "frequency_visits_per_week": 2,
            "expected_duration_weeks": 6,
            "frequency_total_visits": 12,
            "target_body_regions": ["cervical", "upper thoracic"],
            "start_date_offset_days": -1,
            "re_exam_offset_days": 28,
            "baselines": {
                "pain_scale_0_10": 5,
                "key_rom_summary": "C-spine rotation L 55° / R 50° (WNL 80°).",
                "functional_measures": [
                    {"label": "Neck Disability Index", "value": 24, "unit": "%"},
                    {"label": "HA frequency", "value": 5, "unit": "days/wk"},
                ],
            },
            "goals": [
                {"description": "Reduce headache frequency to ≤ 1 day/week",
                 "measure_type": "custom", "unit": "days/wk",
                 "baseline_value": 5, "target_value": 1, "status": "active"},
                {"description": "Restore cervical rotation to ≥ 75° bilaterally",
                 "measure_type": "rom", "unit": "°",
                 "baseline_value": 52, "target_value": 75, "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Cervical diversified + upper-thoracic adjustments", "frequency": "2x/wk"},
                {"kind": "soft_tissue", "description": "Suboccipital release + levator/upper-trap trigger points", "frequency": "each visit"},
                {"kind": "exercise", "description": "Chin tucks, scapular retraction, DNF progression", "frequency": "home daily"},
                {"kind": "education", "description": "Ergonomics: cab seat + sleeping posture"},
            ],
            "home_care_recommendations": "Warm shower + chin tucks each morning. Sleep on one pillow, avoid stomach sleeping.",
            "activity_work_recommendations": "Take a 5-min stretch break every 90 min of driving; cab seat adjusted to support mid-back.",
            "discharge_criteria": "HA frequency ≤ 1 day/wk, NDI ≤ 10%, pain-free ROM.",
        },
        "history": {
            "chief_complaint": "Neck stiffness and posterior headaches.",
            "history_of_present_illness": (
                "42 y/o male long-haul trucker with 4-week history of "
                "progressive neck stiffness and occipital headaches 4–5 "
                "days/wk. Pain is dull, 3-5/10 at rest, worse by end of "
                "shift. Denies visual aura, nausea, photophobia."
            ),
            "onset_date": _date_ago(28),
            "mechanism_of_injury": "Insidious; poor posture during long drives.",
            "pain_locations": ["cervical spine", "bilateral suboccipital region"],
            "aggravating_factors": ["sustained driving", "sleeping prone"],
            "relieving_factors": ["stretching", "heat"],
            "severity": 5,
            "prior_treatment": "Self-massage, OTC ibuprofen with minimal relief.",
            "prior_chiropractic_care": False,
            "medications": "Ibuprofen 200 mg prn.",
            "allergies": "NKDA",
            "past_medical_history": "Pre-DM A1C 5.9.",
            "social_history": "Smoker (1/2 PPD), married, long-haul trucker.",
            "occupation": "Long-haul truck driver",
            "activity_level": "low",
            "review_of_systems": "ROS negative; denies fever, weight loss, neurologic sx.",
            "red_flag_screening": {"fever": False, "trauma": False, "bowel_bladder": False},
        },
        "outcomes": [
            {"measure": "Neck Disability Index", "score": 24, "max_score": 100,
             "recorded_offset_days": -1, "notes": "Baseline at intake."},
        ],
        "patient_intake": {
            "chief_complaint": "Posterior headaches and neck stiffness.",
            "pain_scale_current": 4,
            "pain_scale_worst": 6,
            "pain_quality": ["dull", "tight"],
            "functional_limitations": ["long driving", "sleeping"],
        },
        "case_details": {"case_type": "insurance"},
    },

    ("Isabella", "Cho"): {
        "episode": {
            "case_type": "mva",
            "title": "MVA 3 weeks ago — cervical/thoracic sprain",
            "chief_complaint": "Neck and upper-back pain after rear-end collision.",
            "mechanism_of_injury": "Rear-ended at red light, 25 mph, head restraint low.",
            "onset_date": _date_ago(21),
            "start_date_offset_days": -21,
            "status": "active",
            "tags": ["mva", "pip", "whiplash"],
        },
        "diagnoses": [
            {"icd10_code": "S13.4XXA", "label": "Sprain of ligaments of cervical spine, initial encounter",
             "body_region": "cervical", "chronicity": "acute",
             "is_primary": True, "onset_date": _date_ago(21)},
            {"icd10_code": "S23.3XXA", "label": "Sprain of ligaments of thoracic spine, initial encounter",
             "body_region": "thoracic", "chronicity": "acute",
             "is_primary": False, "onset_date": _date_ago(21)},
            {"icd10_code": "M79.2", "label": "Neuralgia and neuritis, unspecified",
             "body_region": "upper extremity", "chronicity": "acute",
             "is_primary": False, "laterality": "right"},
        ],
        "treatment_plan": {
            "title": "Post-MVA soft-tissue care (PIP) — 8 weeks",
            "frequency_visits_per_week": 3,
            "expected_duration_weeks": 8,
            "frequency_total_visits": 24,
            "target_body_regions": ["cervical", "thoracic", "scapulothoracic"],
            "start_date_offset_days": -21,
            "re_exam_offset_days": 14,
            "baselines": {
                "pain_scale_0_10": 6,
                "key_rom_summary": "C-spine flexion 40° / extension 30°; thoracic rotation L 30° / R 25°.",
                "functional_measures": [
                    {"label": "Neck Disability Index", "value": 32, "unit": "%"},
                    {"label": "Bournemouth Neck Q", "value": 40, "unit": ""},
                ],
            },
            "goals": [
                {"description": "Pain ≤ 1/10 with ADLs",
                 "measure_type": "pain_scale", "unit": "/10",
                 "baseline_value": 6, "target_value": 1, "status": "active"},
                {"description": "NDI ≤ 10%",
                 "measure_type": "outcome_score", "unit": "%",
                 "baseline_value": 32, "target_value": 10, "status": "active"},
                {"description": "Return to regular yoga practice",
                 "measure_type": "functional", "unit": None,
                 "baseline_value": "avoiding", "target_value": "full", "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Cervical + upper-thoracic adjustments, instrument-assisted", "frequency": "3x/wk"},
                {"kind": "modality", "description": "Electrical stim + moist heat, 10 min", "frequency": "each visit x first 3 weeks"},
                {"kind": "soft_tissue", "description": "Myofascial release, paraspinals + trapezius", "frequency": "each visit"},
                {"kind": "exercise", "description": "Cervical retraction, scapular stabilization progression", "frequency": "home daily"},
            ],
            "home_care_recommendations": "Apply ice 15 min 3x/day first week; moist heat after. Gentle ROM drills daily.",
            "activity_work_recommendations": "Modified yoga only for first 3 weeks; no lifting > 10 lb.",
            "discharge_criteria": "NDI ≤ 10%, full pain-free ROM, return to yoga.",
        },
        "history": {
            "chief_complaint": "Neck and upper-back pain, right-arm tingling since MVA.",
            "history_of_present_illness": (
                "29 y/o female, rear-ended 3 weeks ago at ~25 mph. "
                "Immediate neck pain, stiffness the next morning. "
                "Intermittent paresthesia down the right arm (C6-ish "
                "distribution). PCP cleared for no fracture."
            ),
            "onset_date": _date_ago(21),
            "mechanism_of_injury": "Motor vehicle collision — rear-end impact.",
            "pain_locations": ["cervical spine", "upper thoracic", "right trapezius"],
            "pain_radiation": "Intermittent right-arm paresthesia, C6 distribution.",
            "aggravating_factors": ["prolonged sitting", "overhead reach", "driving"],
            "relieving_factors": ["rest", "heat"],
            "severity": 6,
            "prior_treatment": "Ice, ibuprofen, cyclobenzaprine x 7 days from ER.",
            "prior_chiropractic_care": False,
            "medications": "Ibuprofen 600 mg TID.",
            "allergies": "Penicillin (rash)",
            "past_medical_history": "Migraines (well controlled).",
            "social_history": "Non-smoker, yoga 4x/wk pre-injury, software engineer.",
            "occupation": "Software engineer",
            "activity_level": "moderate-high pre-injury",
            "accident_details": {
                "date_of_injury": _date_ago(21),
                "location": "Portland, OR — Burnside & SE 12th",
                "mechanism": "Rear-end collision at red light",
                "vehicle_role": "driver",
                "airbag_deployed": False,
                "seat_belt": True,
                "er_evaluation": "Providence Portland ER, same day; cleared for fracture.",
                "police_report": "PPB case #2026-04-0182",
                "carrier": "Northwest Auto PIP",
                "claim_number": "NWA-2026-44781",
                "adjuster_name": "R. Thompson",
                "adjuster_phone": "5035554412",
            },
            "red_flag_screening": {"fever": False, "trauma": True, "bowel_bladder": False},
        },
        "outcomes": [
            {"measure": "Neck Disability Index", "score": 32, "max_score": 100,
             "recorded_offset_days": -21, "notes": "Baseline post-MVA."},
            {"measure": "Numeric Pain Rating Scale", "score": 6, "max_score": 10,
             "recorded_offset_days": -21, "notes": "Baseline."},
            {"measure": "Numeric Pain Rating Scale", "score": 3, "max_score": 10,
             "recorded_offset_days": -7, "notes": "3-week progress."},
        ],
        "patient_intake": {
            "chief_complaint": "Post-MVA neck and upper back pain with arm tingling.",
            "pain_scale_current": 4,
            "pain_scale_worst": 7,
            "pain_quality": ["sharp", "stiff", "tingling"],
            "injury_mechanism": "Rear-end motor vehicle collision.",
            "functional_limitations": ["driving", "computer work", "yoga"],
        },
        "case_details": {
            "case_type": "auto",
            "date_of_injury": _date_ago(21),
            "carrier_name": "Northwest Auto PIP",
            "claim_number": "NWA-2026-44781",
            "adjuster_name": "R. Thompson",
            "adjuster_phone": "5035554412",
        },
    },

    ("Derrick", "Stone"): {
        "episode": {
            "case_type": "workers_comp",
            "title": "Workers' comp — lumbar strain from warehouse lift",
            "chief_complaint": "Lumbar pain from a work-related lifting injury.",
            "mechanism_of_injury": "Lifting a 60 lb crate off a low pallet at warehouse; twisted.",
            "onset_date": _date_ago(14),
            "start_date_offset_days": -14,
            "status": "active",
            "tags": ["wc", "lumbar", "warehouse"],
        },
        "diagnoses": [
            {"icd10_code": "S33.5XXA", "label": "Sprain of ligaments of lumbar spine, initial encounter",
             "body_region": "lumbar", "chronicity": "acute",
             "is_primary": True, "onset_date": _date_ago(14)},
            {"icd10_code": "M54.50", "label": "Low back pain, unspecified",
             "body_region": "lumbar", "chronicity": "acute",
             "is_primary": False},
        ],
        "treatment_plan": {
            "title": "WC lumbar sprain — return-to-work protocol, 6 wks",
            "frequency_visits_per_week": 3,
            "expected_duration_weeks": 6,
            "frequency_total_visits": 18,
            "target_body_regions": ["lumbar", "thoracolumbar", "hip"],
            "start_date_offset_days": -14,
            "re_exam_offset_days": 14,
            "baselines": {
                "pain_scale_0_10": 6,
                "key_rom_summary": "Lumbar flexion 30° / extension 15°; R SLR +40°.",
                "functional_measures": [
                    {"label": "Oswestry Disability Index", "value": 38, "unit": "%"},
                ],
            },
            "goals": [
                {"description": "Return to full-duty warehouse work",
                 "measure_type": "functional", "unit": None,
                 "baseline_value": "light duty", "target_value": "full duty", "status": "active"},
                {"description": "Pain ≤ 2/10 with lifting",
                 "measure_type": "pain_scale", "unit": "/10",
                 "baseline_value": 6, "target_value": 2, "status": "active"},
                {"description": "Oswestry ≤ 10%",
                 "measure_type": "outcome_score", "unit": "%",
                 "baseline_value": 38, "target_value": 10, "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Lumbar + SI diversified adjustments", "frequency": "3x/wk"},
                {"kind": "modality", "description": "Ice/heat contrast + e-stim", "frequency": "each visit"},
                {"kind": "exercise", "description": "Core stabilization progression, McGill big-3", "frequency": "home daily"},
                {"kind": "education", "description": "Proper lifting mechanics, hip hinge, stack-and-set"},
            ],
            "home_care_recommendations": "Ice 15 min first week then heat. McGill big-3 daily. No prolonged sitting > 30 min.",
            "activity_work_recommendations": "Light duty: lifting limit 20 lb for 2 weeks, then 40 lb for 2 weeks, then re-evaluate.",
            "discharge_criteria": "Return to pre-injury work duties, pain ≤ 2/10, Oswestry ≤ 10%.",
        },
        "history": {
            "chief_complaint": "Work-related low back pain with right-leg ache.",
            "history_of_present_illness": (
                "51 y/o male warehouse worker injured while lifting a "
                "60 lb crate 2 weeks ago. Felt immediate lumbar pain and "
                "right gluteal ache. No radiation below the knee, no "
                "saddle anesthesia, no weakness."
            ),
            "onset_date": _date_ago(14),
            "mechanism_of_injury": "Lifting and twisting with a 60 lb crate.",
            "pain_locations": ["lumbar spine", "right SI region"],
            "pain_radiation": "Right gluteal; does not cross the knee.",
            "aggravating_factors": ["lifting", "sustained standing", "forward bending"],
            "relieving_factors": ["lying down", "heat"],
            "severity": 6,
            "prior_treatment": "ER visit — X-ray negative, discharged with ibuprofen and cyclobenzaprine.",
            "prior_chiropractic_care": True,
            "medications": "Ibuprofen 600 mg TID, cyclobenzaprine 5 mg qhs.",
            "allergies": "NKDA",
            "past_medical_history": "HTN, type-2 DM (well controlled).",
            "social_history": "Former smoker (quit 2019), social drinker, warehouse worker.",
            "occupation": "Warehouse associate",
            "activity_level": "high (physical job)",
            "work_comp_details": {
                "claim_number": "WC-2026-99821",
                "carrier": "Oregon SAIF Workers' Compensation",
                "adjuster_name": "J. Villanueva",
                "adjuster_phone": "5035551224",
                "date_of_injury": _date_ago(14),
                "employer": "NW Logistics Inc.",
                "state_of_claim": "OR",
                "return_to_work_status": "light duty",
            },
            "red_flag_screening": {"fever": False, "trauma": True, "bowel_bladder": False},
        },
        "outcomes": [
            {"measure": "Oswestry Disability Index", "score": 38, "max_score": 100,
             "recorded_offset_days": -14, "notes": "Baseline."},
        ],
        "patient_intake": {
            "chief_complaint": "Low back pain from lifting at work.",
            "pain_scale_current": 5,
            "pain_scale_worst": 7,
            "pain_quality": ["aching", "stiff"],
            "injury_mechanism": "Lifting a heavy crate at work.",
            "functional_limitations": ["lifting", "standing long periods"],
        },
        "case_details": {
            "case_type": "wc",
            "date_of_injury": _date_ago(14),
            "carrier_name": "Oregon SAIF Workers' Compensation",
            "claim_number": "WC-2026-99821",
            "adjuster_name": "J. Villanueva",
            "adjuster_phone": "5035551224",
        },
    },

    ("Aria", "Johnson"): {
        "episode": {
            "case_type": "injury_episode",
            "title": "Right-shoulder impingement — overhead activities",
            "chief_complaint": "Right shoulder pain with overhead motion, 6 weeks.",
            "mechanism_of_injury": "Repetitive overhead painting at home (DIY ceiling).",
            "onset_date": _date_ago(42),
            "start_date_offset_days": -42,
            "status": "active",
            "tags": ["shoulder", "impingement", "overuse"],
        },
        "diagnoses": [
            {"icd10_code": "M75.101", "label": "Rotator cuff tendinopathy, right shoulder",
             "body_region": "shoulder", "chronicity": "subacute",
             "is_primary": True, "laterality": "right"},
            {"icd10_code": "M25.511", "label": "Pain in right shoulder",
             "body_region": "shoulder", "chronicity": "subacute",
             "is_primary": False, "laterality": "right"},
        ],
        "treatment_plan": {
            "title": "Right-shoulder impingement — 4-wk rehab",
            "frequency_visits_per_week": 2,
            "expected_duration_weeks": 4,
            "frequency_total_visits": 8,
            "target_body_regions": ["shoulder", "cervical", "scapulothoracic"],
            "start_date_offset_days": -42,
            "re_exam_offset_days": 14,
            "baselines": {
                "pain_scale_0_10": 5,
                "key_rom_summary": "R-shoulder abduction 110° (L 170°); flexion 140°; painful arc 90–120°.",
                "functional_measures": [
                    {"label": "QuickDASH", "value": 34, "unit": "%"},
                ],
            },
            "goals": [
                {"description": "Full pain-free shoulder abduction",
                 "measure_type": "rom", "unit": "°",
                 "baseline_value": 110, "target_value": 170, "status": "active"},
                {"description": "QuickDASH ≤ 10%",
                 "measure_type": "outcome_score", "unit": "%",
                 "baseline_value": 34, "target_value": 10, "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Cervical + upper thoracic + GH mobilization", "frequency": "2x/wk"},
                {"kind": "soft_tissue", "description": "Rotator-cuff trigger-point release + IASTM", "frequency": "each visit"},
                {"kind": "exercise", "description": "Rotator-cuff progression, scapular stabilization", "frequency": "home daily"},
                {"kind": "modality", "description": "Ultrasound + e-stim", "frequency": "first 2 wks"},
            ],
            "home_care_recommendations": "Ice 10 min post-activity. Sleeper stretch + wall slides daily.",
            "activity_work_recommendations": "Avoid overhead painting/lifting until pain-free abduction > 150°.",
            "discharge_criteria": "QuickDASH ≤ 10%, pain-free overhead ROM.",
        },
        "history": {
            "chief_complaint": "Right shoulder pain with overhead motion.",
            "history_of_present_illness": (
                "28 y/o female, 6 weeks of progressive R shoulder pain "
                "after painting a ceiling at home. Positive painful arc "
                "90–120°. No night pain, no trauma."
            ),
            "onset_date": _date_ago(42),
            "mechanism_of_injury": "Repetitive overhead motion.",
            "pain_locations": ["right anterolateral shoulder"],
            "aggravating_factors": ["overhead reach", "side-sleeping right"],
            "relieving_factors": ["rest", "ice"],
            "severity": 5,
            "prior_treatment": "Ibuprofen prn; home rest (no relief).",
            "prior_chiropractic_care": True,
            "medications": "Ibuprofen 400 mg prn.",
            "allergies": "NKDA",
            "past_medical_history": "Unremarkable.",
            "social_history": "Non-smoker, social drinker, barista + part-time artist.",
            "occupation": "Barista & artist",
            "activity_level": "moderate",
            "review_of_systems": "ROS negative except MSK.",
            "red_flag_screening": {"fever": False, "trauma": False, "bowel_bladder": False},
        },
        "outcomes": [
            {"measure": "QuickDASH", "score": 34, "max_score": 100,
             "recorded_offset_days": -42, "notes": "Baseline."},
            {"measure": "QuickDASH", "score": 20, "max_score": 100,
             "recorded_offset_days": -14, "notes": "2-wk progress — improving."},
        ],
        "patient_intake": {
            "chief_complaint": "Right shoulder impingement pain, worse overhead.",
            "pain_scale_current": 3,
            "pain_scale_worst": 6,
            "pain_quality": ["sharp", "achy"],
            "injury_mechanism": "Painting a ceiling at home.",
            "functional_limitations": ["overhead reach", "side-sleeping"],
        },
        "case_details": {"case_type": "insurance"},
    },

    ("Claire", "Morgan"): {
        "episode": {
            "case_type": "injury_episode",
            "title": "Lumbar strain — completed 3-month plan",
            "chief_complaint": "Lumbar strain from yard work (resolved).",
            "mechanism_of_injury": "Planting shrubs — prolonged bending/lifting.",
            "onset_date": _date_ago(120),
            "start_date_offset_days": -120,
            "end_date_offset_days": -25,
            "status": "closed",
            "closed_reason": "Goals met; patient released to home exercise program.",
            "tags": ["lumbar", "completed", "insurance"],
        },
        "diagnoses": [
            {"icd10_code": "M54.50", "label": "Low back pain, unspecified",
             "body_region": "lumbar", "chronicity": "subacute",
             "is_primary": True, "status": "resolved",
             "onset_date": _date_ago(120), "resolved_offset_days": -25},
        ],
        "treatment_plan": {
            "title": "Lumbar strain — completed 12-wk care (discharged)",
            "plan_status": "completed",
            "frequency_visits_per_week": 2,
            "expected_duration_weeks": 12,
            "frequency_total_visits": 24,
            "target_body_regions": ["lumbar", "pelvis"],
            "start_date_offset_days": -120,
            "baselines": {
                "pain_scale_0_10": 6,
                "functional_measures": [
                    {"label": "Oswestry Disability Index", "value": 36, "unit": "%"},
                ],
            },
            "goals": [
                {"description": "Discharged goal: pain ≤ 2/10",
                 "measure_type": "pain_scale", "unit": "/10",
                 "baseline_value": 6, "target_value": 2, "status": "met"},
                {"description": "Discharged goal: ODI ≤ 10%",
                 "measure_type": "outcome_score", "unit": "%",
                 "baseline_value": 36, "target_value": 10, "status": "met"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Lumbar/SI adjustments — completed"},
                {"kind": "exercise", "description": "Core stabilization HEP — patient transitioned to self-management"},
            ],
            "discharge_criteria": "Pain-free ADLs, pain ≤ 2/10, ODI ≤ 10%. All met.",
            "discharge_reason": "Goals met at 12-wk re-exam; transitioned to HEP.",
        },
        "history": {
            "chief_complaint": "Historical: low back strain from yard work (resolved).",
            "history_of_present_illness": (
                "47 y/o female successfully completed a 12-week chiropractic "
                "care plan 4 weeks ago. All goals met. Transitioned to "
                "home exercise program. Last visit uneventful."
            ),
            "onset_date": _date_ago(120),
            "severity": 2,
            "prior_chiropractic_care": True,
            "occupation": "Elementary school teacher",
            "activity_level": "moderate",
        },
        "outcomes": [
            {"measure": "Oswestry Disability Index", "score": 36, "max_score": 100,
             "recorded_offset_days": -120, "notes": "Baseline."},
            {"measure": "Oswestry Disability Index", "score": 8, "max_score": 100,
             "recorded_offset_days": -25, "notes": "Discharge — goals met."},
        ],
        "patient_intake": {
            "chief_complaint": "No active complaints — maintenance visits only.",
            "pain_scale_current": 1,
            "pain_scale_worst": 2,
        },
        "case_details": {"case_type": "insurance"},
    },

    ("Jaxon", "Morgan"): {
        "episode": {
            "case_type": "new_patient_eval",
            "title": "Pediatric — postural neck stiffness (growth spurt)",
            "chief_complaint": "Occasional neck stiffness related to growth spurt and sports posture.",
            "mechanism_of_injury": "Insidious — growth spurt + heavy backpack + baseball practice.",
            "onset_date": _date_ago(10),
            "start_date_offset_days": -10,
            "status": "active",
            "tags": ["pediatric", "cervical", "postural"],
        },
        "diagnoses": [
            {"icd10_code": "M54.2", "label": "Cervicalgia",
             "body_region": "cervical", "chronicity": "subacute",
             "is_primary": True},
        ],
        "treatment_plan": {
            "title": "Pediatric postural care — education-led, 4 wks",
            "frequency_visits_per_week": 1,
            "expected_duration_weeks": 4,
            "frequency_total_visits": 4,
            "target_body_regions": ["cervical", "thoracic"],
            "start_date_offset_days": -10,
            "re_exam_offset_days": 21,
            "baselines": {
                "pain_scale_0_10": 3,
                "functional_measures": [
                    {"label": "Guardian-reported HA frequency", "value": 2, "unit": "days/wk"},
                ],
            },
            "goals": [
                {"description": "Symptom-free school week",
                 "measure_type": "functional", "unit": None,
                 "baseline_value": "2 days/wk", "target_value": "0 days/wk", "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Gentle low-force cervical mobilization — drop-piece only", "frequency": "1x/wk"},
                {"kind": "education", "description": "Backpack weight < 10% BW; phone posture; nightly stretching"},
                {"kind": "exercise", "description": "Daily chin tucks + thoracic cat-camel (5 min)"},
            ],
            "home_care_recommendations": "Parent-supervised stretch routine each night before bed. Limit phone/tablet to 30 min sessions.",
            "activity_work_recommendations": "No baseball-practice restriction; encourage pre-practice warm-up.",
            "discharge_criteria": "Asymptomatic, consistent posture habit established.",
        },
        "history": {
            "chief_complaint": "Neck stiffness from backpack + sports.",
            "history_of_present_illness": (
                "12 y/o male, parent brought in for intermittent neck "
                "stiffness and occasional mild posterior headaches. No "
                "trauma. Growth spurt this year (+2 in)."
            ),
            "onset_date": _date_ago(10),
            "severity": 3,
            "prior_chiropractic_care": False,
            "medications": "None.",
            "allergies": "NKDA",
            "past_medical_history": "Healthy; vaccinated per schedule.",
            "social_history": "Lives with mother (Claire Morgan). 6th grade. Plays little-league baseball.",
            "occupation": "Student",
            "activity_level": "high",
            "red_flag_screening": {"fever": False, "trauma": False, "night_pain": False},
        },
        "outcomes": [],
        "patient_intake": {
            "chief_complaint": "Occasional neck stiffness and mild headaches.",
            "pain_scale_current": 2,
            "pain_scale_worst": 4,
            "guardian_present": True,
        },
        "case_details": {"case_type": "insurance"},
    },

    ("Ethan", "Parker"): {
        "episode": {
            "case_type": "maintenance",
            "title": "Maintenance / wellness care — chronic mid-back tension",
            "chief_complaint": "Ongoing mid-back tension; monthly maintenance visits.",
            "mechanism_of_injury": None,
            "onset_date": _date_ago(720),
            "start_date_offset_days": -720,
            "status": "active",
            "tags": ["maintenance", "self-pay", "chronic"],
        },
        "diagnoses": [
            {"icd10_code": "M54.6", "label": "Pain in thoracic spine",
             "body_region": "thoracic", "chronicity": "chronic",
             "is_primary": True},
        ],
        "treatment_plan": {
            "title": "Wellness maintenance — monthly adjustment",
            "frequency_visits_per_week": 0,
            "frequency_total_visits": 12,
            "expected_duration_weeks": 52,
            "target_body_regions": ["thoracic", "cervical"],
            "start_date_offset_days": -720,
            "baselines": {
                "pain_scale_0_10": 2,
                "functional_measures": [
                    {"label": "Patient-reported stiffness", "value": "mild", "unit": None},
                ],
            },
            "goals": [
                {"description": "Maintain pain ≤ 2/10 and full ROM",
                 "measure_type": "pain_scale", "unit": "/10",
                 "baseline_value": 2, "target_value": 2, "status": "active"},
            ],
            "planned_interventions": [
                {"kind": "adjustment", "description": "Thoracic + cervical adjustments", "frequency": "1x/month"},
                {"kind": "soft_tissue", "description": "Upper-trap + rhomboid release, 10 min", "frequency": "each visit"},
                {"kind": "exercise", "description": "Thoracic foam-roller mobility drill", "frequency": "home, 3x/wk"},
            ],
            "home_care_recommendations": "Foam-roller thoracic drill 3x/wk, thoracic extensions at desk every 2 hrs.",
            "activity_work_recommendations": "Continue regular cycling & hiking; sit-stand desk encouraged.",
            "discharge_criteria": "N/A — maintenance program.",
            "maintenance_transition_notes": "Transitioned from active care to maintenance 2 years ago; symptom-stable.",
        },
        "history": {
            "chief_complaint": "Chronic mid-back tension (stable).",
            "history_of_present_illness": (
                "38 y/o male on long-term maintenance care. History of "
                "thoracic tension from prior desk-job posture. Stable "
                "on monthly care for 2 years. No new complaints."
            ),
            "onset_date": _date_ago(720),
            "severity": 2,
            "prior_chiropractic_care": True,
            "medications": "None routinely.",
            "allergies": "NKDA",
            "occupation": "Software consultant (remote)",
            "activity_level": "moderate",
        },
        "outcomes": [
            {"measure": "Patient-reported stiffness (Likert)", "score": 2, "max_score": 5,
             "recorded_offset_days": -60, "notes": "Stable — monthly maintenance."},
        ],
        "patient_intake": {
            "chief_complaint": "Self-pay maintenance visits for chronic mid-back tension.",
            "pain_scale_current": 2,
            "pain_scale_worst": 3,
            "pain_quality": ["tight", "stiff"],
        },
        "case_details": {"case_type": "self_pay"},
    },
}


# ---------------------------------------------------------------------------
async def _upsert_episodes(
    tenant_id: str, location_id: str | None,
    patients_by_name: dict[tuple[str, str], str],
    lead_doc_id: str | None,
) -> dict[tuple[str, str], str]:
    """Seed one episode per persona. Idempotent on (patient_id, title).
    Returns {(first,last): episode_id} so diagnoses/plans can link."""
    db = get_db_write()
    ep_ids: dict[tuple[str, str], str] = {}
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        if not pid:
            continue
        ep_spec = bp["episode"]
        key = {"tenant_id": tenant_id, "patient_id": pid,
               "title": ep_spec["title"]}
        existing = await db.clinical_episode_cases.find_one(
            key, {"_id": 0, "id": 1},
        )
        ep_id = existing["id"] if existing else str(uuid.uuid4())
        start_dt = datetime.now(timezone.utc) + timedelta(
            days=ep_spec["start_date_offset_days"],
        )
        end_dt = None
        if "end_date_offset_days" in ep_spec:
            end_dt = datetime.now(timezone.utc) + timedelta(
                days=ep_spec["end_date_offset_days"],
            )
        doc = {
            **key,
            "id": ep_id,
            "location_id": location_id,
            "responsible_provider_id": lead_doc_id,
            "case_type": ep_spec["case_type"],
            "status": ep_spec["status"],
            "chief_complaint": ep_spec.get("chief_complaint"),
            "mechanism_of_injury": ep_spec.get("mechanism_of_injury"),
            "onset_date": ep_spec.get("onset_date"),
            "start_date": _iso(start_dt),
            "end_date": _iso(end_dt) if end_dt else None,
            "closed_reason": ep_spec.get("closed_reason"),
            "tags": ep_spec.get("tags", []),
            "metadata": {},
            "updated_at": _now(),
            "updated_by": lead_doc_id,
        }
        if existing:
            await db.clinical_episode_cases.update_one(
                {"id": ep_id}, {"$set": doc},
            )
        else:
            doc["created_at"] = _now()
            doc["created_by"] = lead_doc_id
            await db.clinical_episode_cases.insert_one(doc)
        ep_ids[name_key] = ep_id
    return ep_ids


async def _upsert_diagnoses(
    tenant_id: str,
    patients_by_name: dict[tuple[str, str], str],
    episode_ids: dict[tuple[str, str], str],
    lead_doc_id: str | None,
) -> dict[tuple[str, str], list[str]]:
    """Seed diagnoses keyed on (patient_id, episode_id, icd10_code).
    Returns {(first,last): [dx_ids]} so treatment plans can link."""
    db = get_db_write()
    dx_map: dict[tuple[str, str], list[str]] = {}
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        ep_id = episode_ids.get(name_key)
        if not (pid and ep_id):
            continue
        dx_ids: list[str] = []
        # Primary-uniqueness in the phase2 writer is per (patient,
        # episode); the demo blueprint already flags exactly one
        # is_primary per episode so writes land cleanly.
        for dx_spec in bp.get("diagnoses", []):
            key = {
                "tenant_id": tenant_id, "patient_id": pid,
                "episode_id": ep_id,
                "icd10_code": dx_spec["icd10_code"],
            }
            existing = await db.clinical_diagnoses.find_one(
                key, {"_id": 0, "id": 1},
            )
            dx_id = existing["id"] if existing else str(uuid.uuid4())
            resolved_offset = dx_spec.get("resolved_offset_days")
            doc = {
                **key, "id": dx_id,
                "label": dx_spec["label"],
                "status": dx_spec.get("status", "active"),
                "is_primary": dx_spec.get("is_primary", False),
                "body_region": dx_spec.get("body_region"),
                "laterality": dx_spec.get("laterality"),
                "chronicity": dx_spec.get("chronicity"),
                "onset_date": dx_spec.get("onset_date"),
                "resolved_date": (
                    (datetime.now(timezone.utc) + timedelta(days=resolved_offset)).date().isoformat()
                    if resolved_offset is not None else None
                ),
                "notes": dx_spec.get("notes"),
                "updated_at": _now(),
                "updated_by": lead_doc_id,
            }
            if existing:
                await db.clinical_diagnoses.update_one(
                    {"id": dx_id}, {"$set": doc},
                )
            else:
                doc["created_at"] = _now()
                doc["created_by"] = lead_doc_id
                await db.clinical_diagnoses.insert_one(doc)
            dx_ids.append(dx_id)
        dx_map[name_key] = dx_ids
    return dx_map


async def _upsert_treatment_plans(
    tenant_id: str, location_id: str | None,
    patients_by_name: dict[tuple[str, str], str],
    episode_ids: dict[tuple[str, str], str],
    dx_map: dict[tuple[str, str], list[str]],
    lead_doc_id: str | None,
) -> None:
    """Seed one active or completed treatment plan per persona with an
    episode. Idempotent on (patient_id, episode_id)."""
    db = get_db_write()
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        ep_id = episode_ids.get(name_key)
        if not (pid and ep_id) or "treatment_plan" not in bp:
            continue
        tp = bp["treatment_plan"]
        key = {"tenant_id": tenant_id, "patient_id": pid,
               "episode_id": ep_id}
        existing = await db.clinical_treatment_plans.find_one(
            key, {"_id": 0, "id": 1},
        )
        plan_id = existing["id"] if existing else str(uuid.uuid4())
        start_dt = datetime.now(timezone.utc) + timedelta(
            days=tp["start_date_offset_days"],
        )
        re_exam_date = None
        if "re_exam_offset_days" in tp:
            re_exam_date = (
                datetime.now(timezone.utc) + timedelta(
                    days=tp["re_exam_offset_days"],
                )
            ).date().isoformat()
        # Assign deterministic UUIDs to goals so reseeding doesn't churn them.
        goals = []
        for i, g in enumerate(tp["goals"]):
            goals.append({
                "id": g.get("id") or str(uuid.uuid5(uuid.NAMESPACE_DNS, f"goal-{plan_id}-{i}")),
                **{k: v for k, v in g.items() if k != "id"},
            })
        doc = {
            **key, "id": plan_id,
            "location_id": location_id,
            "responsible_provider_id": lead_doc_id,
            "plan_status": tp.get("plan_status", "active"),
            "title": tp["title"],
            "diagnosis_ids": dx_map.get(name_key, []),
            "target_body_regions": tp.get("target_body_regions", []),
            "frequency_visits_per_week": tp.get("frequency_visits_per_week"),
            "frequency_total_visits": tp.get("frequency_total_visits"),
            "expected_duration_weeks": tp.get("expected_duration_weeks"),
            "start_date": _iso(start_dt),
            "re_exam_date": re_exam_date,
            "planned_interventions": tp.get("planned_interventions", []),
            "goals": goals,
            "baselines": tp.get("baselines", {}),
            "home_care_recommendations": tp.get("home_care_recommendations"),
            "activity_work_recommendations": tp.get("activity_work_recommendations"),
            "discharge_criteria": tp.get("discharge_criteria"),
            "maintenance_transition_notes": tp.get("maintenance_transition_notes"),
            "discharge_reason": tp.get("discharge_reason"),
            "discharged_at": (
                _iso(start_dt + timedelta(weeks=tp.get("expected_duration_weeks", 12)))
                if tp.get("plan_status") in ("completed", "discharged") else None
            ),
            "updated_at": _now(),
            "updated_by": lead_doc_id,
        }
        if existing:
            await db.clinical_treatment_plans.update_one(
                {"id": plan_id}, {"$set": doc},
            )
        else:
            doc["created_at"] = _now()
            doc["created_by"] = lead_doc_id
            await db.clinical_treatment_plans.insert_one(doc)


async def _upsert_history(
    tenant_id: str,
    patients_by_name: dict[tuple[str, str], str],
    lead_doc_id: str | None,
) -> None:
    """`clinical_history` — one per patient, keyed on patient_id."""
    db = get_db_write()
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        if not pid:
            continue
        key = {"tenant_id": tenant_id, "patient_id": pid}
        existing = await db.clinical_history.find_one(key, {"_id": 0, "id": 1})
        hx_id = existing["id"] if existing else str(uuid.uuid4())
        hist = bp.get("history", {})
        field_meta = {k: {"source": "intake"} for k in hist.keys()}
        doc = {
            **key, "id": hx_id,
            **hist,
            "field_meta": field_meta,
            "seeded_from_form_id": None,
            "last_imported_at": _now(),
            "updated_at": _now(),
            "updated_by": lead_doc_id,
        }
        if existing:
            await db.clinical_history.update_one({"id": hx_id}, {"$set": doc})
        else:
            doc["created_at"] = _now()
            doc["created_by"] = lead_doc_id
            await db.clinical_history.insert_one(doc)


async def _upsert_intake_forms(
    tenant_id: str, location_id: str | None,
    patients_by_name: dict[tuple[str, str], str],
    lead_doc_id: str | None,
) -> None:
    """One completed intake form per persona so the Intake tab's
    "Intake forms" list is populated. Idempotent on
    (patient_id, source='demo_seed') — `source` is an internal
    provenance marker that never surfaces to the UI (vs. `notes`,
    which the Intake tab renders verbatim)."""
    db = get_db_write()
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        if not pid:
            continue
        # New canonical idempotency key.
        key = {"tenant_id": tenant_id, "patient_id": pid,
               "source": "demo_seed"}
        existing = await db.patient_intake_forms.find_one(
            key, {"_id": 0, "id": 1},
        ) or await db.patient_intake_forms.find_one(
            # Legacy rows used `notes="demo_seed"` — match them
            # once so the reseed updates in place instead of
            # creating a duplicate.
            {"tenant_id": tenant_id, "patient_id": pid,
             "notes": "demo_seed"},
            {"_id": 0, "id": 1},
        )
        form_id = existing["id"] if existing else str(uuid.uuid4())
        # Both blobs are stored already-encrypted because the patient
        # fields are too — the intake-forms reader uses the same
        # decryption pass as the patient doc.
        enc_intake = encrypt_patient_doc(
            {"clinical_intake": bp.get("patient_intake", {})}
        )["clinical_intake"]
        enc_case = encrypt_patient_doc(
            {"case_details": bp.get("case_details", {})}
        )["case_details"]
        # Captured 1–3 days before the episode onset so the timeline
        # makes clinical sense (intake before care).
        captured_offset = bp["episode"].get("start_date_offset_days", -7) - 1
        captured_dt = datetime.now(timezone.utc) + timedelta(
            days=captured_offset,
        )
        doc = {
            **key, "id": form_id,
            # `notes` is rendered to the user — keep it empty for
            # seeded rows so no internal marker leaks into the chart.
            "notes": "",
            "location_id": location_id,
            "status": "completed",
            "version": 1,
            "captured_by": lead_doc_id,
            "captured_at": _iso(captured_dt),
            "clinical_intake": enc_intake,
            "case_details": enc_case,
            "updated_at": _now(),
        }
        if existing:
            await db.patient_intake_forms.update_one(
                {"id": form_id}, {"$set": doc},
            )
        else:
            doc["created_at"] = _now()
            await db.patient_intake_forms.insert_one(doc)


async def _upsert_outcomes(
    tenant_id: str, location_id: str | None,
    patients_by_name: dict[tuple[str, str], str],
    episode_ids: dict[tuple[str, str], str],
    lead_doc_id: str | None,
) -> None:
    """Outcome measure entries (Oswestry/NDI/NPRS/QuickDASH). Idempotent on
    (patient_id, label, captured_at)."""
    db = get_db_write()
    # Map human labels → the constrained `measure_type` vocabulary the
    # clinical router's OutcomeEntryCreate model enforces.
    def _infer_type(label: str) -> str:
        k = label.lower()
        if "neck disability" in k or k.startswith("ndi"):
            return "ndi"
        if "oswestry" in k or k == "odi":
            return "oswestry"
        if "vas" in k or "numeric pain" in k or "nprs" in k or "pain rating" in k:
            return "pain_vas"
        if "pain scale" in k or "pain_scale" in k:
            return "pain_scale"
        if "dash" in k or "functional" in k or "bournemouth" in k:
            return "functional_index"
        return "custom"

    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        if not pid:
            continue
        ep_id = episode_ids.get(name_key)
        for spec in bp.get("outcomes", []):
            captured_dt = datetime.now(timezone.utc) + timedelta(
                days=spec["recorded_offset_days"],
            )
            captured_at = _iso(captured_dt)
            label = spec["measure"]
            key = {
                "tenant_id": tenant_id, "patient_id": pid,
                "label": label, "captured_at": captured_at,
            }
            existing = await db.clinical_outcome_entries.find_one(
                key, {"_id": 0, "id": 1},
            )
            out_id = existing["id"] if existing else str(uuid.uuid4())
            doc = {
                **key, "id": out_id,
                "episode_id": ep_id,
                "measure_type": _infer_type(label),
                "score": spec["score"],
                "max_score": spec.get("max_score"),
                "unit": spec.get("unit"),
                "source": "provider_charted",
                "note": spec.get("notes"),
                "updated_at": _now(),
                "updated_by": lead_doc_id,
            }
            if existing:
                await db.clinical_outcome_entries.update_one(
                    {"id": out_id}, {"$set": doc},
                )
            else:
                doc["created_at"] = _now()
                doc["created_by"] = lead_doc_id
                await db.clinical_outcome_entries.insert_one(doc)


async def _write_patient_intake_fields(
    tenant_id: str,
    patients_by_name: dict[tuple[str, str], str],
) -> None:
    """Populate the Intake-wizard grouped sections (`clinical_intake`,
    `case_details`) on each patient doc. These are field-encrypted JSON
    blobs; use `encrypt_patient_doc` so the masking layer keeps working.
    """
    db = get_db_write()
    for name_key, bp in CHART_BLUEPRINT.items():
        pid = patients_by_name.get(name_key)
        if not pid:
            continue
        updates = {}
        if "patient_intake" in bp:
            enc = encrypt_patient_doc({"clinical_intake": bp["patient_intake"]})
            updates["clinical_intake"] = enc["clinical_intake"]
        if "case_details" in bp:
            enc = encrypt_patient_doc({"case_details": bp["case_details"]})
            updates["case_details"] = enc["case_details"]
        if updates:
            updates["updated_at"] = _now()
            await db.patients.update_one({"id": pid}, {"$set": updates})


# ---------------------------------------------------------------------------
async def seed_demo_clinical_charts(
    tenant_id: str, location_id: str | None,
    lead_doc_id: str | None,
) -> None:
    """Drive the full chart seed for every persona in CHART_BLUEPRINT.

    Called from `seed_demo_clinic()` after personas + staff are in
    place so the patient + provider IDs are resolvable.
    """
    db = get_db_write()
    patients_by_name: dict[tuple[str, str], str] = {}
    for name_key in CHART_BLUEPRINT.keys():
        first, last = name_key
        p = await db.patients.find_one(
            {"tenant_id": tenant_id, "first_name": first, "last_name": last},
            {"_id": 0, "id": 1},
        )
        if p:
            patients_by_name[name_key] = p["id"]

    episode_ids = await _upsert_episodes(
        tenant_id, location_id, patients_by_name, lead_doc_id,
    )
    dx_map = await _upsert_diagnoses(
        tenant_id, patients_by_name, episode_ids, lead_doc_id,
    )
    await _upsert_treatment_plans(
        tenant_id, location_id, patients_by_name,
        episode_ids, dx_map, lead_doc_id,
    )
    await _upsert_history(tenant_id, patients_by_name, lead_doc_id)
    await _upsert_outcomes(
        tenant_id, location_id, patients_by_name,
        episode_ids, lead_doc_id,
    )
    await _upsert_intake_forms(
        tenant_id, location_id, patients_by_name, lead_doc_id,
    )
    await _write_patient_intake_fields(tenant_id, patients_by_name)
    logger.info(
        "demo.clinical_seed complete: %d patients, %d episodes, %d dx rows",
        len(patients_by_name),
        len(episode_ids),
        sum(len(v) for v in dx_map.values()),
    )
