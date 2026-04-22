"""
Billing demo data for Riverbend Chiropractic & Wellness.

Extends `services/demo/seed.py` with a curated set of claims,
submissions, remittances, invoices, statements, payments, and
adjustments so the billing dashboards, claims queue, A/R aging,
denials views, and patient statements are immediately populated on
first login.

Everything is idempotent — upserts on stable business keys so boot
restarts don't duplicate. Fully scoped to the Riverbend tenant;
never touches Sunrise or the platform admin.

See `/app/memory/DEMO_SEED.md` §6 for the full persona → billing
scenario map.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from core.crypto import encrypt_text
from core.db import get_db_write

logger = logging.getLogger("ccms.demo.billing_seed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_date_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def _iso_ts_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Scenario catalog. Keyed so re-running upserts in place.
# Each claim carries:
#   key              — stable id derived from persona + scenario slug.
#   persona          — (first_name, last_name, dob) tuple matching the
#                      persona seeded in services/demo/seed.py.
#   payer_code       — payer_code from services/demo/seed.py payer catalog.
#   days_ago         — service date offset (positive = past, 0 = today).
#   status           — ClaimStatus literal.
#   billed_cents     — total billed (sum of lines).
#   paid_cents       — total paid (0 for non-paid statuses).
#   lines            — [(seq, service_date, cpt, units, billed_cents,
#                         modifiers, dx_pointers)]
#   diagnoses        — [(seq, icd10_code)]
#   modifier_flags   — {"at": bool, "gp": bool, ...} purely cosmetic for docs.
#   followup_flag    — Phase 10 manual follow-up marker.
#   followup_reason  — short operator hint.
#   denial_code      — X12 CARC / adjustment reason when denied/rejected.
#   last_event       — human-readable "last activity" shown in queue.
# ---------------------------------------------------------------------------
CPT_CMT_1_2 = "98940"     # Chiropractic manipulative treatment, 1-2 regions
CPT_CMT_3_4 = "98941"     # CMT, 3-4 regions
CPT_NEW_PT = "99203"      # New patient E/M level 3
CPT_IASTM = "97140"       # Manual therapy techniques (IASTM)
CPT_REEXAM = "99213"      # Est. patient E/M level 3


_CLAIM_SCENARIOS = [
    # --- Marcus Reid — Medicare chronic LBP ---------------------------------
    {
        "key": "reid_medicare_paid_old",
        "persona": ("Marcus", "Reid", "1958-07-21"),
        "payer_code": "MCR-OR",
        "days_ago": 72,
        "status": "paid",
        "billed_cents": 6500,
        "paid_cents": 2913,   # Medicare allowed ~45% of billed
        "adjustment_cents": 3587,
        "patient_resp_cents": 0,
        "diagnoses": [(1, "M99.03"), (2, "M54.50")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 6500, ["AT"], [1, 2]),
        ],
        "initial_treatment_date_days_ago": 72,
        "requires_at": True,
        "last_event": "paid — Medicare ERA posted",
    },
    {
        "key": "reid_medicare_paid_recent",
        "persona": ("Marcus", "Reid", "1958-07-21"),
        "payer_code": "MCR-OR",
        "days_ago": 30,
        "status": "paid",
        "billed_cents": 6500,
        "paid_cents": 2925,
        "adjustment_cents": 3575,
        "patient_resp_cents": 0,
        "diagnoses": [(1, "M99.03"), (2, "M54.50")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 6500, ["AT"], [1, 2]),
        ],
        "initial_treatment_date_days_ago": 72,
        "requires_at": True,
        "last_event": "paid — Medicare ERA posted",
    },
    {
        "key": "reid_medicare_accepted",
        "persona": ("Marcus", "Reid", "1958-07-21"),
        "payer_code": "MCR-OR",
        "days_ago": 1,
        "status": "accepted",
        "billed_cents": 6500,
        "paid_cents": 0,
        "diagnoses": [(1, "M99.03"), (2, "M54.50")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 6500, ["AT"], [1, 2]),
        ],
        "initial_treatment_date_days_ago": 72,
        "requires_at": True,
        "last_event": "accepted — awaiting 835 ERA",
    },

    # --- Hannah Whitaker — Cascade Blue Shield acute neck ------------------
    {
        "key": "whitaker_cbs_draft_newpt",
        "persona": ("Hannah", "Whitaker", "1992-03-04"),
        "payer_code": "CBS-COMM",
        "days_ago": 0,
        "status": "draft",
        "billed_cents": 22500,   # 99203 $150 + 98940 $75
        "paid_cents": 0,
        "diagnoses": [(1, "M54.2")],
        "lines": [
            (1, CPT_NEW_PT, 1, 15000, [], [1]),
            (2, CPT_CMT_1_2, 1, 7500, [], [1]),
        ],
        "last_event": "draft — ready for scrubber",
    },
    {
        "key": "whitaker_cbs_ready",
        "persona": ("Hannah", "Whitaker", "1992-03-04"),
        "payer_code": "CBS-COMM",
        "days_ago": 2,
        "status": "ready",
        "billed_cents": 7500,
        "paid_cents": 0,
        "diagnoses": [(1, "M54.2")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 7500, [], [1]),
        ],
        "last_event": "scrubbed clean — queued for the next 837P batch",
    },
    {
        "key": "whitaker_cbs_validation_failed",
        "persona": ("Hannah", "Whitaker", "1992-03-04"),
        "payer_code": "CBS-COMM",
        "days_ago": 1,
        "status": "validation_failed",
        "billed_cents": 7500,
        "paid_cents": 0,
        "diagnoses": [(1, "M54.2")],
        "lines": [
            # Intentionally missing a modifier the payer rule demands
            # so the scrubber blocks submission until the biller fixes it.
            (1, CPT_CMT_1_2, 1, 7500, [], [1]),
        ],
        "validation_error_count": 1,
        "validation_warning_count": 0,
        "denial_reason": (
            "Scrubber error: line 1 missing required modifier for "
            "Cascade Blue Shield bundling policy. Edit the claim "
            "and re-validate."
        ),
        "last_event": "validation_failed — needs modifier fix",
    },

    # --- Isabella Cho — PIP auto -------------------------------------------
    {
        "key": "cho_pip_submitted_portal",
        "persona": ("Isabella", "Cho", "1984-11-12"),
        "payer_code": "NWA-PIP",
        "days_ago": 2,
        "status": "submitted",
        "billed_cents": 14500,   # 98940 $75 + 97140 $70
        "paid_cents": 0,
        "diagnoses": [(1, "S13.4XXA"), (2, "S23.3XXA")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 7500, [], [1, 2]),
            (2, CPT_IASTM, 1, 7000, ["59"], [1, 2]),
        ],
        "accident_date_days_ago": 6,
        "submission_method": "portal",
        "last_event": "submitted via PIP portal — paper EOB pending",
    },
    {
        "key": "cho_pip_partially_paid",
        "persona": ("Isabella", "Cho", "1984-11-12"),
        "payer_code": "NWA-PIP",
        "days_ago": 18,
        "status": "partially_paid",
        "billed_cents": 14500,
        "paid_cents": 8000,     # PIP paid $80 of $145, $65 still outstanding
        "adjustment_cents": 0,
        "patient_resp_cents": 0,   # PIP — patient never responsible
        "diagnoses": [(1, "S13.4XXA"), (2, "S23.3XXA")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 7500, [], [1, 2]),
            (2, CPT_IASTM, 1, 7000, ["59"], [1, 2]),
        ],
        "accident_date_days_ago": 21,
        "submission_method": "portal",
        "followup_flag": True,
        "followup_reason": "PIP partial payment — follow up on remaining $65",
        "last_event": "partially paid by PIP — $65 balance on wire",
    },

    # --- Derrick Stone — Workers' Comp -------------------------------------
    {
        "key": "stone_wc_submitted_portal",
        "persona": ("Derrick", "Stone", "1972-05-30"),
        "payer_code": "SAIF-WC",
        "days_ago": 0,
        "status": "submitted",
        "billed_cents": 9000,    # 98941 3-region
        "paid_cents": 0,
        "diagnoses": [(1, "S33.5XXA")],
        "lines": [
            (1, CPT_CMT_3_4, 1, 9000, [], [1]),
        ],
        "accident_date_days_ago": 3,
        "submission_method": "portal",
        "authorization_number": "SAIF-WC-AUTH-88410",
        "last_event": "submitted via SAIF portal — WC adjuster notified",
    },
    {
        "key": "stone_wc_denied_missing_case",
        "persona": ("Derrick", "Stone", "1972-05-30"),
        "payer_code": "SAIF-WC",
        "days_ago": 21,
        "status": "denied",
        "billed_cents": 9000,
        "paid_cents": 0,
        "diagnoses": [(1, "S33.5XXA")],
        "lines": [
            (1, CPT_CMT_3_4, 1, 9000, [], [1]),
        ],
        "accident_date_days_ago": 24,
        "submission_method": "portal",
        "denial_code": "CO-16",
        "denial_reason": (
            "Claim lacks information or has submission/billing error — "
            "WC case / claim number missing from CMS-1500."
        ),
        "followup_flag": True,
        "followup_reason": "WC: add claim number, rebill",
        "last_event": "denied — needs WC case number; flagged for follow-up",
    },

    # --- Aria Johnson — PacificCare commercial -----------------------------
    {
        "key": "johnson_pac_paid",
        "persona": ("Aria", "Johnson", "1997-02-18"),
        "payer_code": "PAC-COMM",
        "days_ago": 14,
        "status": "paid",
        "billed_cents": 14500,
        "paid_cents": 9500,
        "adjustment_cents": 2500,  # contractual write-off
        "patient_resp_cents": 2500,
        "copay_cents": 2500,
        "diagnoses": [(1, "M76.30"), (2, "M54.5")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 7500, [], [1, 2]),
            (2, CPT_IASTM, 1, 7000, ["59"], [1, 2]),
        ],
        "last_event": "paid — ERA posted, $25 copay collected at visit",
    },
    {
        "key": "johnson_pac_denied_coding",
        "persona": ("Aria", "Johnson", "1997-02-18"),
        "payer_code": "PAC-COMM",
        "days_ago": 7,
        "status": "denied",
        "billed_cents": 7500,
        "paid_cents": 0,
        "patient_resp_cents": 0,
        "diagnoses": [(1, "M76.30")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 7500, [], [1]),
        ],
        "denial_code": "CO-11",
        "denial_reason": (
            "Diagnosis inconsistent with procedure — IT band syndrome "
            "(M76.30) is not a supported primary Dx for CMT without a "
            "secondary musculoskeletal Dx."
        ),
        "followup_flag": True,
        "followup_reason": "Rebill with secondary M99.03 as primary",
        "last_event": "denied — coding/documentation mismatch",
    },

    # --- Claire Morgan — PacificCare (older A/R aging 90+) ----------------
    {
        "key": "morgan_pac_paid_old",
        "persona": ("Claire", "Morgan", "1986-09-09"),
        "payer_code": "PAC-COMM",
        "days_ago": 95,
        "status": "paid",
        "billed_cents": 15000,   # 98940 + 99213
        "paid_cents": 10000,
        "adjustment_cents": 2500,
        "patient_resp_cents": 2500,
        "copay_cents": 2500,
        "diagnoses": [(1, "M54.6")],
        "lines": [
            (1, CPT_REEXAM, 1, 7500, [], [1]),
            (2, CPT_CMT_1_2, 1, 7500, [], [1]),
        ],
        "last_event": "paid 95d ago — copay collected, claim closed",
    },

    # --- Jaxon Morgan — pediatric, REJECTED subscriber mismatch -----------
    {
        "key": "morgan_jax_rejected_subscriber",
        "persona": ("Jaxon", "Morgan", "2014-06-02"),
        "payer_code": "PAC-COMM",
        "days_ago": 45,
        "status": "rejected",
        "billed_cents": 6000,
        "paid_cents": 0,
        "patient_resp_cents": 6000,   # pushed to guarantor
        "diagnoses": [(1, "M54.6")],
        "lines": [
            (1, CPT_CMT_1_2, 1, 6000, [], [1]),
        ],
        "denial_code": "CO-31",
        "denial_reason": (
            "Patient cannot be identified as our insured — dependent "
            "DOB on file does not match subscriber's policy roster."
        ),
        "followup_flag": True,
        "followup_reason": "Verify dependent DOB with PacificCare",
        "last_event": "rejected at intake — subscriber mismatch",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _lookup_refs(tenant_id: str) -> dict:
    """Resolve the persona patient rows + payer rows + demo provider
    ids we need before writing any billing rows. Returns a dict with
    lookup tables so the downstream seed loops don't re-query Mongo."""
    db = get_db_write()
    refs: dict = {"patient_by_persona": {}, "policy_by_persona": {},
                  "payer_by_code": {}, "doctor_id": None,
                  "billing_user_id": None, "location_id": None}

    loc = await db.locations.find_one(
        {"tenant_id": tenant_id}, {"_id": 0, "id": 1},
    )
    refs["location_id"] = loc["id"] if loc else None

    doc = await db.users.find_one(
        {"email": "doctor@ccms.app"}, {"_id": 0, "id": 1},
    )
    refs["doctor_id"] = doc["id"] if doc else None
    billing = await db.users.find_one(
        {"email": "tomas.rivera@riverbend-chiro.app"}, {"_id": 0, "id": 1},
    )
    refs["billing_user_id"] = (
        billing["id"] if billing else refs["doctor_id"]
    )

    # ---- Provider directory — canonical IDs used on claims ------------
    # Without these, the wire builder falls back to using the claim's
    # `billing_provider_id` directly as the NPI — which would leak the
    # raw user-UUID into outbound 837 payloads. Pre-seed by
    # `services/demo/seed.py::_upsert_providers`; look them up here.
    billing_provider = await db.providers.find_one(
        {"tenant_id": tenant_id, "kind": "billing"},
        {"_id": 0, "id": 1, "npi": 1, "name": 1},
    )
    rendering_lead = await db.providers.find_one(
        {"tenant_id": tenant_id, "kind": "rendering",
         "name": "Dr. Noah Carter, DC"},
        {"_id": 0, "id": 1, "npi": 1},
    )
    rendering_associate = await db.providers.find_one(
        {"tenant_id": tenant_id, "kind": "rendering",
         "name": "Dr. Samuel Ito, DC"},
        {"_id": 0, "id": 1, "npi": 1},
    )
    facility = await db.service_facilities.find_one(
        {"tenant_id": tenant_id},
        {"_id": 0, "id": 1},
    )
    refs["billing_provider_id"] = (
        billing_provider["id"] if billing_provider else None
    )
    refs["rendering_provider_lead_id"] = (
        rendering_lead["id"] if rendering_lead else None
    )
    refs["rendering_provider_associate_id"] = (
        rendering_associate["id"] if rendering_associate else
        refs["rendering_provider_lead_id"]
    )
    refs["facility_id"] = facility["id"] if facility else None

    async for p in db.patients.find({"tenant_id": tenant_id}, {"_id": 0}):
        refs["patient_by_persona"][
            (p["first_name"], p["last_name"], p["date_of_birth"])
        ] = p
    async for payer in db.billing_payers.find({"tenant_id": tenant_id}, {"_id": 0}):
        refs["payer_by_code"][payer.get("payer_code")] = payer
    async for pol in db.patient_insurance_policies.find(
        {"tenant_id": tenant_id, "rank": "primary"}, {"_id": 0},
    ):
        refs["policy_by_persona"][pol["patient_id"]] = pol

    return refs


