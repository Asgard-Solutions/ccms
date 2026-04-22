"""
Billing seed — code catalog, modifiers, fee schedule placeholders.

Idempotent. Safe to call on every boot. This intentionally seeds only a
minimal chiropractic-relevant catalog; production tenants will override
the catalog through dedicated CRUD endpoints (not yet implemented).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from core.db import get_db_write

logger = logging.getLogger("ccms.billing.seed")

# Common chiropractic CPT codes (public AMA codes used illustratively).
_CPT_CATALOG: list[tuple[str, str, int]] = [
    ("98940", "Chiropractic manipulative treatment; 1-2 regions", 5500),
    ("98941", "Chiropractic manipulative treatment; 3-4 regions", 7500),
    ("98942", "Chiropractic manipulative treatment; 5 regions", 9000),
    ("97110", "Therapeutic exercises", 4000),
    ("97140", "Manual therapy techniques", 4200),
    ("97012", "Mechanical traction", 3200),
    ("97014", "Electrical stimulation (unattended)", 2800),
    ("99202", "Office/outpatient visit, new patient, 15-29 min", 12000),
    ("99213", "Office/outpatient visit, established patient, 20-29 min", 10000),
]

# Common HCPCS Level II "modifiers" are actually a separate concept; the
# codes below are Level II procedure codes. CMS "modifier codes" (-25, -59,
# GA, GP, etc.) live in the modifier catalog.
_MODIFIER_CATALOG: list[tuple[str, str]] = [
    ("25", "Significant, separately identifiable E/M service on the same day"),
    ("59", "Distinct procedural service"),
    ("GA", "Waiver of liability statement on file"),
    ("GP", "Services delivered under an outpatient physical therapy plan of care"),
    ("GY", "Item/service statutorily excluded"),
    ("GZ", "Item/service expected to be denied as not reasonable and necessary"),
]


async def seed_billing() -> None:
    db = get_db_write()
    now = datetime.now(timezone.utc).isoformat()

    # The code catalog and modifier catalog are tenant-agnostic system
    # defaults (tenant_id=None). Tenants override entries by inserting
    # tenant-scoped rows with the same code; the router layer is where
    # that override resolution will live once catalog CRUD is added.
    for code, desc, price in _CPT_CATALOG:
        await db.billing_code_catalog.update_one(
            {"tenant_id": None, "code_type": "cpt", "code": code},
            {
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "tenant_id": None,
                    "code_type": "cpt",
                    "code": code,
                    "created_at": now,
                },
                "$set": {
                    "description": desc,
                    "default_price_cents": price,
                    "active": True,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    for code, desc in _MODIFIER_CATALOG:
        await db.billing_modifier_catalog.update_one(
            {"tenant_id": None, "code": code},
            {
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "tenant_id": None,
                    "code": code,
                    "created_at": now,
                },
                "$set": {
                    "description": desc,
                    "active": True,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    logger.info(
        "billing.seed complete: %d CPT codes / %d modifiers (system defaults)",
        len(_CPT_CATALOG), len(_MODIFIER_CATALOG),
    )

    # Phase 2a — clearinghouse routing backfill.
    #
    # Existing payer rows created before the clearinghouse-routing
    # fields existed are missing the four new keys. Fill them in with
    # the safe defaults (none / portal / not_started / null) so
    # `PayerPublic` validation and routing both keep working without a
    # dedicated migration script. Idempotent — only rows missing the
    # field are touched.
    for field_name, default_value in (
        ("clearinghouse_route", "none"),
        ("claim_submission_mode", "portal"),
        ("enrollment_status", "not_started"),
        ("trading_partner_id", None),
    ):
        res = await db.billing_payers.update_many(
            {field_name: {"$exists": False}},
            {"$set": {field_name: default_value, "updated_at": now}},
        )
        if res.modified_count:
            logger.info(
                "billing.seed backfilled %s on %d payer rows",
                field_name, res.modified_count,
            )

    # Phase 5 — patient_control_number backfill.
    #
    # Every claim row needs a non-null PCN so the clearinghouse
    # payload builder can always populate CLM01. For rows that predate
    # Phase 5 we derive `CCMS-<first 8 chars of uuid, upper>` from
    # the claim's existing id — deterministic and unique per tenant.
    legacy_claims = db.claims.find(
        {"$or": [
            {"patient_control_number": {"$exists": False}},
            {"patient_control_number": None},
            {"patient_control_number": ""},
        ]},
        {"_id": 0, "id": 1},
    )
    backfilled = 0
    async for c in legacy_claims:
        await db.claims.update_one(
            {"id": c["id"]},
            {"$set": {
                "patient_control_number": f"CCMS-{c['id'][:8].upper()}",
                "updated_at": now,
            }},
        )
        backfilled += 1
    if backfilled:
        logger.info(
            "billing.seed backfilled patient_control_number on %d claim rows",
            backfilled,
        )
