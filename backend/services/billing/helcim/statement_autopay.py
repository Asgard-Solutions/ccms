"""Statement auto-pay helper — bridge between statement generation and the
recurring auto-charge engine.

When a statement is generated for a patient who has opted in (and the
tenant toggle is enabled, and the patient has a saved card), we drop a
one-shot `statement_autopay` schedule that will charge the open balance
on the configured cadence (default: 3 days post-statement, single
charge). If the charge fails, the standard retry-with-backoff applies.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.tenancy import tenant_db
from services.billing.helcim.scheduler import ScheduleCreate, create_schedule

logger = logging.getLogger("ccms.billing.helcim.statement_autopay")

# Days post-statement before the auto-charge fires. Gives patients a window to
# review the statement and reach out before being charged.
GRACE_DAYS = 3


async def maybe_create_statement_autopay(
    tenant_id: str, *, patient_id: str, statement_id: str,
    total_cents: int, actor: dict,
) -> dict | None:
    db = tenant_db(tenant_id)

    settings = await db.helcim_statement_autopay.find_one(
        {"tenant_id": tenant_id}, {"_id": 0, "enabled": 1},
    )
    if not settings or not settings.get("enabled"):
        logger.debug("statement_autopay: tenant toggle off")
        return None

    optin = await db.helcim_statement_autopay_patients.find_one(
        {"tenant_id": tenant_id, "patient_id": patient_id},
        {"_id": 0},
    )
    if not optin or not optin.get("opted_in"):
        logger.debug("statement_autopay: patient not opted in")
        return None

    # Pick the patient's chosen card or default card.
    card_token_id = optin.get("card_token_id")
    if not card_token_id:
        default_card = await db.patient_card_tokens.find_one(
            {"tenant_id": tenant_id, "patient_id": patient_id,
             "deleted_at": None, "is_default": True},
            {"_id": 0, "id": 1},
        )
        if not default_card:
            # Fall back to most-recent saved card.
            cur = db.patient_card_tokens.find(
                {"tenant_id": tenant_id, "patient_id": patient_id,
                 "deleted_at": None}, {"_id": 0, "id": 1},
            ).sort("created_at", -1).limit(1)
            rows = await cur.to_list(length=1)
            default_card = rows[0] if rows else None
        if not default_card:
            logger.info("statement_autopay: no card on file for patient=%s", patient_id)
            return None
        card_token_id = default_card["id"]

    start_at = (datetime.now(timezone.utc) + timedelta(days=GRACE_DAYS)).isoformat()
    sched = await create_schedule(
        tenant_id,
        ScheduleCreate(
            patient_id=patient_id,
            card_token_id=card_token_id,
            kind="statement_autopay",
            label=f"Statement {statement_id[:8]} auto-pay",
            invoice_id=None,
            total_cents=total_cents,
            num_charges=1,
            frequency="monthly",  # irrelevant for n=1, but required.
            start_at=start_at,
            notes=f"Generated from statement {statement_id}",
        ),
        actor=actor,
    )
    logger.info(
        "statement_autopay: created schedule=%s patient=%s amount_cents=%s",
        sched["id"], patient_id, total_cents,
    )
    return sched