async def _wipe_prior_demo(tenant_id: str) -> None:
    """Clear prior demo billing rows (keyed on our seed marker) so a
    scenario edit takes effect immediately on the next boot. Only
    rows carrying `demo_seed_key` are removed — anything created
    through the live UI is preserved."""
    db = get_db_write()
    for coll in (
        "claims", "claim_lines", "claim_diagnoses", "claim_line_modifiers",
        "claim_submissions", "claim_events", "remittances",
        "remittance_claims", "remittance_lines",
        "invoices", "invoice_lines", "payments", "payment_allocations",
        "adjustments", "statements",
    ):
        await db[coll].delete_many({
            "tenant_id": tenant_id,
            "demo_seed_key": {"$exists": True},
        })


# ---------------------------------------------------------------------------
async def _seed_one_claim(
    tenant_id: str, scenario: dict, refs: dict,
) -> tuple[str, dict] | None:
    """Write one claim + its lines + diagnoses + modifiers + events +
    (when applicable) submission + remittance rows. Returns
    (claim_id, scenario) on success; None if persona is missing."""
    db = get_db_write()
    patient = refs["patient_by_persona"].get(scenario["persona"])
    if not patient:
        logger.warning("demo.billing: persona missing %s", scenario["persona"])
        return None
    payer = refs["payer_by_code"].get(scenario["payer_code"])
    if not payer:
        logger.warning("demo.billing: payer %s missing", scenario["payer_code"])
        return None
    policy = refs["policy_by_persona"].get(patient["id"])

    claim_id = str(uuid.uuid4())
    service_date = _iso_date_ago(scenario["days_ago"])
    billed = scenario["billed_cents"]
    paid = scenario.get("paid_cents", 0)
    now = _now()

    claim_doc = {
        "id": claim_id,
        "demo_seed_key": scenario["key"],
        "tenant_id": tenant_id,
        "location_id": refs["location_id"],
        "patient_id": patient["id"],
        "payer_id": payer["id"],
        "policy_id": policy["id"] if policy else None,
        "claim_type": "professional",
        "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": refs.get("billing_provider_id")
                               or refs["doctor_id"],
        "rendering_provider_id": refs.get("rendering_provider_lead_id")
                                 or refs["doctor_id"],
        "facility_id": refs.get("facility_id"),
        "patient_control_number": f"RB-{scenario['key'][-8:].upper()}",
        "payer_claim_control_number": None,
        "accident_date": (
            _iso_date_ago(scenario["accident_date_days_ago"])
            if scenario.get("accident_date_days_ago") is not None else None
        ),
        "onset_date": None,
        "initial_treatment_date": (
            _iso_date_ago(scenario["initial_treatment_date_days_ago"])
            if scenario.get("initial_treatment_date_days_ago") is not None
            else None
        ),
        "authorization_number": scenario.get("authorization_number"),
        "status": scenario["status"],
        "service_date_from": service_date,
        "service_date_to": service_date,
        "billed_cents": billed,
        "paid_cents": paid,
        "submitted_at": (
            _iso_ts_ago(scenario["days_ago"])
            if scenario["status"] not in ("draft", "validation_failed")
            else None
        ),
        "accepted_at": (
            _iso_ts_ago(max(0, scenario["days_ago"] - 1))
            if scenario["status"] in ("accepted", "paid",
                                       "partially_paid", "denied")
            else None
        ),
        "last_denial_code": scenario.get("denial_code"),
        "notes": scenario.get("denial_reason") or None,
        "validation_error_count": scenario.get("validation_error_count", 0),
        "validation_warning_count": scenario.get("validation_warning_count", 0),
        "validation_last_run_at": (
            _iso_ts_ago(scenario["days_ago"])
            if scenario["status"] == "validation_failed" else None
        ),
        "followup_flag": scenario.get("followup_flag", False),
        "followup_reason": scenario.get("followup_reason"),
        "followup_flagged_at": (
            _iso_ts_ago(max(0, scenario["days_ago"] - 1))
            if scenario.get("followup_flag") else None
        ),
        "followup_flagged_by": (
            refs["billing_user_id"] if scenario.get("followup_flag") else None
        ),
        "next_action_at": (
            _iso_ts_ago(-3) if scenario.get("followup_flag") else None
        ),
        "assigned_to": None,
        "assignee_name": None,
        "created_at": _iso_ts_ago(scenario["days_ago"]),
        "updated_at": now,
        "created_by": refs["doctor_id"],
        "updated_by": refs["doctor_id"],
        "history": [{
            "at": _iso_ts_ago(scenario["days_ago"]),
            "by": refs["doctor_id"],
            "action": "demo_seeded",
            "status": scenario["status"],
            "billed_cents": billed,
        }],
    }
    await db.claims.insert_one(claim_doc)

    # Lines + modifiers + diagnoses
    for seq, cpt, units, line_billed, mods, dx_ptrs in scenario["lines"]:
        line_id = str(uuid.uuid4())
        await db.claim_lines.insert_one({
            "id": line_id,
            "demo_seed_key": scenario["key"],
            "tenant_id": tenant_id,
            "claim_id": claim_id,
            "sequence": seq,
            "service_date": service_date,
            "code_type": "CPT",
            "code": cpt,
            "units": units,
            "billed_cents": line_billed,
            "diagnosis_pointers": dx_ptrs,
            "created_at": now,
        })
        for i, mod in enumerate(mods, start=1):
            await db.claim_line_modifiers.insert_one({
                "id": str(uuid.uuid4()),
                "demo_seed_key": scenario["key"],
                "tenant_id": tenant_id,
                "claim_line_id": line_id,
                "sequence": i,
                "modifier_code": mod,
                "created_at": now,
            })
    for seq, icd in scenario["diagnoses"]:
        await db.claim_diagnoses.insert_one({
            "id": str(uuid.uuid4()),
            "demo_seed_key": scenario["key"],
            "tenant_id": tenant_id,
            "claim_id": claim_id,
            "sequence": seq,
            "code": icd,
            "created_at": now,
        })

    # Claim events — timeline on the detail view
    events: list[tuple[str, str, dict]] = [
        (scenario["days_ago"], "created", {"billed_cents": billed}),
    ]
    if scenario["status"] not in ("draft", "validation_failed"):
        events.append((
            scenario["days_ago"], "submitted",
            {"method": scenario.get("submission_method", "edi_837p")},
        ))
    if scenario["status"] in ("accepted", "paid",
                               "partially_paid", "denied"):
        events.append((
            max(0, scenario["days_ago"] - 1), "accepted",
            {"payer_reference": f"ACK-{claim_id[:8]}"},
        ))
    if scenario["status"] in ("paid", "partially_paid"):
        events.append((
            max(0, scenario["days_ago"] - 2), "paid",
            {"paid_cents": paid,
             "adjustment_cents": scenario.get("adjustment_cents", 0)},
        ))
    if scenario["status"] == "denied":
        events.append((
            max(0, scenario["days_ago"] - 1), "denied",
            {"denial_code": scenario.get("denial_code"),
             "reason": scenario.get("denial_reason")},
        ))
    if scenario["status"] == "rejected":
        events.append((
            max(0, scenario["days_ago"] - 1), "rejected",
            {"denial_code": scenario.get("denial_code"),
             "reason": scenario.get("denial_reason")},
        ))
    if scenario.get("followup_flag"):
        events.append((
            max(0, scenario["days_ago"] - 1), "followup_flagged",
            {"reason": scenario.get("followup_reason")},
        ))
    for d_ago, ev, payload in events:
        await db.claim_events.insert_one({
            "id": str(uuid.uuid4()),
            "demo_seed_key": scenario["key"],
            "tenant_id": tenant_id,
            "claim_id": claim_id,
            "event_type": ev,
            "actor_id": refs["doctor_id"],
            "payload": payload,
            "created_at": _iso_ts_ago(d_ago),
        })

    # Submission + remit rows (once the claim left the building)
    if scenario["status"] not in ("draft", "validation_failed"):
        sub_id = str(uuid.uuid4())
        is_portal = scenario.get("submission_method") == "portal"
        await db.claim_submissions.insert_one({
            "id": sub_id,
            "demo_seed_key": scenario["key"],
            "tenant_id": tenant_id,
            "claim_id": claim_id,
            "method": "manual_portal" if is_portal else "batch_file",
            "external_reference": f"DEMO-{scenario['key'][-6:].upper()}",
            "submitted_at": _iso_ts_ago(scenario["days_ago"]),
            "submitted_by": refs["doctor_id"],
            "payload_format": (
                "manual" if is_portal else "x12-837p-preview"
            ),
            "payload_size_bytes": 0 if is_portal else billed // 4 + 500,
            "adapter_route": (
                "none" if is_portal else "change_healthcare"
            ),
            "adapter_status": (
                "sent" if is_portal else "accepted"
            ),
            "adapter_external_id": (
                None if is_portal else f"CHC-{claim_id[:8].upper()}"
            ),
            "adapter_message": None,
            "trace_id": None if is_portal else f"trc-{claim_id[:12]}",
            "correlation_id": None if is_portal else f"cor-{claim_id[:12]}",
            "sandbox": not is_portal,
            "outcome": (
                "paid" if scenario["status"] in ("paid", "partially_paid")
                else ("denied" if scenario["status"] == "denied"
                      else ("rejected" if scenario["status"] == "rejected"
                            else ("accepted" if scenario["status"] == "accepted"
                                  else None)))
            ),
            "outcome_at": (
                _iso_ts_ago(max(0, scenario["days_ago"] - 2))
                if scenario["status"] in ("paid", "denied", "rejected")
                else None
            ),
            "outcome_by": refs["billing_user_id"],
            "payer_reference": f"ACK-{claim_id[:8]}",
            "denial_code": scenario.get("denial_code"),
            "paid_cents": paid if paid > 0 else None,
            "notes": scenario.get("denial_reason"),
        })

        if scenario["status"] in ("paid", "partially_paid"):
            # ERA-backed remittance with line-level adjudication.
            remit_id = str(uuid.uuid4())
            await db.remittances.insert_one({
                "id": remit_id,
                "demo_seed_key": scenario["key"],
                "tenant_id": tenant_id,
                "payer_id": payer["id"],
                "source": "era_835",
                "received_at": _iso_ts_ago(
                    max(0, scenario["days_ago"] - 2)
                ),
                "payer_reference": f"ERA-{scenario['key'][-6:].upper()}",
                "total_paid_cents": paid,
                "claim_count": 1,
                "created_at": _iso_ts_ago(
                    max(0, scenario["days_ago"] - 2)
                ),
                "updated_at": now,
                "posted_by": refs["billing_user_id"],
            })
            await db.remittance_claims.insert_one({
                "id": str(uuid.uuid4()),
                "demo_seed_key": scenario["key"],
                "tenant_id": tenant_id,
                "remittance_id": remit_id,
                "claim_id": claim_id,
                "billed_cents": billed,
                "paid_cents": paid,
                "adjustment_cents": scenario.get("adjustment_cents", 0),
                "patient_responsibility_cents": scenario.get(
                    "patient_resp_cents", 0,
                ),
                "status": "posted",
            })
    return claim_id, scenario


