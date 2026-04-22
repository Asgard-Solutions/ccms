"""
Demo / showcase seed for Riverbend Chiropractic & Wellness.

Idempotent. Runs on every boot after `seed_tenancy()` and
`seed_identity()` so the primary demo tenant always has:

  * A realistic staff roster (clinic owner, associate chiropractor,
    office manager, front desk, billing specialist, chiropractic
    assistant) — in addition to the login-helper demo accounts that
    `identity/seed.py` already creates.
  * A diverse catalog of fictional patient personas spanning the
    workflows product demos care about:

        - Ethan Parker       — self-pay wellness / maintenance
        - Hannah Whitaker    — new acute neck pain (commercial BCBS)
        - Marcus Reid        — chronic low-back (Medicare-age)
        - Isabella Cho       — auto accident / personal injury
        - Derrick Stone      — workers' comp
        - Aria Johnson       — active adult / runner (commercial)
        - Claire Morgan      — guarantor / family head
        - Jaxon Morgan       — minor dependent of Claire

  * Fictionalised but safe-looking payer rows (commercial, Medicare,
    workers' comp, auto / PIP) with the existing ClearinghouseRoute
    defaults so billing / claims workflows light up without needing
    manual setup.

  * Insurance policies tied to the personas above (primary + one
    secondary example, family dependent, workers' comp, auto / PIP).

  * A one-week appointment board spanning new-patient evals, routine
    adjustments, re-exams, therapy, follow-ups, one canceled slot,
    and completed visits — distributed across recent past, today,
    and the next 5 days so the calendar never looks empty.

  * A clinical note for each persona that reads like real
    chiropractic documentation (chief complaint, subjective,
    objective, assessment, plan). PHI fields are encrypted at rest.

Everything is keyed idempotently — the seed upserts on (tenant_id +
stable business key such as email / NPI / policy member_id) so
re-running after an upgrade never duplicates rows, and fields get
refreshed in place.

GUARDRAIL: no real person data, no real PHI. Fictional names /
addresses / phones only. Phone numbers use the 555-01xx block that
NANP reserves for fiction.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from core.crypto import encrypt_text
from core.db import get_db_write
from core.security import hash_password
from services.patient._shared import encrypt_patient_value

logger = logging.getLogger("ccms.demo.seed")

_DEFAULT_TENANT_SLUG = "default"
_DEMO_PASSWORD = "Riverbend@ComplianceClinic1"  # meets policy; staff accounts


# ---------------------------------------------------------------------------
# Staff roster — one realistic person per role the UI cares about.
# ---------------------------------------------------------------------------
_STAFF = [
    {
        "email": "olivia.hart@riverbend-chiro.app",
        "name": "Olivia Hart",
        "display_name": "Olivia Hart",
        "role": "admin",
        "title": "Clinic Owner",
        "credentials": "MBA",
        "phone": "+1-503-555-0121",
        "tenant_scope_all": True,
    },
    {
        "email": "dr.samuel.ito@riverbend-chiro.app",
        "name": "Dr. Samuel Ito",
        "display_name": "Dr. Samuel Ito, DC",
        "role": "doctor",
        "title": "Associate Chiropractor",
        "credentials": "DC",
        "npi": "1730598210",
        "phone": "+1-503-555-0133",
        "tenant_scope_all": False,
    },
    {
        "email": "lena.brooks@riverbend-chiro.app",
        "name": "Lena Brooks",
        "display_name": "Lena Brooks",
        "role": "staff",
        "title": "Office Manager",
        "phone": "+1-503-555-0144",
        "tenant_scope_all": False,
    },
    {
        "email": "tomas.rivera@riverbend-chiro.app",
        "name": "Tomás Rivera",
        "display_name": "Tomás Rivera",
        "role": "staff",
        "title": "Billing Specialist",
        "phone": "+1-503-555-0155",
        "tenant_scope_all": False,
    },
    {
        "email": "priya.shah@riverbend-chiro.app",
        "name": "Priya Shah",
        "display_name": "Priya Shah",
        "role": "staff",
        "title": "Chiropractic Assistant",
        "phone": "+1-503-555-0166",
        "tenant_scope_all": False,
    },
]


# ---------------------------------------------------------------------------
# Payer catalog — safely fictional but recognisable shapes.
# ---------------------------------------------------------------------------
_PAYERS = [
    {
        "name": "PacificCare Commercial",
        "payer_type": "commercial",
        "payer_code": "PAC-COMM",
        "electronic_payer_id": "PAC1234",
        "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
        "claim_submission_mode": "edi",
        "enrollment_status": "enrolled",
    },
    {
        "name": "Cascade Blue Shield",
        "payer_type": "commercial",
        "payer_code": "CBS-COMM",
        "electronic_payer_id": "CBS4451",
        "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
        "claim_submission_mode": "edi",
        "enrollment_status": "enrolled",
    },
    {
        "name": "Medicare — Oregon",
        "payer_type": "medicare",
        "payer_code": "MCR-OR",
        "electronic_payer_id": "MCRA01",
        "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
        "claim_submission_mode": "edi",
        "enrollment_status": "enrolled",
        "requires_at_modifier": True,
        "requires_subluxation_primary": True,
        "requires_initial_treatment_date": True,
    },
    {
        "name": "Oregon SAIF Workers' Comp",
        "payer_type": "workers_comp",
        "payer_code": "SAIF-WC",
        "electronic_payer_id": "SAIF87",
        "remit_method": "paper_eob",
        "clearinghouse_route": "none",
        "claim_submission_mode": "portal",
        "enrollment_status": "not_started",
    },
    {
        "name": "Northwest Auto PIP",
        "payer_type": "auto",
        "payer_code": "NWA-PIP",
        "electronic_payer_id": "NWA0099",
        "remit_method": "paper_eob",
        "clearinghouse_route": "none",
        "claim_submission_mode": "portal",
        "enrollment_status": "not_started",
    },
    {
        "name": "Self-Pay",
        "payer_type": "self_pay",
        "payer_code": "SELF",
        "remit_method": "none",
        "clearinghouse_route": "none",
        "claim_submission_mode": "portal",
        "enrollment_status": "not_started",
    },
]


# ---------------------------------------------------------------------------
# Structured address + emergency-contact data — the Edit patient wizard
# reads from `address_details.{line1,city,state,postal_code}` and
# `emergency_contact_details.{name,relationship,phone}`, so we must
# persist both the structured form (for the Edit form) *and* the legacy
# flat string (for backward-compatibility with older callers).
#
# Keyed on (first_name, last_name) — unique across the demo personas.
# ---------------------------------------------------------------------------
_ADDRESS_BY_NAME: dict[tuple[str, str], dict] = {
    ("Hannah", "Whitaker"): {
        "line1": "1124 SE Ankeny St", "line2": None,
        "city": "Portland", "state": "OR",
        "postal_code": "97214", "country": "USA",
    },
    ("Marcus", "Reid"): {
        "line1": "2208 NE Alberta St", "line2": None,
        "city": "Portland", "state": "OR",
        "postal_code": "97211", "country": "USA",
    },
    ("Isabella", "Cho"): {
        "line1": "3418 SE Division St", "line2": "Unit 2",
        "city": "Portland", "state": "OR",
        "postal_code": "97202", "country": "USA",
    },
    ("Derrick", "Stone"): {
        "line1": "507 NE Killingsworth St", "line2": None,
        "city": "Portland", "state": "OR",
        "postal_code": "97211", "country": "USA",
    },
    ("Aria", "Johnson"): {
        "line1": "9912 SW Capitol Hwy", "line2": "Apt 3C",
        "city": "Portland", "state": "OR",
        "postal_code": "97219", "country": "USA",
    },
    ("Claire", "Morgan"): {
        "line1": "1711 SW Park Ave", "line2": None,
        "city": "Portland", "state": "OR",
        "postal_code": "97201", "country": "USA",
    },
    ("Jaxon", "Morgan"): {
        "line1": "1711 SW Park Ave", "line2": None,
        "city": "Portland", "state": "OR",
        "postal_code": "97201", "country": "USA",
    },
}

_EMERGENCY_BY_NAME: dict[tuple[str, str], dict] = {
    ("Hannah", "Whitaker"): {
        "name": "Nora Whitaker", "relationship": "Mother",
        "phone": "+1-503-555-0219", "phone_alt": None,
        "email": "nora.whitaker@example.com",
    },
    ("Marcus", "Reid"): {
        "name": "Donna Reid", "relationship": "Spouse",
        "phone": "+1-503-555-0177", "phone_alt": None,
        "email": "donna.reid@example.com",
    },
    ("Isabella", "Cho"): {
        "name": "Daniel Cho", "relationship": "Spouse",
        "phone": "+1-503-555-0227", "phone_alt": None,
        "email": "daniel.cho@example.com",
    },
    ("Derrick", "Stone"): {
        "name": "Rachel Stone", "relationship": "Daughter",
        "phone": "+1-503-555-0252", "phone_alt": None,
        "email": "rachel.stone@example.com",
    },
    ("Aria", "Johnson"): {
        "name": "Kim Johnson", "relationship": "Mother",
        "phone": "+1-503-555-0263", "phone_alt": None,
        "email": "kim.johnson@example.com",
    },
    ("Claire", "Morgan"): {
        "name": "Aaron Morgan", "relationship": "Spouse",
        "phone": "+1-503-555-0282", "phone_alt": None,
        "email": "aaron.morgan@example.com",
    },
    ("Jaxon", "Morgan"): {
        # Minor dependent — primary emergency contact is the guardian.
        "name": "Claire Morgan", "relationship": "Mother",
        "phone": "+1-503-555-0280", "phone_alt": "+1-503-555-0282",
        "email": "claire.morgan@example.com",
    },
    # Ethan Parker lives on the identity/seed.py path but we keep his
    # structured address + emergency contact here so both seeders share
    # one source of truth. identity/seed.py imports this dict.
    ("Ethan", "Parker"): {
        "name": "Sarah Parker", "relationship": "Spouse",
        "phone": "+1-503-555-0191", "phone_alt": None,
        "email": "sarah.parker@example.com",
    },
}

_ADDRESS_BY_NAME[("Ethan", "Parker")] = {
    "line1": "842 NW Lovejoy St", "line2": "Apt 4B",
    "city": "Portland", "state": "OR",
    "postal_code": "97209", "country": "USA",
}


# ---------------------------------------------------------------------------
# Clinic profile — the "Settings > Clinic" page reads from
# `clinic_profiles` keyed on (tenant_id, location_id). We populate one
# realistic profile for the default location so the config screens feel
# like a real clinic (not a blank "configure me" shell).
# ---------------------------------------------------------------------------
_CLINIC_PROFILE = {
    "name": "Riverbend Chiropractic & Wellness",
    "address_line1": "1840 NW Riverside Dr",
    "address_line2": "Suite 210",
    "city": "Portland",
    "state": "OR",
    "postal_code": "97209",
    "country": "US",
    # Stored as 10-digit canonical (matches `core.phone.normalize_us_phone`
    # output) so the frontend `formatPhoneDisplay` can render it as
    # `(503) 555-0100`. Never store pre-formatted `+1-503-555-0100`
    # here — it defeats the frontend's 10-digit canonicalisation path.
    "primary_phone": "5035550100",
    "secondary_phone": "5035550101",
    "email": "hello@riverbend-chiro.app",
    "website": "https://riverbend-chiro.app",
    "timezone": "America/Los_Angeles",
    "notes": (
        "Full-service chiropractic and wellness clinic specializing in "
        "acute injury care, auto accident / PIP, workers' comp, and "
        "maintenance wellness. In-house x-ray, massage therapy, and "
        "rehab exercise space."
    ),
    # Mon–Fri 08:00–12:00, 13:00–18:00 (lunch break); Sat 09:00–13:00;
    # Sun closed. Models.DayHours.day_of_week is 0=Mon..6=Sun.
    "hours": [
        {"day_of_week": 0, "is_closed": False, "intervals": [
            {"open_time": "08:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "18:00"},
        ]},
        {"day_of_week": 1, "is_closed": False, "intervals": [
            {"open_time": "08:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "18:00"},
        ]},
        {"day_of_week": 2, "is_closed": False, "intervals": [
            {"open_time": "08:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "18:00"},
        ]},
        {"day_of_week": 3, "is_closed": False, "intervals": [
            {"open_time": "08:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "18:00"},
        ]},
        {"day_of_week": 4, "is_closed": False, "intervals": [
            {"open_time": "08:00", "close_time": "12:00"},
            {"open_time": "13:00", "close_time": "17:00"},
        ]},
        {"day_of_week": 5, "is_closed": False, "intervals": [
            {"open_time": "09:00", "close_time": "13:00"},
        ]},
        {"day_of_week": 6, "is_closed": True, "intervals": []},
    ],
}


# ---------------------------------------------------------------------------
# Appointment types — visit-type catalog the Book Appointment dialog
# reads to prefill duration + reason strings. Ordered via sort_order so
# the most common visit types (adjustment, follow-up) surface first.
# ---------------------------------------------------------------------------
_APPOINTMENT_TYPES = [
    {"name": "Chiropractic Adjustment",
     "default_duration_minutes": 15, "sort_order": 10,
     "description": "Routine spinal adjustment for established patients.",
     "default_follow_up_days": 7},
    {"name": "Follow-up Visit",
     "default_duration_minutes": 30, "sort_order": 20,
     "description": "Progress check with treatment rendered.",
     "default_follow_up_days": 7},
    {"name": "New Patient Exam",
     "default_duration_minutes": 60, "sort_order": 30,
     "description": "Initial consultation + exam + first adjustment for new patients.",
     "default_follow_up_days": 3},
    {"name": "Re-Exam",
     "default_duration_minutes": 30, "sort_order": 40,
     "description": "Scheduled progress re-evaluation against the treatment plan.",
     "default_follow_up_days": None},
    {"name": "Therapy / Modality",
     "default_duration_minutes": 20, "sort_order": 50,
     "description": "Soft-tissue work, TENS, IASTM, or other supporting modality.",
     "default_follow_up_days": 7},
    {"name": "Auto Injury / PIP Evaluation",
     "default_duration_minutes": 45, "sort_order": 60,
     "description": "Initial evaluation for MVA / PIP cases; adjuster coordination.",
     "default_follow_up_days": 3},
    {"name": "Workers' Comp Evaluation",
     "default_duration_minutes": 45, "sort_order": 70,
     "description": "Initial evaluation for on-the-job injuries; WC reporting.",
     "default_follow_up_days": 3},
    {"name": "Maintenance / Wellness Visit",
     "default_duration_minutes": 15, "sort_order": 80,
     "description": "Self-pay maintenance adjustment for established patients.",
     "default_follow_up_days": 30},
    {"name": "Pediatric Visit",
     "default_duration_minutes": 15, "sort_order": 90,
     "description": "Gentle adjustment for pediatric patients; parent present.",
     "default_follow_up_days": 14},
]


# ---------------------------------------------------------------------------
# Rooms / exam spaces for the default location.
# ---------------------------------------------------------------------------
_ROOMS = [
    {"name": "Exam 1", "type": "exam", "sort_order": 10,
     "notes": "Primary exam room (front)."},
    {"name": "Exam 2", "type": "exam", "sort_order": 20,
     "notes": "Secondary exam room (hallway east)."},
    {"name": "Adjustment 1", "type": "exam", "sort_order": 30,
     "notes": "Open-bay adjustment table with drop piece."},
    {"name": "Adjustment 2", "type": "exam", "sort_order": 40,
     "notes": "Open-bay adjustment table — flexion/distraction."},
    {"name": "Consult Room", "type": "consult", "sort_order": 50,
     "notes": "Report-of-findings / consultation room."},
    {"name": "X-Ray Suite", "type": "xray", "sort_order": 60,
     "notes": "In-house digital x-ray (shielded)."},
    {"name": "Therapy Bay", "type": "therapy", "sort_order": 70,
     "notes": "Modalities, rehab exercise, and soft-tissue work."},
]
_PERSONAS = [
    {
        "first_name": "Hannah", "middle_name": "Rose", "last_name": "Whitaker",
        "preferred_name": "Hannah", "date_of_birth": "1992-03-04",
        "gender": "female", "pronouns": "she/her", "marital_status": "single",
        "language": "English", "occupation": "UX Designer",
        "employer": "Oregon Design Studio",
        "employer_phone": "+1-503-555-0211",
        "email": "hannah.whitaker@example.com",
        "phone": "+1-503-555-0210", "phone_work": "+1-503-555-0211",
        "address": "1124 SE Ankeny St, Portland, OR 97214",
        "emergency_contact": "Nora Whitaker (mother) — +1-503-555-0219",
        "referral_source": "Google search",
        "persona": "acute_neck_pain",
        "payer_code": "CBS-COMM",
        "policy": {"member_id": "CBS1992-H04", "group": "ORDS-0417",
                   "copay_cents": 3000, "deductible_cents": 150000},
        "clinical": {
            "chief_complaint":
                "Sharp left-sided neck pain radiating into trapezius; "
                "3 days post wake-up. 6/10 at rest, 8/10 with left "
                "rotation.",
            "subjective":
                "Works ~8 hrs/day at a dual-monitor setup. Slept on a "
                "hotel pillow Sunday, woke up Monday with stiffness. "
                "Ibuprofen 400mg bid — partial relief.",
            "objective":
                "Cervical ROM: flexion 40° (full), extension 30°, "
                "left rotation 45° (guarded), right rotation 75°. "
                "Tenderness over left C5-C6 facets and upper trap. "
                "No neurologic deficit; Spurling's negative.",
            "assessment":
                "Acute cervical facet joint sprain (M54.2) with "
                "associated myofascial dysfunction. No red flags.",
            "plan":
                "Diversified CMT C5-C6 (98940), myofascial release "
                "upper trap, recommend 6-visit plan over 2 weeks. "
                "Postural education + monitor screen ergonomics. "
                "Re-exam at visit 6.",
        },
    },
    {
        "first_name": "Marcus", "middle_name": "Lee", "last_name": "Reid",
        "preferred_name": "Marcus", "date_of_birth": "1958-07-21",
        "gender": "male", "pronouns": "he/him", "marital_status": "married",
        "language": "English", "occupation": "Retired foreman",
        "email": "marcus.reid@example.com",
        "phone": "+1-503-555-0175", "phone_alt": "+1-503-555-0176",
        "address": "2208 NE Alberta St, Portland, OR 97211",
        "emergency_contact": "Donna Reid (spouse) — +1-503-555-0177",
        "referral_source": "Referred by PCP (Dr. Amin)",
        "persona": "chronic_lbp_medicare",
        "payer_code": "MCR-OR",
        "policy": {"member_id": "1EG4-TE5-MK72", "group": None,
                   "copay_cents": 0, "deductible_cents": 24000},
        "clinical": {
            "chief_complaint":
                "Chronic low back pain, worsening over the past 8 "
                "weeks. 5/10 baseline, 7/10 after yard work.",
            "subjective":
                "Retired construction foreman. History of heavy "
                "lifting. Previous CMT course in 2022 helped. Denies "
                "leg pain or bowel/bladder changes.",
            "objective":
                "Lumbar flexion 50°, extension 20° (painful). "
                "Palpable hypomobility L4-L5 and sacroiliac joints. "
                "SLR negative bilaterally. DTRs 2+ and symmetric.",
            "assessment":
                "Subluxation complex, lumbar region (M99.03) with "
                "chronic lumbar strain (M54.50). Active-treatment "
                "episode re-opened.",
            "plan":
                "Medicare active-treatment plan, 12 visits over 6 "
                "weeks. Initial treatment date set today; AT modifier "
                "required every visit. Re-exam at visit 12.",
        },
        "initial_treatment_today": True,
    },
    {
        "first_name": "Isabella", "middle_name": "Marie", "last_name": "Cho",
        "preferred_name": "Bella", "date_of_birth": "1984-11-12",
        "gender": "female", "pronouns": "she/her", "marital_status": "married",
        "language": "English", "occupation": "Middle-school Teacher",
        "employer": "Portland Public Schools",
        "employer_phone": "+1-503-555-0226",
        "email": "bella.cho@example.com",
        "phone": "+1-503-555-0225",
        "address": "3418 SE Division St, Portland, OR 97202",
        "emergency_contact": "Daniel Cho (spouse) — +1-503-555-0227",
        "referral_source": "Attorney referral — Parker & Associates",
        "persona": "auto_accident_pip",
        "payer_code": "NWA-PIP",
        "policy": {"member_id": "NWA-PIP-7741-Q", "group": "CLAIM-2026-00118",
                   "copay_cents": 0, "deductible_cents": 0,
                   "adjuster_name": "Angela Price", "adjuster_phone": "+1-503-555-0228"},
        "clinical": {
            "chief_complaint":
                "Neck + mid-back pain after rear-end MVA 6 days ago. "
                "4/10 neck, 5/10 thoracic, headache 3/10.",
            "subjective":
                "Low-speed rear-end collision at a stoplight. Belted, "
                "airbag did not deploy. ER eval same day — no "
                "fracture. Pain onset within 12 hours. Limited work "
                "since.",
            "objective":
                "Cervical ROM globally reduced ~30%. Palpable spasm "
                "bilateral paraspinals C5-T6. Spurling's negative. "
                "Thoracic segmental hypomobility T4-T6.",
            "assessment":
                "Cervical strain (S13.4XXA) and thoracic strain "
                "(S23.3XXA) secondary to MVA. PIP case.",
            "plan":
                "3x/week x 4 weeks, then 2x/week x 2 weeks, then "
                "re-evaluate. Diversified CMT, manual therapy, "
                "cryotherapy. Coordinate progress notes with PIP "
                "adjuster monthly.",
        },
        "accident_date_days_ago": 6,
    },
    {
        "first_name": "Derrick", "middle_name": "Wayne", "last_name": "Stone",
        "preferred_name": "Derrick", "date_of_birth": "1972-05-30",
        "gender": "male", "pronouns": "he/him", "marital_status": "divorced",
        "language": "English", "occupation": "Warehouse Supervisor",
        "employer": "Cascade Freight Logistics",
        "employer_phone": "+1-503-555-0251",
        "email": "derrick.stone@example.com",
        "phone": "+1-503-555-0250",
        "address": "507 NE Killingsworth St, Portland, OR 97211",
        "emergency_contact": "Rachel Stone (daughter) — +1-503-555-0252",
        "referral_source": "Employer workers' comp program",
        "persona": "workers_comp",
        "payer_code": "SAIF-WC",
        "policy": {"member_id": "SAIF-WC-22-8841", "group": "WC-CLM-88410",
                   "copay_cents": 0, "deductible_cents": 0,
                   "adjuster_name": "Greg Fuentes", "adjuster_phone": "+1-503-555-0253"},
        "clinical": {
            "chief_complaint":
                "Low back injury lifting a 65 lb pallet at work "
                "3 days ago. Sharp pain radiating into right glute.",
            "subjective":
                "Felt a 'pop' on the lift, has been unable to return "
                "to full duty. Reported to manager same day; WC claim "
                "opened. No prior back injuries.",
            "objective":
                "Antalgic gait favoring right. Lumbar flexion 40° "
                "(guarded). SLR positive on right at 60°. Right SI "
                "joint tenderness. No motor weakness.",
            "assessment":
                "Acute lumbar strain with right SI joint involvement "
                "(S33.5XXA). Work-related — WC primary.",
            "plan":
                "3x/week x 3 weeks. CMT lumbar + pelvic, manual "
                "therapy, TENS. Modified-duty note issued: no "
                "lifting >15 lb. Progress reports to SAIF adjuster "
                "every 2 weeks.",
        },
        "accident_date_days_ago": 3,
    },
    {
        "first_name": "Aria", "last_name": "Johnson",
        "preferred_name": "Aria", "date_of_birth": "1997-02-18",
        "gender": "female", "pronouns": "she/her", "marital_status": "single",
        "language": "English", "occupation": "Marketing Manager",
        "employer": "Summit Outdoor Co",
        "email": "aria.johnson@example.com",
        "phone": "+1-503-555-0261", "phone_work": "+1-503-555-0262",
        "address": "9912 SW Capitol Hwy, Portland, OR 97219",
        "emergency_contact": "Kim Johnson (mother) — +1-503-555-0263",
        "referral_source": "Teammate at Stumptown Running Club",
        "persona": "athlete",
        "payer_code": "PAC-COMM",
        "policy": {"member_id": "PAC-ARI-2601", "group": "SUMOUT-HR",
                   "copay_cents": 2500, "deductible_cents": 100000},
        "clinical": {
            "chief_complaint":
                "Right-sided hip and IT band tightness after "
                "ramping marathon training. 3/10 at rest, 5/10 at "
                "mile 10.",
            "subjective":
                "Training for Portland Marathon in October. ~45 "
                "miles/week. Noticed tightness after adding hill "
                "repeats 2 weeks ago.",
            "objective":
                "Right hip external rotation limited ~20% vs left. "
                "Positive Ober's test. TFL and glute medius "
                "tenderness. Lumbar exam unremarkable.",
            "assessment":
                "Right IT band syndrome with pelvic asymmetry "
                "(M76.30). Overuse / training-load related.",
            "plan":
                "2x/week x 3 weeks. CMT sacroiliac + lumbar, manual "
                "therapy, IASTM to TFL/ITB. Home mobility program. "
                "Cut mileage 20% for 1 week, reintroduce hills "
                "gradually.",
        },
    },
    {
        # Guarantor / family-head persona — has a minor dependent below.
        "first_name": "Claire", "last_name": "Morgan",
        "preferred_name": "Claire", "date_of_birth": "1986-09-09",
        "gender": "female", "pronouns": "she/her", "marital_status": "married",
        "language": "English", "occupation": "Operations Director",
        "employer": "Riverpoint Biotech",
        "employer_phone": "+1-503-555-0281",
        "email": "claire.morgan@example.com",
        "phone": "+1-503-555-0280",
        "address": "1711 SW Park Ave, Portland, OR 97201",
        "emergency_contact": "Aaron Morgan (spouse) — +1-503-555-0282",
        "referral_source": "Family referral (neighbor)",
        "persona": "family_head",
        "payer_code": "PAC-COMM",
        "policy": {"member_id": "PAC-MOR-1009", "group": "RPBIO-HR",
                   "copay_cents": 2500, "deductible_cents": 250000},
        "clinical": {
            "chief_complaint":
                "Mid-back tension from desk work and carrying child "
                "gear. 4/10 intermittent, worse by Thursday.",
            "subjective":
                "Works from home 3 days/week. Two young kids, lots "
                "of car-seat lifting. No prior chiropractic care.",
            "objective":
                "Thoracic segmental hypomobility T3-T5. Bilateral "
                "upper trap tightness. Posture: mild forward head.",
            "assessment":
                "Thoracic myofascial dysfunction with segmental "
                "restrictions (M54.6). Deconditioning-related.",
            "plan":
                "Diversified CMT thoracic + cervical, manual "
                "therapy to upper traps. 1x/week x 4 weeks; add "
                "scapular stability exercises.",
        },
    },
    {
        "first_name": "Jaxon", "last_name": "Morgan",
        "preferred_name": "Jax", "date_of_birth": "2014-06-02",
        "gender": "male", "pronouns": "he/him", "marital_status": "single",
        "language": "English", "occupation": "Student",
        "email": "claire.morgan@example.com",    # guardian contact
        "phone": "+1-503-555-0280",
        "address": "1711 SW Park Ave, Portland, OR 97201",
        "emergency_contact": "Claire Morgan (mother) — +1-503-555-0280",
        "referral_source": "Parent (Claire Morgan)",
        "persona": "minor_dependent",
        "guarantor_email": "claire.morgan@example.com",
        "payer_code": "PAC-COMM",
        # Shares the guarantor's policy as a dependent
        "policy": {"member_id": "PAC-MOR-1009", "group": "RPBIO-HR",
                   "copay_cents": 2500, "deductible_cents": 250000,
                   "relationship_to_subscriber": "child"},
        "clinical": {
            "chief_complaint":
                "Mild upper-back soreness after new gymnastics class "
                "last week. 2/10, no radicular symptoms.",
            "subjective":
                "11-year-old. New gymnastics program — bar work. "
                "Mom noticed complaint for 3 days. Otherwise healthy.",
            "objective":
                "Mild thoracic T3-T4 segmental restriction. No "
                "scoliosis. Full spinal ROM. Neuro intact.",
            "assessment":
                "Pediatric thoracic myofascial strain — activity "
                "related (M54.6).",
            "plan":
                "Gentle pediatric CMT T3-T4. 2 visits, reassess. "
                "Parent education on warm-up routine.",
        },
    },
]


# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_default_tenant() -> tuple[str | None, str | None]:
    db = get_db_write()
    tenant = await db.tenants.find_one({"slug": _DEFAULT_TENANT_SLUG}, {"_id": 0, "id": 1})
    if not tenant:
        return None, None
    loc = await db.locations.find_one(
        {"tenant_id": tenant["id"]}, {"_id": 0, "id": 1},
    )
    return tenant["id"], (loc["id"] if loc else None)


async def _upsert_staff(tenant_id: str) -> dict[str, str]:
    db = get_db_write()
    now = _now()
    id_by_email: dict[str, str] = {}
    hashed = hash_password(_DEMO_PASSWORD)
    for spec in _STAFF:
        existing = await db.users.find_one({"email": spec["email"]}, {"_id": 0})
        base = {
            "email": spec["email"],
            "name": spec["name"],
            "display_name": spec.get("display_name"),
            "title": spec.get("title"),
            "credentials": spec.get("credentials"),
            "npi": spec.get("npi"),
            "role": spec["role"],
            "phone": spec["phone"],
            "status": "active",
            "tenant_id": tenant_id,
            "tenant_scope_all": spec["tenant_scope_all"],
            "mfa_enabled": False,
            "mfa_policy_required": False,
            "updated_at": now,
        }
        if existing is None:
            user_id = str(uuid.uuid4())
            await db.users.insert_one({
                "id": user_id,
                "password_hash": hashed,
                "password_history": [hashed],
                "password_changed_at": now,
                "session_epoch": 0,
                "created_at": now,
                **base,
            })
        else:
            user_id = existing["id"]
            await db.users.update_one({"id": user_id}, {"$set": base})
        id_by_email[spec["email"]] = user_id
    return id_by_email


async def _upsert_payers(tenant_id: str) -> dict[str, dict]:
    db = get_db_write()
    now = _now()
    by_code: dict[str, dict] = {}
    for spec in _PAYERS:
        existing = await db.billing_payers.find_one(
            {"tenant_id": tenant_id, "payer_code": spec["payer_code"]},
            {"_id": 0},
        )
        doc = {
            "tenant_id": tenant_id,
            "status": "active",
            "updated_at": now,
            **spec,
        }
        if existing is None:
            doc.update({"id": str(uuid.uuid4()), "created_at": now})
            await db.billing_payers.insert_one(doc)
        else:
            await db.billing_payers.update_one(
                {"id": existing["id"]}, {"$set": doc},
            )
            doc = {**existing, **doc}
        by_code[spec["payer_code"]] = doc
    return by_code


async def _upsert_personas(
    tenant_id: str, location_id: str | None,
    doctor_id: str | None,
) -> dict[str, str]:
    db = get_db_write()
    now = _now()
    id_by_email: dict[str, str] = {}
    for spec in _PERSONAS:
        # Key on (first_name, last_name, email) — stable across reseeds
        # and immune to PHI-encryption of scalar fields like DOB. Minor
        # dependents who share their guardian's email (Jaxon Morgan vs
        # Claire Morgan) stay distinct because their first_name differs.
        key = {
            "tenant_id": tenant_id,
            "first_name": spec["first_name"],
            "last_name": spec["last_name"],
            "email": spec["email"],
        }
        existing = await db.patients.find_one(key, {"_id": 0, "id": 1})
        patient_id = existing["id"] if existing else str(uuid.uuid4())

        address = _ADDRESS_BY_NAME.get(
            (spec["first_name"], spec["last_name"]),
        ) or {}
        emergency = _EMERGENCY_BY_NAME.get(
            (spec["first_name"], spec["last_name"]),
        ) or {}

        # Grouped sections required by the Edit Patient wizard
        # (services/pages/patientWizardLogic.js :: payloadToForm reads
        # from address_details / emergency_contact_details / contact /
        # demographics / admin / guarantor / insurance).
        demographics_group = {
            "first_name": spec["first_name"],
            "middle_name": spec.get("middle_name"),
            "last_name": spec["last_name"],
            "preferred_name": spec.get("preferred_name"),
            "date_of_birth": spec["date_of_birth"],
            "gender": spec["gender"],
            "sex_at_birth": spec["gender"],
            "pronouns": spec.get("pronouns"),
            "marital_status": spec.get("marital_status"),
            "language": spec.get("language"),
            "occupation": spec.get("occupation"),
            "employer": spec.get("employer"),
            "employer_phone": spec.get("employer_phone"),
        }
        contact_group = {
            "phone": spec["phone"],
            "phone_alt": spec.get("phone_alt"),
            "phone_work": spec.get("phone_work"),
            "email": spec["email"],
            "preferred_contact_method": "email",
            "sms_consent": True,
            "email_consent": True,
            "voicemail_consent": True,
        }
        admin_group = {
            "primary_provider_id": doctor_id,
            "referral_source": spec.get("referral_source"),
            "mrn": None,
            "tags": [spec["persona"]],
        }

        # Guarantor — responsible-party=self for every persona except
        # Jaxon (minor dependent), whose guardian is Claire Morgan.
        if spec.get("persona") == "minor_dependent":
            guarantor_group = {
                "same_as_patient": False,
                "first_name": "Claire",
                "last_name": "Morgan",
                "relationship": "Parent",
                "date_of_birth": "1986-09-09",
                "phone": "+1-503-555-0280",
                "email": "claire.morgan@example.com",
                "employer": "Riverpoint Biotech",
                "employer_phone": "+1-503-555-0281",
                "address": (
                    "1711 SW Park Ave, Portland, OR 97201"
                ),
            }
        else:
            guarantor_group = {"same_as_patient": True}

        # Insurance — mirror the policy row so the Edit wizard
        # renders the primary block on load.
        p = spec.get("policy") or {}
        insurance_group: dict | None = None
        if p:
            payer_name = {
                "CBS-COMM": "Cascade Blue Shield",
                "MCR-OR": "Medicare — Oregon",
                "SAIF-WC": "Oregon SAIF Workers' Comp",
                "NWA-PIP": "Northwest Auto PIP",
                "PAC-COMM": "PacificCare Commercial",
            }.get(spec["payer_code"], spec["payer_code"])
            relationship = p.get("relationship_to_subscriber") or (
                "Child" if spec.get("persona") == "minor_dependent"
                else "Self"
            )
            subscriber_name = (
                "Claire Morgan"
                if spec.get("persona") == "minor_dependent"
                else f"{spec['first_name']} {spec['last_name']}"
            )
            insurance_group = {
                "primary": {
                    "carrier": payer_name,
                    "plan_name": p.get("group") or payer_name,
                    "plan_type": {
                        "MCR-OR": "Medicare",
                        "SAIF-WC": "Workers Comp",
                        "NWA-PIP": "Auto / PIP",
                    }.get(spec["payer_code"], "PPO"),
                    "member_id": p["member_id"],
                    "group_number": p.get("group"),
                    "policy_holder_name": subscriber_name,
                    "policy_holder_relationship": relationship,
                    "policy_holder_dob": (
                        "1986-09-09"
                        if spec.get("persona") == "minor_dependent"
                        else spec["date_of_birth"]
                    ),
                    "effective_date": "2026-01-01",
                    "copay": (
                        f"{p['copay_cents'] / 100:.2f}"
                        if p.get("copay_cents") else ""
                    ),
                    "deductible": (
                        f"{p['deductible_cents'] / 100:.2f}"
                        if p.get("deductible_cents") else ""
                    ),
                },
                "secondary": {},
            }

        doc = {
            "id": patient_id,
            "tenant_id": tenant_id,
            "location_id": location_id,
            "user_id": None,
            # Legacy flat scalars (kept so any caller still reading
            # the flat shape keeps working — e.g. pytest fixtures,
            # simple list renderers).
            "first_name": spec["first_name"],
            "middle_name": spec.get("middle_name"),
            "last_name": spec["last_name"],
            "preferred_name": spec.get("preferred_name"),
            "date_of_birth": spec["date_of_birth"],
            "gender": spec["gender"],
            "pronouns": spec.get("pronouns"),
            "marital_status": spec.get("marital_status"),
            "language": spec.get("language"),
            "phone": spec["phone"],
            "phone_alt": spec.get("phone_alt"),
            "phone_work": spec.get("phone_work"),
            "email": spec["email"],
            "preferred_contact_method": "email",
            "occupation": spec.get("occupation"),
            "employer": spec.get("employer"),
            "employer_phone": spec.get("employer_phone"),
            "referral_source": spec.get("referral_source"),
            "primary_provider_id": doctor_id,
            # PHI scalars — encrypted at rest.
            "address": encrypt_text(
                ", ".join(
                    x for x in (
                        address.get("line1"), address.get("line2"),
                        address.get("city"), address.get("state"),
                        address.get("postal_code"),
                    ) if x
                )
            ),
            "emergency_contact": encrypt_text(
                f"{emergency.get('name', '')} "
                f"({emergency.get('relationship', '')}) — "
                f"{emergency.get('phone', '')}"
            ),
            "notes": encrypt_text(
                f"Demo persona: {spec['persona']}. See "
                f"/app/memory/DEMO_SEED.md for full scenario."
            ),
            # Structured grouped sections — read back by the Edit
            # Patient wizard. Encrypted at rest as JSON blobs (per
            # services/patient/_shared.py::PATIENT_SECTION_ENCRYPTED)
            # so the same decrypt pipeline that handles
            # user-edited rows also handles our seeded rows.
            "demographics": encrypt_patient_value(demographics_group),
            "contact": encrypt_patient_value(contact_group),
            "address_details": encrypt_patient_value(address),
            "emergency_contact_details": encrypt_patient_value(emergency),
            "admin": encrypt_patient_value(admin_group),
            "guarantor": encrypt_patient_value(guarantor_group),
            "insurance": (
                encrypt_patient_value(insurance_group)
                if insurance_group is not None else None
            ),
            "status": "active",
            "updated_at": now,
        }
        if existing is None:
            doc["created_at"] = now
            await db.patients.insert_one(doc)
        else:
            await db.patients.update_one({"id": patient_id}, {"$set": doc})
        id_by_email[spec["email"] + "|" + spec["first_name"]] = patient_id
    return id_by_email


async def _upsert_policies(
    tenant_id: str, payer_by_code: dict[str, dict],
) -> None:
    """Attach an insurance policy per persona, keyed on (patient_id,
    payer_id, rank=primary)."""
    db = get_db_write()
    now = _now()
    for spec in _PERSONAS:
        payer = payer_by_code.get(spec["payer_code"])
        if not payer:
            continue
        patient = await db.patients.find_one(
            {"tenant_id": tenant_id,
             "first_name": spec["first_name"],
             "last_name": spec["last_name"],
             "email": spec["email"]},
            {"_id": 0, "id": 1},
        )
        if not patient:
            continue
        policy_key = {
            "tenant_id": tenant_id,
            "patient_id": patient["id"],
            "payer_id": payer["id"],
            "rank": "primary",
        }
        existing = await db.patient_insurance_policies.find_one(
            policy_key, {"_id": 0, "id": 1},
        )
        p = spec["policy"]
        subscriber = (
            spec["guarantor_email"]
            if spec.get("persona") == "minor_dependent"
            else f"{spec['first_name']} {spec['last_name']}"
        )
        relationship = p.get("relationship_to_subscriber") or (
            "child" if spec.get("persona") == "minor_dependent" else "self"
        )
        doc = {
            **policy_key,
            "subscriber_name": subscriber if isinstance(subscriber, str)
                                else f"{spec['first_name']} {spec['last_name']}",
            "member_id": p["member_id"],
            "group_number": p.get("group"),
            "relationship_to_subscriber": relationship,
            "copay_cents": p.get("copay_cents"),
            "deductible_cents": p.get("deductible_cents"),
            "effective_date": "2026-01-01",
            "status": "active",
            "updated_at": now,
        }
        if existing is None:
            doc.update({"id": str(uuid.uuid4()), "created_at": now})
            await db.patient_insurance_policies.insert_one(doc)
        else:
            await db.patient_insurance_policies.update_one(
                {"id": existing["id"]}, {"$set": doc},
            )


async def _seed_clinical_notes(
    tenant_id: str, location_id: str | None,
    doctor_id: str | None,
) -> None:
    """One realistic chart note per persona — idempotent on
    (patient_id, title)."""
    if not doctor_id:
        return
    db = get_db_write()
    now = _now()
    for spec in _PERSONAS:
        patient = await db.patients.find_one(
            {"tenant_id": tenant_id,
             "first_name": spec["first_name"],
             "last_name": spec["last_name"],
             "email": spec["email"]},
            {"_id": 0, "id": 1},
        )
        if not patient:
            continue
        clin = spec["clinical"]
        title = f"{spec['persona'].replace('_', ' ').title()} — initial visit"
        existing = await db.medical_records.find_one(
            {"tenant_id": tenant_id,
             "patient_id": patient["id"],
             "title": title},
            {"_id": 0, "id": 1},
        )
        body = (
            f"CHIEF COMPLAINT\n{clin['chief_complaint']}\n\n"
            f"SUBJECTIVE\n{clin['subjective']}\n\n"
            f"OBJECTIVE\n{clin['objective']}\n\n"
            f"ASSESSMENT\n{clin['assessment']}\n\n"
            f"PLAN\n{clin['plan']}"
        )
        doc = {
            "tenant_id": tenant_id,
            "location_id": location_id,
            "patient_id": patient["id"],
            "record_type": "assessment",
            "title": title,
            "description": encrypt_text(body),
            "diagnosis": encrypt_text(clin["assessment"]),
            "treatment": encrypt_text(clin["plan"]),
            "recorded_by": doctor_id,
            "recorded_at": now,
            "updated_at": now,
        }
        if existing is None:
            doc.update({"id": str(uuid.uuid4()), "created_at": now})
            await db.medical_records.insert_one(doc)
        else:
            await db.medical_records.update_one(
                {"id": existing["id"]}, {"$set": doc},
            )


async def _seed_appointments(
    tenant_id: str, location_id: str | None,
    doctor_primary: str | None, doctor_associate: str | None,
) -> None:
    """Week-long schedule across personas:
        - past / today / future
        - a cancellation, a completed visit, scheduled adjustments,
          a re-exam, a therapy visit.
    Idempotent on (tenant_id, patient_id, start_time)."""
    if not (doctor_primary and doctor_associate):
        return
    db = get_db_write()
    now_dt = datetime.now(timezone.utc)

    def _at(days_offset: int, hour: int, minute: int = 0) -> datetime:
        d = (now_dt + timedelta(days=days_offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0,
        )
        return d

    # (first_name,last_name,dob, days_offset, hour, duration_min, reason,
    #  status, provider_choice: 'primary'|'associate')
    schedule = [
        ("Ethan", "Parker", "1991-08-17", -2, 17, 15,
         "Maintenance adjustment", "completed", "primary"),
        ("Marcus", "Reid", "1958-07-21", -1, 9, 30,
         "Medicare initial exam (active treatment)", "completed", "primary"),
        ("Hannah", "Whitaker", "1992-03-04", 0, 10, 45,
         "New patient — acute neck pain", "scheduled", "primary"),
        ("Isabella", "Cho", "1984-11-12", 0, 11, 30,
         "PIP follow-up adjustment", "scheduled", "associate"),
        ("Derrick", "Stone", "1972-05-30", 0, 14, 30,
         "Workers' comp visit 2", "scheduled", "primary"),
        ("Aria", "Johnson", "1997-02-18", 1, 8, 30,
         "IT band follow-up + IASTM", "scheduled", "associate"),
        ("Claire", "Morgan", "1986-09-09", 2, 17, 30,
         "Thoracic adjustment", "scheduled", "primary"),
        ("Jaxon", "Morgan", "2014-06-02", 2, 17, 30,
         "Pediatric thoracic check", "scheduled", "associate"),
        ("Marcus", "Reid", "1958-07-21", 2, 9, 30,
         "Active-treatment visit 3", "scheduled", "primary"),
        ("Hannah", "Whitaker", "1992-03-04", 3, 10, 30,
         "Neck pain follow-up", "scheduled", "primary"),
        ("Isabella", "Cho", "1984-11-12", 3, 11, 30,
         "PIP adjustment + manual therapy", "scheduled", "associate"),
        ("Marcus", "Reid", "1958-07-21", 4, 9, 45,
         "Re-exam at visit 6", "scheduled", "primary"),
        # A canceled and a rescheduled slot to make the calendar feel real.
        ("Aria", "Johnson", "1997-02-18", -3, 8, 30,
         "Canceled — schedule conflict", "cancelled", "associate"),
    ]

    for first, last, dob, d_offset, hour, dur, reason, sts, who in schedule:
        # Lookup by name only — DOB can be encrypted after a PATCH
        # round-trip which breaks exact-string matching.
        pt = await db.patients.find_one(
            {"tenant_id": tenant_id, "first_name": first, "last_name": last},
            {"_id": 0, "id": 1},
        )
        if not pt:
            continue
        provider_id = (doctor_associate if who == "associate"
                       else doctor_primary)
        start = _at(d_offset, hour)
        end = start + timedelta(minutes=dur)
        key = {
            "tenant_id": tenant_id,
            "patient_id": pt["id"],
            "start_time": start.isoformat(),
        }
        existing = await db.appointments.find_one(key, {"_id": 0, "id": 1})
        doc = {
            **key,
            "location_id": location_id,
            "provider_id": provider_id,
            "end_time": end.isoformat(),
            "reason": reason,
            "notes": encrypt_text("Seeded demo schedule — Riverbend Chiropractic."),
            "status": sts,
            "created_by": provider_id,
            "updated_at": _now(),
        }
        if existing is None:
            doc.update({"id": str(uuid.uuid4()), "created_at": _now()})
            await db.appointments.insert_one(doc)
        else:
            await db.appointments.update_one(
                {"id": existing["id"]}, {"$set": doc},
            )


async def _upsert_clinic_profile(
    tenant_id: str, location_id: str | None,
    created_by: str | None,
) -> None:
    """Seed/refresh the Riverbend ClinicProfile row. Keyed on
    (tenant_id, location_id). Idempotent: re-running refreshes the
    stored fields without bumping `id` or `created_at`.
    """
    if not location_id:
        logger.info("demo.seed: no default location — skipping clinic_profile")
        return
    db = get_db_write()
    now = _now()
    key = {"tenant_id": tenant_id, "location_id": location_id}
    existing = await db.clinic_profiles.find_one(key, {"_id": 0, "id": 1, "created_at": 1})
    doc = {
        **key,
        **_CLINIC_PROFILE,
        "updated_at": now,
        "updated_by": created_by,
    }
    if existing is None:
        doc.update({
            "id": str(uuid.uuid4()),
            "created_at": now,
            "created_by": created_by,
        })
        await db.clinic_profiles.insert_one(doc)
    else:
        await db.clinic_profiles.update_one({"id": existing["id"]}, {"$set": doc})


async def _upsert_appointment_types(tenant_id: str, created_by: str | None) -> None:
    """Seed realistic visit-type catalog. Keyed on (tenant_id, name)."""
    db = get_db_write()
    now = _now()
    for spec in _APPOINTMENT_TYPES:
        key = {"tenant_id": tenant_id, "name": spec["name"]}
        existing = await db.appointment_types.find_one(key, {"_id": 0, "id": 1})
        doc = {
            **key,
            "default_duration_minutes": spec["default_duration_minutes"],
            "description": spec.get("description"),
            "sort_order": spec["sort_order"],
            "is_active": True,
            "default_follow_up_days": spec.get("default_follow_up_days"),
            "updated_at": now,
            "updated_by": created_by,
        }
        if existing is None:
            doc.update({
                "id": str(uuid.uuid4()),
                "created_at": now,
                "created_by": created_by,
            })
            await db.appointment_types.insert_one(doc)
        else:
            await db.appointment_types.update_one(
                {"id": existing["id"]}, {"$set": doc},
            )


async def _upsert_rooms(tenant_id: str, location_id: str | None) -> None:
    """Seed clinic rooms. Keyed on (tenant_id, location_id, name)."""
    if not location_id:
        logger.info("demo.seed: no default location — skipping rooms")
        return
    db = get_db_write()
    now = _now()
    for spec in _ROOMS:
        key = {
            "tenant_id": tenant_id,
            "location_id": location_id,
            "name": spec["name"],
        }
        existing = await db.rooms.find_one(key, {"_id": 0, "id": 1})
        doc = {
            **key,
            "type": spec["type"],
            "is_active": True,
            "sort_order": spec["sort_order"],
            "notes": spec.get("notes"),
            "updated_at": now,
        }
        if existing is None:
            doc.update({"id": str(uuid.uuid4()), "created_at": now})
            await db.rooms.insert_one(doc)
        else:
            await db.rooms.update_one({"id": existing["id"]}, {"$set": doc})


# ---------------------------------------------------------------------------
# Notification + follow-up seed — populates the Notification Log,
# Checkout panel's Follow-up Suggestions queue, and the patient-chart
# communication history so the demo feels like a live clinic. All rows
# are keyed to existing Riverbend appointments so the reseed can wipe +
# re-insert deterministically.
#
# Event types covered (must stay in sync with frontend `FILTERS` in
# `pages/Notifications.jsx`):
#   - appointment.booked           (every scheduled appt)
#   - appointment.reminder         (24h before scheduled)
#   - appointment.same_day_reminder (morning of the appt)
#   - appointment.cancelled        (the Aria cancellation)
#   - appointment.follow_up        ("haven't seen you in a while")
#   - review.request               (after completed visit)
# ---------------------------------------------------------------------------
def _render_body(template: str, patient_name: str, provider_name: str,
                 when: str, reason: str) -> str:
    return template.format(
        patient_name=patient_name or "Patient",
        provider_name=provider_name,
        when=when, reason=reason or "Chiropractic consultation",
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _fmt_when(dt: datetime) -> str:
    return dt.strftime("%a %b %d, %Y at %I:%M %p")


async def _seed_notifications(
    tenant_id: str, location_id: str | None,
) -> None:
    """Wipe + re-seed realistic notification/communication rows for the
    Riverbend tenant, keyed on the seeded appointments."""
    db = get_db_write()
    now_dt = datetime.now(timezone.utc)

    # 1. Wipe any prior demo-seeded notifications on this tenant so the
    # shape stays curated (the communication subscriber's live rows
    # also live here — but those fire from real user actions, not the
    # seed; tests that book appointments emit their own rows that get
    # swept by reseed_demo_clinic.py separately).
    await db.notifications.delete_many({
        "tenant_id": tenant_id,
        "source": "demo_seed",
    })

    # 2. Load every seeded appointment with patient + provider names.
    appts = [a async for a in db.appointments.find(
        {"tenant_id": tenant_id}, {"_id": 0},
    )]
    patients = {
        p["id"]: p
        async for p in db.patients.find(
            {"tenant_id": tenant_id},
            {"_id": 0, "id": 1, "first_name": 1, "last_name": 1,
             "email": 1, "phone": 1},
        )
    }
    providers = {
        u["id"]: u.get("display_name") or u.get("name") or "your provider"
        async for u in db.users.find(
            {}, {"_id": 0, "id": 1, "name": 1, "display_name": 1},
        )
    }

    docs: list[dict] = []

    def _add(appt: dict, *, channel: str, to_address: str | None,
             event_type: str, subject: str, body: str, status: str,
             created_dt: datetime, extra: dict | None = None) -> None:
        if not to_address:
            return
        docs.append({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "location_id": location_id,
            "appointment_id": appt["id"] if appt else None,
            "patient_id": appt["patient_id"] if appt else None,
            "channel": channel,
            "to_address": to_address,
            "event_type": event_type,
            "subject": subject,
            "body": body,
            "status": status,
            "created_at": _iso(created_dt),
            "source": "demo_seed",
            **(extra or {}),
        })

    for appt in appts:
        pid = appt.get("patient_id")
        patient = patients.get(pid) or {}
        patient_name = (
            f"{patient.get('first_name','')} {patient.get('last_name','')}"
            .strip() or "Patient"
        )
        email = patient.get("email") or ""
        phone = patient.get("phone") or ""
        provider_name = providers.get(appt.get("provider_id")) or "your provider"
        start = datetime.fromisoformat(appt["start_time"])
        when_pretty = _fmt_when(start)
        reason = appt.get("reason") or "Chiropractic consultation"

        # --- Booked (sent at booking) -----------------------------------
        booked_at = start - timedelta(days=7)
        _add(appt, channel="email", to_address=email,
             event_type="appointment.booked",
             subject="Your appointment is confirmed",
             body=_render_body(
                 "Hi {patient_name}, your appointment with {provider_name} "
                 "is confirmed for {when}. Reason: {reason}. "
                 "Reply to this email if you need to reschedule.",
                 patient_name, provider_name, when_pretty, reason),
             status="delivered", created_dt=booked_at)
        _add(appt, channel="sms", to_address=phone,
             event_type="appointment.booked",
             subject=None,
             body=_render_body(
                 "Riverbend Chiropractic: {patient_name}, your visit with "
                 "{provider_name} is booked for {when}. Reply STOP to "
                 "opt out.",
                 patient_name, provider_name, when_pretty, reason),
             status="delivered", created_dt=booked_at)

        status = appt.get("status")

        # --- Cancellation notice ----------------------------------------
        if status == "cancelled":
            cancel_at = start - timedelta(days=1)
            _add(appt, channel="email", to_address=email,
                 event_type="appointment.cancelled",
                 subject="Your appointment was cancelled",
                 body=_render_body(
                     "Hi {patient_name}, your appointment with "
                     "{provider_name} scheduled for {when} has been "
                     "cancelled. We will reach out to reschedule.",
                     patient_name, provider_name, when_pretty, reason),
                 status="delivered", created_dt=cancel_at)
            _add(appt, channel="sms", to_address=phone,
                 event_type="appointment.cancelled",
                 subject=None,
                 body=_render_body(
                     "Riverbend: {patient_name}, your {when} visit was "
                     "cancelled. We'll call you to rebook.",
                     patient_name, provider_name, when_pretty, reason),
                 status="delivered", created_dt=cancel_at)
            continue  # don't send reminders for cancelled appts

        # --- 24h reminder -----------------------------------------------
        reminder_at = start - timedelta(hours=24)
        # Mix in one `failed` SMS (carrier rejection) for Marcus Reid's
        # future active-treatment visit, and one `suppressed` email for
        # Claire Morgan (opted out of email marketing) — so Ops can
        # show "delivered | failed | suppressed" side-by-side.
        first_name = patient.get("first_name")
        sms_status = "failed" if (first_name == "Marcus" and start > now_dt) else "delivered"
        email_status = "suppressed" if first_name == "Claire" else "delivered"

        if reminder_at < now_dt + timedelta(days=5):
            _add(appt, channel="email", to_address=email,
                 event_type="appointment.reminder",
                 subject=f"Reminder: appointment tomorrow with {provider_name}",
                 body=_render_body(
                     "Hi {patient_name}, this is a reminder that your "
                     "appointment with {provider_name} is tomorrow at "
                     "{when}. Please arrive 10 minutes early to update "
                     "any paperwork.",
                     patient_name, provider_name, when_pretty, reason),
                 status=email_status, created_dt=reminder_at,
                 extra={"failure_reason": (
                     "patient opted out of email marketing"
                     if email_status == "suppressed" else None)})
            _add(appt, channel="sms", to_address=phone,
                 event_type="appointment.reminder",
                 subject=None,
                 body=_render_body(
                     "Riverbend: reminder — {patient_name} has a visit "
                     "with {provider_name} tomorrow at {when}.",
                     patient_name, provider_name, when_pretty, reason),
                 status=sms_status, created_dt=reminder_at,
                 extra={"failure_reason": (
                     "carrier rejected (handset unreachable)"
                     if sms_status == "failed" else None)})

        # --- Same-day reminder (SMS only) -------------------------------
        same_day_at = start.replace(hour=8, minute=0, second=0, microsecond=0)
        # Only send the same-day ping if the appointment is today or has
        # already happened (completed visits also had a same-day ping).
        if same_day_at.date() <= now_dt.date() and status != "cancelled":
            _add(appt, channel="sms", to_address=phone,
                 event_type="appointment.same_day_reminder",
                 subject=None,
                 body=_render_body(
                     "Riverbend today: hi {patient_name}, see you at "
                     "{when} with {provider_name}. Our address: "
                     "1840 NW Riverside Dr, Suite 210.",
                     patient_name, provider_name, when_pretty, reason),
                 status="delivered", created_dt=same_day_at)

        # --- Review request (after completed visits) --------------------
        if status == "completed":
            review_at = start + timedelta(days=1)
            _add(appt, channel="email", to_address=email,
                 event_type="review.request",
                 subject="How was your visit with Riverbend Chiropractic?",
                 body=_render_body(
                     "Hi {patient_name}, thanks for visiting "
                     "{provider_name} on {when}. If you have a moment, "
                     "we'd love a quick review — it helps others find "
                     "us. https://riverbend-chiro.app/review",
                     patient_name, provider_name, when_pretty, reason),
                 status="delivered", created_dt=review_at)
            _add(appt, channel="sms", to_address=phone,
                 event_type="review.request",
                 subject=None,
                 body=_render_body(
                     "Thanks for visiting Riverbend! Leave a quick "
                     "review: https://riverbend-chiro.app/review "
                     "Reply STOP to opt out.",
                     patient_name, provider_name, when_pretty, reason),
                 status="sent", created_dt=review_at + timedelta(hours=2))

    # --- Standalone "haven't seen you" follow-up for Ethan Parker -----
    ethan = next(
        (p for p in patients.values() if p.get("first_name") == "Ethan"
         and p.get("last_name") == "Parker"), None,
    )
    if ethan:
        follow_dt = now_dt - timedelta(days=1, hours=4)
        docs.append({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "location_id": location_id,
            "appointment_id": None,
            "patient_id": ethan["id"],
            "channel": "email",
            "to_address": ethan.get("email") or "",
            "event_type": "appointment.follow_up",
            "subject": "We haven't seen you in a while — time for a tune-up?",
            "body": (
                f"Hi {ethan.get('first_name') or 'there'}, it's been a "
                "few weeks since your last maintenance adjustment. "
                "Chiropractic care works best when it's consistent — "
                "book your next visit at "
                "https://riverbend-chiro.app/book."
            ),
            "status": "delivered",
            "created_at": _iso(follow_dt),
            "source": "demo_seed",
        })

    if docs:
        # Drop any row whose recipient address didn't resolve (the
        # communication subscriber also does this; be defensive).
        docs = [d for d in docs if d.get("to_address")]
        await db.notifications.insert_many(docs)

    logger.info(
        "demo.seed: notifications seeded — %d rows across %d appointments",
        len(docs), len(appts),
    )


async def _seed_follow_up_suggestions(
    tenant_id: str, location_id: str | None,
) -> None:
    """Seed a curated queue of rebooking suggestions (what the Checkout
    page's FollowUpSuggestions card surfaces to front desk)."""
    db = get_db_write()
    now_dt = datetime.now(timezone.utc)

    # Wipe prior demo rows on this tenant.
    await db.follow_up_suggestions.delete_many({
        "tenant_id": tenant_id,
        "source": "demo_seed",
    })

    # Map appointment-type name → id so we can link the suggestion to
    # the visit type Olivia would have picked at checkout.
    type_by_name = {
        t["name"]: t["id"]
        async for t in db.appointment_types.find(
            {"tenant_id": tenant_id}, {"_id": 0, "id": 1, "name": 1},
        )
    }

    # Pull the two completed appts seeded by `_seed_appointments` —
    # Ethan (maintenance, 2 days ago) and Marcus (new patient exam,
    # 1 day ago) — plus the `today` Hannah appt as an advance
    # rebook-attempt that front desk hasn't acted on yet.
    def _find_appt(first: str, last: str, status: str | None = None) -> dict | None:
        return None  # placeholder; async lookup below

    async def _load(first: str, last: str, statuses: tuple[str, ...]) -> dict | None:
        pt = await db.patients.find_one(
            {"tenant_id": tenant_id, "first_name": first, "last_name": last},
            {"_id": 0, "id": 1},
        )
        if not pt:
            return None
        appt = await db.appointments.find_one(
            {"tenant_id": tenant_id, "patient_id": pt["id"],
             "status": {"$in": list(statuses)}},
            sort=[("start_time", -1)],
        )
        if not appt:
            return None
        return appt

    candidates: list[tuple[str, str, str, int]] = [
        # (first, last, visit_type, days_until_next)
        ("Ethan",  "Parker", "Maintenance / Wellness Visit", 30),
        ("Marcus", "Reid",   "New Patient Exam",              3),
        ("Hannah", "Whitaker", "Follow-up Visit",             7),
    ]

    docs: list[dict] = []
    for first, last, type_name, days in candidates:
        appt = await _load(first, last, ("completed", "scheduled"))
        if not appt:
            continue
        type_id = type_by_name.get(type_name)
        suggested_at = (now_dt + timedelta(days=days)).date().isoformat()
        docs.append({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "location_id": location_id,
            "appointment_id": appt["id"],
            "patient_id": appt["patient_id"],
            "provider_id": appt["provider_id"],
            "appointment_type_id": type_id,
            "suggested_at": suggested_at,
            "source": "demo_seed",
            "status": "pending",
            "note": f"Rebook {type_name} ({days}d after last visit)",
            "created_at": _iso(now_dt),
            "created_by": appt.get("provider_id"),
        })

    if docs:
        await db.follow_up_suggestions.insert_many(docs)

    logger.info(
        "demo.seed: follow_up_suggestions seeded — %d rows", len(docs),
    )


# ---------------------------------------------------------------------------
async def seed_demo_clinic() -> None:
    """Idempotent realistic seed for the Riverbend demo tenant. Safe
    to call on every boot. Returns silently when the default tenant
    hasn't been created yet (earlier seed steps will have created it
    by the time `server.py` calls us)."""
    tenant_id, location_id = await _get_default_tenant()
    if not tenant_id:
        logger.info("demo.seed: default tenant missing — skipping")
        return

    staff_by_email = await _upsert_staff(tenant_id)
    payers_by_code = await _upsert_payers(tenant_id)

    # Primary provider: the demo login helper's Dr. Noah Carter.
    db = get_db_write()
    lead_doc = await db.users.find_one(
        {"email": "doctor@ccms.app"}, {"_id": 0, "id": 1},
    )
    associate_doc_id = staff_by_email.get(
        "dr.samuel.ito@riverbend-chiro.app",
    )
    lead_doc_id = lead_doc["id"] if lead_doc else None

    await _upsert_personas(tenant_id, location_id, lead_doc_id)
    await _upsert_policies(tenant_id, payers_by_code)
    await _seed_clinical_notes(tenant_id, location_id, lead_doc_id)
    await _seed_appointments(
        tenant_id, location_id, lead_doc_id, associate_doc_id,
    )
    # Clinic configuration — profile / hours, appointment types, rooms.
    # Clinic Owner (Olivia Hart) is the `created_by` attributor so the
    # audit/provenance fields look real.
    owner_id = staff_by_email.get("olivia.hart@riverbend-chiro.app")
    await _upsert_clinic_profile(tenant_id, location_id, owner_id)
    await _upsert_appointment_types(tenant_id, owner_id)
    await _upsert_rooms(tenant_id, location_id)
    # Notification log + follow-up rebooking queue — populates the
    # Communication panel and Checkout page's suggestions card.
    await _seed_notifications(tenant_id, location_id)
    await _seed_follow_up_suggestions(tenant_id, location_id)
    logger.info(
        "demo.seed complete: staff=%d personas=%d payers=%d "
        "appt_types=%d rooms=%d clinic_profile=1",
        len(_STAFF), len(_PERSONAS), len(_PAYERS),
        len(_APPOINTMENT_TYPES), len(_ROOMS),
    )
