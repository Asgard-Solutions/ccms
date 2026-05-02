"""Recurring auto-charge engine — payment schedules + worker.

A `payment_schedule` is the canonical row for "split this $X into N
charges every {weekly|biweekly|monthly} starting on Y". The worker
(`process_due_schedules`) periodically wakes up, finds rows whose
`next_charge_at` is in the past, and charges them through the Helcim
Customer Vault.

Each attempt is recorded as a `payment_schedule_runs` row — never
deleted, even after success/failure. Failed attempts are retried up
to 3 times with a 1d → 3d → 7d backoff. After the 3rd failure, the
schedule is marked `failed` and a tenant-scoped notification is
created so the clinic admin can intervene.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.tenancy import tenant_db
from services.billing.helcim import now_iso
from services.billing.helcim.card_vault import (
    get_decrypted as get_card_decrypted,
    record_use as record_card_use,
)
from services.billing.helcim.client import HelcimClient
from services.billing.helcim.credentials import get_decrypted_credentials

logger = logging.getLogger("ccms.billing.helcim.scheduler")

SCHEDULES = "payment_schedules"
RUNS = "payment_schedule_runs"

# Used by chiro practices: Statement (monthly), payment plans (biweekly /
# weekly / monthly), treatment plans (configurable cadence).
Frequency = Literal["weekly", "biweekly", "monthly"]
ScheduleStatus = Literal[
    "active", "paused", "completed", "cancelled", "failed",
]
ScheduleKind = Literal["payment_plan", "treatment_plan", "statement_autopay"]

RETRY_BACKOFF_DAYS = (1, 3, 7)
MAX_FAILED_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    card_token_id: str
    kind: ScheduleKind = "payment_plan"
    label: str = Field(min_length=1, max_length=140)
    invoice_id: str | None = None
    treatment_plan_id: str | None = None
    total_cents: int = Field(ge=1, le=10_000_000)
    num_charges: int = Field(ge=1, le=120)
    frequency: Frequency = "monthly"
    start_at: str  # ISO date or datetime; treated as UTC
    notes: str | None = Field(default=None, max_length=500)


class SchedulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    notes: str | None = None
    next_charge_at: str | None = None  # admin reschedule


class SchedulePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    patient_id: str
    card_token_id: str
    kind: ScheduleKind
    label: str
    invoice_id: str | None = None
    treatment_plan_id: str | None = None
    total_cents: int
    per_charge_cents: int
    last_charge_cents: int
    num_charges: int
    charges_completed: int
    charges_failed: int
    consecutive_failures: int
    frequency: Frequency
    start_at: str
    next_charge_at: str | None
    status: ScheduleStatus
    notes: str | None = None
    created_at: str
    last_run_at: str | None = None


class RunPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    schedule_id: str
    attempted_at: str
    outcome: Literal["success", "declined", "error", "skipped"]
    amount_cents: int
    helcim_transaction_id: str | None = None
    error: str | None = None
    attempt_number: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(iso: str) -> datetime:
    """Tolerant ISO parser — accepts 'YYYY-MM-DD' or full ISO."""
    if "T" in iso or " " in iso:
        # full datetime
        s = iso.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            d = datetime.fromisoformat(s.split("+")[0])
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    # date only
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _advance(prev: datetime, frequency: Frequency) -> datetime:
    if frequency == "weekly":
        return prev + timedelta(days=7)
    if frequency == "biweekly":
        return prev + timedelta(days=14)
    # monthly — naive month-add (not calendar-correct for Feb 30, but
    # acceptable for billing cadence).
    month = prev.month + 1
    year = prev.year
    if month > 12:
        month -= 12
        year += 1
    day = min(prev.day, 28)  # avoid invalid day-of-month edge cases
    return prev.replace(year=year, month=month, day=day)


def _split_amount(total_cents: int, n: int) -> tuple[int, int]:
    """Even per-charge cents + final-charge cents that absorbs rounding."""
    per = total_cents // n
    last = total_cents - per * (n - 1)
    return per, last


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def create_schedule(tenant_id: str, payload: ScheduleCreate, *,
                          actor: dict) -> dict:
    db = tenant_db(tenant_id)
    per, last = _split_amount(payload.total_cents, payload.num_charges)
    start = _parse_dt(payload.start_at)
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "patient_id": payload.patient_id,
        "card_token_id": payload.card_token_id,
        "kind": payload.kind,
        "label": payload.label,
        "invoice_id": payload.invoice_id,
        "treatment_plan_id": payload.treatment_plan_id,
        "total_cents": payload.total_cents,
        "per_charge_cents": per,
        "last_charge_cents": last,
        "num_charges": payload.num_charges,
        "charges_completed": 0,
        "charges_failed": 0,
        "consecutive_failures": 0,
        "frequency": payload.frequency,
        "start_at": start.isoformat(),
        "next_charge_at": start.isoformat(),
        "status": "active",
        "notes": payload.notes,
        "created_at": now_iso(),
        "created_by": actor.get("email") or actor.get("id"),
        "last_run_at": None,
    }
    await db[SCHEDULES].insert_one(doc)
    doc.pop("_id", None)
    return doc


async def list_schedules(tenant_id: str, *, patient_id: str | None = None,
                          status: str | None = None) -> list[dict]:
    db = tenant_db(tenant_id)
    q: dict = {"tenant_id": tenant_id}
    if patient_id:
        q["patient_id"] = patient_id
    if status and status != "all":
        q["status"] = status
    return await db[SCHEDULES].find(q, {"_id": 0}).sort("created_at", -1).to_list(length=500)


async def list_runs(tenant_id: str, schedule_id: str) -> list[dict]:
    db = tenant_db(tenant_id)
    return await db[RUNS].find(
        {"tenant_id": tenant_id, "schedule_id": schedule_id},
        {"_id": 0},
    ).sort("attempted_at", -1).to_list(length=500)


async def patch_schedule(tenant_id: str, sid: str, payload: SchedulePatch) -> dict:
    db = tenant_db(tenant_id)
    update: dict = {}
    if payload.label is not None:
        update["label"] = payload.label
    if payload.notes is not None:
        update["notes"] = payload.notes
    if payload.next_charge_at is not None:
        update["next_charge_at"] = _parse_dt(payload.next_charge_at).isoformat()
    if not update:
        return await db[SCHEDULES].find_one(
            {"id": sid, "tenant_id": tenant_id}, {"_id": 0},
        )
    await db[SCHEDULES].update_one(
        {"id": sid, "tenant_id": tenant_id}, {"$set": update},
    )
    return await db[SCHEDULES].find_one(
        {"id": sid, "tenant_id": tenant_id}, {"_id": 0},
    )


async def transition_status(tenant_id: str, sid: str,
                             new_status: ScheduleStatus) -> dict:
    db = tenant_db(tenant_id)
    await db[SCHEDULES].update_one(
        {"id": sid, "tenant_id": tenant_id},
        {"$set": {"status": new_status,
                  "consecutive_failures": 0 if new_status == "active" else None}},
    )
    return await db[SCHEDULES].find_one(
        {"id": sid, "tenant_id": tenant_id}, {"_id": 0},
    )


# ---------------------------------------------------------------------------
# Worker — charge due schedules
# ---------------------------------------------------------------------------

@dataclass
class ChargeOutcome:
    schedule_id: str
    outcome: str  # success | declined | error | skipped
    amount_cents: int
    helcim_transaction_id: str | None
    error: str | None
    attempt_number: int


async def _record_run(tenant_id: str, sched: dict, outcome: ChargeOutcome) -> None:
    db = tenant_db(tenant_id)
    await db[RUNS].insert_one({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "schedule_id": outcome.schedule_id,
        "attempted_at": now_iso(),
        "outcome": outcome.outcome,
        "amount_cents": outcome.amount_cents,
        "helcim_transaction_id": outcome.helcim_transaction_id,
        "error": outcome.error,
        "attempt_number": outcome.attempt_number,
    })


async def _notify_admin_failure(tenant_id: str, sched: dict, reason: str) -> None:
    """Post a tenant-scoped notification when a schedule terminally fails."""
    db = tenant_db(tenant_id)
    await db.notifications.insert_one({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "category": "billing",
        "severity": "warning",
        "title": f"Auto-charge schedule failed: {sched.get('label', '?')}",
        "body": (
            f"Patient {sched.get('patient_id')} payment schedule "
            f"{sched.get('id')} failed after {MAX_FAILED_ATTEMPTS} attempts. "
            f"Last error: {reason}. Update the card on file or contact the patient."
        ),
        "patient_id": sched.get("patient_id"),
        "read": False,
        "created_at": now_iso(),
    })


async def charge_one_schedule(tenant_id: str, sched: dict) -> ChargeOutcome:
    """Charge one schedule via Helcim and update its state.

    Pure async function — exposed for direct admin "Run now" invocation
    from the router as well as the periodic worker.
    """
    db = tenant_db(tenant_id)
    sid = sched["id"]
    attempt_number = sched.get("charges_completed", 0) + sched.get("charges_failed", 0) + 1

    # Determine this charge's amount (final charge absorbs the rounding).
    is_final = sched["charges_completed"] + 1 == sched["num_charges"]
    amount_cents = sched["last_charge_cents"] if is_final else sched["per_charge_cents"]

    creds = await get_decrypted_credentials(tenant_id)
    if not creds:
        outcome = ChargeOutcome(
            schedule_id=sid, outcome="error", amount_cents=amount_cents,
            helcim_transaction_id=None,
            error="Helcim credentials not configured for tenant.",
            attempt_number=attempt_number,
        )
        await _record_run(tenant_id, sched, outcome)
        return outcome

    card = await get_card_decrypted(tenant_id, sched["card_token_id"])
    if not card:
        outcome = ChargeOutcome(
            schedule_id=sid, outcome="error", amount_cents=amount_cents,
            helcim_transaction_id=None,
            error="Saved card was deleted or could not be loaded.",
            attempt_number=attempt_number,
        )
        await _record_run(tenant_id, sched, outcome)
        return outcome

    cli = HelcimClient(creds["api_token"])
    res = await cli.purchase_with_card_token(
        amount=amount_cents / 100, currency="USD",
        card_token=card["card_token"],
        customer_code=card.get("customer_code"),
        invoice_number=sched.get("invoice_id"),
        comments=f"Schedule {sched.get('label')} ({attempt_number}/{sched['num_charges']})",
    )
    txn = (res.get("data") or {}).get("transaction") if isinstance(res.get("data"), dict) else None
    approved = res.ok and txn and (txn.get("status") == "APPROVED")
    txn_id = str(txn.get("transactionId")) if txn and txn.get("transactionId") else None

    outcome = ChargeOutcome(
        schedule_id=sid,
        outcome=("success" if approved else
                  "declined" if (txn and not approved) else
                  "error"),
        amount_cents=amount_cents,
        helcim_transaction_id=txn_id,
        error=(None if approved else
                (res.get("error") or (txn and txn.get("response")) or "unknown")),
        attempt_number=attempt_number,
    )

    # Update schedule state.
    if approved:
        await record_card_use(tenant_id, sched["card_token_id"], outcome="success")
        new_completed = sched["charges_completed"] + 1
        next_at = _advance(_parse_dt(sched["next_charge_at"]), sched["frequency"])
        update = {
            "charges_completed": new_completed,
            "consecutive_failures": 0,
            "last_run_at": now_iso(),
        }
        if new_completed >= sched["num_charges"]:
            update.update({"status": "completed", "next_charge_at": None})
        else:
            update["next_charge_at"] = next_at.isoformat()
        await db[SCHEDULES].update_one(
            {"id": sid, "tenant_id": tenant_id}, {"$set": update},
        )
    else:
        await record_card_use(tenant_id, sched["card_token_id"], outcome=outcome.outcome)
        consecutive = sched.get("consecutive_failures", 0) + 1
        backoff_days = RETRY_BACKOFF_DAYS[min(consecutive - 1, len(RETRY_BACKOFF_DAYS) - 1)]
        update: dict = {
            "charges_failed": sched["charges_failed"] + 1,
            "consecutive_failures": consecutive,
            "last_run_at": now_iso(),
        }
        if consecutive >= MAX_FAILED_ATTEMPTS:
            update["status"] = "failed"
            update["next_charge_at"] = None
            await _notify_admin_failure(tenant_id, sched, outcome.error or outcome.outcome)
        else:
            now_dt = datetime.now(timezone.utc)
            update["next_charge_at"] = (now_dt + timedelta(days=backoff_days)).isoformat()
        await db[SCHEDULES].update_one(
            {"id": sid, "tenant_id": tenant_id}, {"$set": update},
        )

    await _record_run(tenant_id, sched, outcome)
    return outcome


async def find_due(tenant_id: str, *, now: Optional[datetime] = None) -> list[dict]:
    db = tenant_db(tenant_id)
    now = now or datetime.now(timezone.utc)
    return await db[SCHEDULES].find(
        {"tenant_id": tenant_id, "status": "active",
         "next_charge_at": {"$lte": now.isoformat()}},
        {"_id": 0},
    ).to_list(length=200)


async def process_due_schedules(tenant_id: str, *, limit: int = 50) -> list[ChargeOutcome]:
    """Process all schedules that are due for `tenant_id`.

    Called by the background loop on a per-tenant fan-out and by the
    admin `POST /scheduler/tick` endpoint.
    """
    due = await find_due(tenant_id)
    outcomes: list[ChargeOutcome] = []
    for sched in due[:limit]:
        try:
            o = await charge_one_schedule(tenant_id, sched)
            outcomes.append(o)
        except Exception as e:
            logger.exception("scheduler.error tenant=%s schedule=%s err=%s",
                             tenant_id, sched.get("id"), e)
            outcomes.append(ChargeOutcome(
                schedule_id=sched.get("id", "?"), outcome="error",
                amount_cents=0, helcim_transaction_id=None,
                error=str(e), attempt_number=-1,
            ))
    return outcomes