# ---------------------------------------------------------------------------
async def _seed_invoices_and_statements(
    tenant_id: str, refs: dict, seeded_claims: list[tuple[str, dict]],
) -> None:
    """Build the patient-responsibility story:
      * Ethan Parker — self-pay wellness visit paid cash same day.
      * Hannah Whitaker — N/A (draft claim, no invoice yet).
      * Aria Johnson — $125 deductible owed (statement-ready).
      * Jaxon Morgan — $60 owed after rejected claim (statement-ready).
      * Claire Morgan — paid in full at visit.
      * Marcus Reid / Isabella Cho / Derrick Stone — $0 pt resp.
    """
    db = get_db_write()
    now = _now()

    def _invoice_doc(
        *,
        key: str, patient_id: str, days_ago: int,
        status: str, total_cents: int, balance_cents: int,
        lines: list[tuple[int, str, str, str, int, int]],
        notes: str | None = None,
    ) -> str:
        iid = str(uuid.uuid4())
        db_coroutines.append(db.invoices.insert_one({
            "id": iid,
            "demo_seed_key": key,
            "tenant_id": tenant_id,
            "location_id": refs["location_id"],
            "patient_id": patient_id,
            "appointment_id": None,
            "status": status,
            "issued_at": _iso_ts_ago(days_ago) if status != "draft" else None,
            "due_date": _iso_date_ago(-14),
            "currency": "USD",
            "subtotal_cents": total_cents,
            "tax_cents": 0,
            "adjustment_cents": 0,
            "total_cents": total_cents,
            "balance_cents": balance_cents,
            "notes": notes,
            "created_at": _iso_ts_ago(days_ago),
            "updated_at": now,
            "created_by": refs["billing_user_id"],
            "updated_by": refs["billing_user_id"],
        }))
        for seq, code_type, code, desc, qty, unit in lines:
            total = qty * unit
            db_coroutines.append(db.invoice_lines.insert_one({
                "id": str(uuid.uuid4()),
                "demo_seed_key": key,
                "tenant_id": tenant_id,
                "invoice_id": iid,
                "sequence": seq,
                "code_type": code_type,
                "code": code,
                "description": desc,
                "service_date": _iso_date_ago(days_ago),
                "quantity": qty,
                "unit_price_cents": unit,
                "total_cents": total,
                "modifiers": [],
                "provider_id": refs["doctor_id"],
                "created_at": _iso_ts_ago(days_ago),
            }))
        return iid

    db_coroutines: list = []

    # Ethan Parker — self-pay maintenance, paid cash day-of -----------------
    ethan = next((p for k, p in refs["patient_by_persona"].items()
                  if k[0] == "Ethan" and k[1] == "Parker"), None)
    if ethan:
        eid = _invoice_doc(
            key="ethan_self_pay_paid",
            patient_id=ethan["id"], days_ago=2, status="paid",
            total_cents=7000, balance_cents=0,
            lines=[(1, "CPT", CPT_CMT_1_2,
                    "Chiropractic adjustment — self-pay cash rate",
                    1, 7000)],
            notes="Cash paid at visit.",
        )
        pay_id = str(uuid.uuid4())
        db_coroutines.append(db.payments.insert_one({
            "id": pay_id,
            "demo_seed_key": "ethan_self_pay_paid",
            "tenant_id": tenant_id,
            "patient_id": ethan["id"],
            "method": "cash",
            "status": "settled",
            "amount_cents": 7000,
            "allocated_cents": 7000,
            "currency": "USD",
            "received_at": _iso_ts_ago(2),
            "posted_by": refs["billing_user_id"],
            "notes": "Cash at front desk",
            "created_at": _iso_ts_ago(2),
            "updated_at": now,
        }))
        db_coroutines.append(db.payment_allocations.insert_one({
            "id": str(uuid.uuid4()),
            "demo_seed_key": "ethan_self_pay_paid",
            "tenant_id": tenant_id,
            "payment_id": pay_id,
            "invoice_id": eid,
            "invoice_line_id": None,
            "amount_cents": 7000,
            "created_at": _iso_ts_ago(2),
        }))

    # Aria Johnson — $125 deductible owed after ERA posting -----------------
    aria = next((p for k, p in refs["patient_by_persona"].items()
                 if k[0] == "Aria" and k[1] == "Johnson"), None)
    if aria:
        iid = _invoice_doc(
            key="aria_deductible_open",
            patient_id=aria["id"], days_ago=12, status="issued",
            total_cents=12500, balance_cents=12500,
            lines=[(1, "CPT", CPT_CMT_1_2,
                    "Patient responsibility after PacificCare ERA "
                    "(deductible applied)",
                    1, 12500)],
            notes=(
                "Primary paid — deductible balance billed to patient."
            ),
        )
        # Statement-ready snapshot
        db_coroutines.append(db.statements.insert_one({
            "id": str(uuid.uuid4()),
            "demo_seed_key": "aria_deductible_open",
            "tenant_id": tenant_id,
            "patient_id": aria["id"],
            "balance_cents": 12500,
            "generated_at": _iso_ts_ago(5),
            "status": "ready",
            "invoice_ids": [iid],
            "body": encrypt_text(
                "Statement for Aria Johnson — $125.00 patient "
                "responsibility after PacificCare adjudication."
            ),
            "created_at": _iso_ts_ago(5),
            "updated_at": now,
        }))

    # Jaxon Morgan — $60 owed after rejected claim -------------------------
    jax = next((p for k, p in refs["patient_by_persona"].items()
                if k[0] == "Jaxon" and k[1] == "Morgan"), None)
    if jax:
        iid = _invoice_doc(
            key="jaxon_rejected_balance",
            patient_id=jax["id"], days_ago=42, status="issued",
            total_cents=6000, balance_cents=6000,
            lines=[(1, "CPT", CPT_CMT_1_2,
                    "Pediatric chiropractic visit — claim rejected "
                    "(subscriber mismatch); balance billed to guarantor.",
                    1, 6000)],
            notes="Guarantor: Claire Morgan.",
        )
        db_coroutines.append(db.statements.insert_one({
            "id": str(uuid.uuid4()),
            "demo_seed_key": "jaxon_rejected_balance",
            "tenant_id": tenant_id,
            "patient_id": jax["id"],
            "balance_cents": 6000,
            "generated_at": _iso_ts_ago(30),
            "status": "ready",
            "invoice_ids": [iid],
            "body": encrypt_text(
                "Statement for Jaxon Morgan (guarantor: Claire Morgan) "
                "— $60.00 after rejected claim."
            ),
            "created_at": _iso_ts_ago(30),
            "updated_at": now,
        }))

    # Hannah Whitaker — small $60 copay open ------------------------------
    hannah = next((p for k, p in refs["patient_by_persona"].items()
                   if k[0] == "Hannah" and k[1] == "Whitaker"), None)
    if hannah:
        _invoice_doc(
            key="hannah_copay_open",
            patient_id=hannah["id"], days_ago=0, status="issued",
            total_cents=3000, balance_cents=3000,
            lines=[(1, "CPT", CPT_NEW_PT,
                    "New patient copay — Cascade Blue Shield $30",
                    1, 3000)],
            notes="Copay collected at check-in pending.",
        )

    if db_coroutines:
        # Sequential await so we never race a line into an invoice
        # that doesn't exist yet.
        for c in db_coroutines:
            await c


