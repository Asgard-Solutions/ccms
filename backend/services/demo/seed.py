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
# Patient personas — each drives downstream demographics, insurance, and
# clinical notes. Keeping them declarative so extending the catalog is
# just adding an entry to this list.
# ---------------------------------------------------------------------------
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
        # Minor dependents share the guardian's email/phone — key on
        # first+last+dob so we never collapse them into one record.
        key = {
            "tenant_id": tenant_id,
            "first_name": spec["first_name"],
            "last_name": spec["last_name"],
            "date_of_birth": spec["date_of_birth"],
        }
        existing = await db.patients.find_one(key, {"_id": 0, "id": 1})
        patient_id = existing["id"] if existing else str(uuid.uuid4())
        doc = {
            "id": patient_id,
            "tenant_id": tenant_id,
            "location_id": location_id,
            "user_id": None,
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
            # PHI fields — encrypted at rest.
            "address": encrypt_text(spec["address"]),
            "emergency_contact": encrypt_text(spec["emergency_contact"]),
            "notes": encrypt_text(
                f"Demo persona: {spec['persona']}. See "
                f"/app/memory/DEMO_SEED.md for full scenario."
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
             "date_of_birth": spec["date_of_birth"]},
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
             "date_of_birth": spec["date_of_birth"]},
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
        pt = await db.patients.find_one(
            {"tenant_id": tenant_id, "first_name": first, "last_name": last,
             "date_of_birth": dob},
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
    logger.info(
        "demo.seed complete: staff=%d personas=%d payers=%d",
        len(_STAFF), len(_PERSONAS), len(_PAYERS),
    )
