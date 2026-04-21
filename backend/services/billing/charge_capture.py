"""
Fee schedule + charge capture — bridge between clinical encounters
and billable artifacts.

Design
------
* A `fee_schedule` carries per-code allowed amounts. One self_pay
  schedule acts as the clinic's default. Payer-specific schedules
  override for insurance-covered responsibility.
* Price resolution precedence:
    1. payer-specific fee schedule line (if insurance responsibility + payer)
    2. self_pay default line (for self_pay / fallback)
    3. system code catalog `default_price_cents`
    4. 0 (last resort — surfaced as a warning in the preview)
* A **charge candidate** is a draft invoice line derived from a signed
  medical record's procedures. Candidates are computed server-side so
  the UI cannot alter the price rules.
* **Capturing** a record converts the candidates into an invoice in
  `draft` status with the record's patient + location. The record is
  then flagged `charge_status=captured` + linked to the invoice. The
  invoice is NOT auto-issued; operators review and issue per the
  existing invoice lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase


@dataclass
class PriceResolution:
    code: str
    unit_price_cents: int
    source: str              # "payer_schedule" | "self_pay_schedule" | "catalog" | "zero"
    fee_schedule_id: str | None


async def _find_schedule_line(
    db: AsyncIOMotorDatabase, tenant_id: str,
    fee_schedule_id: str, code: str,
) -> dict | None:
    return await db.fee_schedule_lines.find_one(
        {"tenant_id": tenant_id,
         "fee_schedule_id": fee_schedule_id, "code": code},
        {"_id": 0},
    )


async def resolve_charge_price(
    db: AsyncIOMotorDatabase,
    *,
    tenant_id: str,
    responsibility: str,
    payer_id: str | None,
    code_type: str,
    code: str,
) -> PriceResolution:
    """Return the resolved `PriceResolution` for one procedure code."""
    # 1. Payer schedule (when insurance + payer resolved to an active schedule).
    if responsibility in ("insurance", "mixed") and payer_id:
        schedule = await db.fee_schedules.find_one(
            {"tenant_id": tenant_id, "kind": "payer",
             "payer_id": payer_id, "active": True},
            {"_id": 0},
        )
        if schedule:
            line = await _find_schedule_line(
                db, tenant_id, schedule["id"], code,
            )
            if line:
                return PriceResolution(
                    code=code, unit_price_cents=line["allowed_cents"],
                    source="payer_schedule",
                    fee_schedule_id=schedule["id"],
                )

    # 2. Self-pay / default schedule.
    schedule = await db.fee_schedules.find_one(
        {"tenant_id": tenant_id, "kind": "self_pay", "active": True},
        {"_id": 0},
    )
    if schedule:
        line = await _find_schedule_line(db, tenant_id, schedule["id"], code)
        if line:
            return PriceResolution(
                code=code, unit_price_cents=line["allowed_cents"],
                source="self_pay_schedule",
                fee_schedule_id=schedule["id"],
            )

    # 3. System catalog default — same collection used by billing_code_catalog.
    catalog = await db.billing_code_catalog.find_one(
        {"$or": [
            {"tenant_id": tenant_id, "code_type": code_type, "code": code},
            {"tenant_id": None, "code_type": code_type, "code": code},
        ]},
        {"_id": 0, "default_price_cents": 1},
    )
    if catalog:
        return PriceResolution(
            code=code, unit_price_cents=catalog.get("default_price_cents", 0),
            source="catalog", fee_schedule_id=None,
        )

    return PriceResolution(
        code=code, unit_price_cents=0, source="zero", fee_schedule_id=None,
    )


async def build_charge_candidates(
    db: AsyncIOMotorDatabase, *, tenant_id: str, record: dict,
) -> dict[str, Any]:
    """Return a draft preview of charges derived from one medical record.

    Output shape:
    ```
    {
      "record_id": str,
      "patient_id": str,
      "responsibility": "self_pay" | "insurance" | "mixed",
      "payer_id": str | None,            # resolved active primary policy
      "policy_id": str | None,
      "lines": [
        {"code", "description", "quantity", "unit_price_cents",
         "total_cents", "price_source", "fee_schedule_id",
         "modifiers", "service_date"}, ...
      ],
      "warnings": [str, ...],
      "total_cents": int,
    }
    ```

    The caller decides whether to persist (`capture_record`) or discard.
    """
    warnings: list[str] = []
    procedures = record.get("procedures") or []
    if not procedures:
        warnings.append("Record has no procedures to bill.")

    responsibility = record.get("responsibility") or "self_pay"
    payer_id: str | None = None
    policy_id: str | None = None

    if responsibility in ("insurance", "mixed"):
        policy = await db.patient_insurance_policies.find_one(
            {"tenant_id": tenant_id,
             "patient_id": record["patient_id"],
             "rank": "primary",
             "status": "active"},
            {"_id": 0},
        )
        if policy:
            payer_id = policy["payer_id"]
            policy_id = policy["id"]
            if not policy.get("member_id"):
                warnings.append("Active primary policy is missing a member ID.")
        else:
            warnings.append(
                "Insurance responsibility set but no active primary "
                "policy was found. Capture will be blocked.",
            )

    service_date = (record.get("recorded_at") or "")[:10] or None

    lines: list[dict] = []
    for p in procedures:
        res = await resolve_charge_price(
            db, tenant_id=tenant_id,
            responsibility=responsibility, payer_id=payer_id,
            code_type=p.get("code_type", "cpt"), code=p["code"],
        )
        if res.source == "zero":
            warnings.append(f"No price found for code {p['code']}.")
        qty = int(p.get("units", 1))
        total = res.unit_price_cents * qty
        # Look up a human description if available.
        cat = await db.billing_code_catalog.find_one(
            {"$or": [
                {"tenant_id": tenant_id, "code_type": p.get("code_type", "cpt"),
                 "code": p["code"]},
                {"tenant_id": None, "code_type": p.get("code_type", "cpt"),
                 "code": p["code"]},
            ]},
            {"_id": 0, "description": 1},
        )
        lines.append({
            "code_type": p.get("code_type", "cpt"),
            "code": p["code"],
            "description": (cat or {}).get("description") or p["code"],
            "quantity": qty,
            "unit_price_cents": res.unit_price_cents,
            "total_cents": total,
            "price_source": res.source,
            "fee_schedule_id": res.fee_schedule_id,
            "modifiers": p.get("modifiers") or [],
            "service_date": service_date,
        })

    total_cents = sum(ln["total_cents"] for ln in lines)
    return {
        "record_id": record["id"],
        "patient_id": record["patient_id"],
        "responsibility": responsibility,
        "payer_id": payer_id,
        "policy_id": policy_id,
        "lines": lines,
        "warnings": warnings,
        "total_cents": total_cents,
        "can_capture": (
            bool(lines)
            and (responsibility == "self_pay" or payer_id is not None)
        ),
    }