# ---------------------------------------------------------------------------
async def seed_demo_billing() -> None:
    """Idempotent curated billing seed for the Riverbend tenant.

    Run order:
      1. Resolve refs (patients/payers/providers/policies).
      2. Purge any prior demo rows tagged with `demo_seed_key`.
      3. Seed each claim scenario (with submission + remittance rows
         as appropriate).
      4. Seed the invoice / payment / statement story that reflects
         the patient-responsibility side of the ledger.
    """
    db = get_db_write()
    tenant = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    if not tenant:
        logger.info("demo.billing_seed: default tenant missing — skipping")
        return
    tid = tenant["id"]
    refs = await _lookup_refs(tid)
    if not refs["doctor_id"] or not refs["patient_by_persona"]:
        logger.info(
            "demo.billing_seed: patients / doctor not ready — skipping "
            "(demo.seed() must run first)",
        )
        return

    await _wipe_prior_demo(tid)

    seeded: list[tuple[str, dict]] = []
    for scenario in _CLAIM_SCENARIOS:
        result = await _seed_one_claim(tid, scenario, refs)
        if result:
            seeded.append(result)

    await _seed_invoices_and_statements(tid, refs, seeded)

    logger.info(
        "demo.billing_seed complete: %d claims, %d invoices/statements",
        len(seeded), 4,   # Ethan + Aria + Jaxon + Hannah
    )
