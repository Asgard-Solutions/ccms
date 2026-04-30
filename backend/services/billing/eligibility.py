"""
services/billing/eligibility.py — Eligibility 270/271 orchestration.

The flow:
  1. Caller supplies (patient, policy, payer, provider) canonical rows.
  2. `build_270_request(...)` produces a spec-compliant 270 wire.
  3. The chosen engine (mock or live clearinghouse) returns a 271 wire.
  4. `parse_271_response(...)` returns a canonical dict the UI renders.

`MockEligibilityEngine` is the default engine — it synthesises a
deterministic 271 from the policy/payer shape so the workflow works
end-to-end without a live clearinghouse. When Change Healthcare /
Optum sandbox credentials are configured, `LiveEligibilityEngine` will
post the 270 to the real endpoint (future work — the scaffolding in
services/billing/clearinghouse/change_healthcare.py is the seam).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from services.billing.clearinghouse.x12_270_271 import (
    ELEMENT_SEP,
    SEGMENT_JOIN,
    SEGMENT_TERMINATOR,
    SERVICE_TYPE_LABELS,
    build_270_request,
    parse_271_response,
    _digits,
    _upper,
    _yyyymmdd,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_plan_profile(policy: dict, payer: dict) -> dict[str, Any]:
    """Derive a deterministic plan benefit profile from the policy +
    payer shape. Used by `MockEligibilityEngine` so every member_id
    always resolves to the same canned 271. The profile is stable
    (hash-seeded) — re-running the check returns identical values.
    """
    seed_material = (
        (policy.get("member_id") or "")
        + "|" + (policy.get("group_number") or "")
        + "|" + (payer.get("id") or "")
    )
    h = hashlib.sha256(seed_material.encode("utf-8")).digest()
    # Deterministic "roll" 0..99 drives eligibility outcomes. Keep
    # most members active so demos feel realistic.
    roll = h[0] % 100

    payer_type = (payer.get("payer_type") or "commercial").lower()

    # Termination-rigged rows (member_id ending in TERM) — for demo
    # scrips that want to showcase an inactive-coverage path.
    inactive = (
        (policy.get("member_id") or "").upper().endswith("TERM")
        or roll < 3
    )
    if inactive:
        return {
            "coverage_active": False,
            "plan_name": "INACTIVE COVERAGE",
            "effective_date": policy.get("effective_date"),
            "termination_date": policy.get("termination_date")
                                or "2025-12-31",
            "copay_cents": None,
            "coinsurance_pct": None,
            "deductible_cents": None,
            "deductible_met_cents": None,
            "out_of_pocket_cents": None,
            "notes": [
                "Coverage terminated. Member must update benefits "
                "before scheduling.",
            ],
        }

    if payer_type == "medicare":
        plan = "MEDICARE PART B"
        copay = 0  # Part B is 20% coinsurance after deductible
        coinsurance = 20
        deductible = 24000  # $240 2026 Part B deductible in cents
    elif payer_type == "medicaid":
        plan = "MEDICAID MANAGED CARE"
        copay = 0
        coinsurance = 0
        deductible = 0
    elif payer_type == "workers_comp":
        plan = "WORKERS' COMPENSATION"
        copay = 0
        coinsurance = 0
        deductible = 0
    elif payer_type == "auto":
        plan = "AUTO MEDICAL — PIP"
        copay = 0
        coinsurance = 0
        deductible = 25000  # $250
    else:  # commercial fallback
        plan = (policy.get("group_number") or "PPO OPEN ACCESS").upper()
        # Copay scales with hash byte 1 (25..40 = $25..$40 in $5 bumps)
        copay = (2500 + ((h[1] % 4) * 500))
        # Deductible scales with hash byte 2 (1000..2500 in cents/100)
        deductible = (100000 + ((h[2] % 6) * 25000))
        coinsurance = 20

    # Percentage "met" drives the UI remaining-deductible display.
    if deductible:
        met_pct = h[3] % 100  # 0..99 %
        met = (deductible * met_pct) // 100
        met = (met // 100) * 100  # round to whole dollars
    else:
        met = 0

    # Out-of-pocket max tracks deductible tier.
    oop = deductible * 6 if deductible else 0

    return {
        "coverage_active": True,
        "plan_name": plan,
        "effective_date": policy.get("effective_date") or "2026-01-01",
        "termination_date": policy.get("termination_date") or "2026-12-31",
        "copay_cents": copay,
        "coinsurance_pct": coinsurance,
        "deductible_cents": deductible,
        "deductible_met_cents": met,
        "out_of_pocket_cents": oop,
        "notes": [],
    }


def _build_271_response(
    *,
    request_wire: str,
    submitter: dict,
    provider: dict,
    payer: dict,
    patient: dict,
    policy: dict,
    profile: dict[str, Any],
    service_type_codes: list[str],
    now: datetime | None = None,
) -> str:
    """Synthesise a deterministic 271 wire mirroring the 270.

    The 271 is written with the EB segments the parser expects so the
    round-trip is loss-free. Every financial figure lives in both the
    canonical profile AND a structured EB segment.
    """
    now = now or datetime.now(timezone.utc)
    segments: list[str] = []

    def seg(*fields: Any) -> None:
        rendered = [("" if f is None else str(f)) for f in fields]
        while len(rendered) > 1 and rendered[-1] == "":
            rendered.pop()
        segments.append(ELEMENT_SEP.join(rendered))

    # Envelope — for round-trip parsing we only need ST..SE. The
    # adapter persists both request and response wires so the
    # envelope echo is optional. We include a trimmed ISA/GS/GE/IEA
    # for verifiability but do not strictly match the request trace.
    isa13 = uuid.uuid4().hex[:9].zfill(9)
    seg(
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", (payer.get("electronic_payer_id") or "PAYER").upper().ljust(15)[:15],
        "ZZ", (submitter.get("id") or "SUB").upper().ljust(15)[:15],
        now.strftime("%y%m%d"), now.strftime("%H%M"),
        "^", "00501", isa13, "0", "T", ":",
    )
    seg("GS", "HB",
        (payer.get("electronic_payer_id") or "PAYER").upper(),
        (submitter.get("id") or "SUB").upper(),
        now.strftime("%Y%m%d"), now.strftime("%H%M"),
        "1", "X", "005010X279A1")

    # Hash a trace from the 270 BHT so the correlation id is stable.
    trace = hashlib.sha256(request_wire.encode("utf-8")).hexdigest()[:12]

    st_index = len(segments)
    seg("ST", "271", "0001", "005010X279A1")
    seg("BHT", "0022", "11", trace, now.strftime("%Y%m%d"), now.strftime("%H%M"))

    # Source (payer echo)
    seg("HL", "1", "", "20", "1")
    seg("NM1", "PR", "2",
        _upper(payer.get("name") or "PAYER", 60), "", "", "", "",
        "PI", payer.get("electronic_payer_id") or "")

    # Receiver (provider echo)
    seg("HL", "2", "1", "21", "1")
    entity_type = (provider.get("entity_type") or "person").lower()
    if entity_type.startswith("org"):
        seg("NM1", "1P", "2",
            _upper(provider.get("name") or "PROVIDER", 60), "", "", "", "",
            "XX", provider.get("npi") or "")
    else:
        seg("NM1", "1P", "1",
            _upper(provider.get("last_name") or provider.get("name"), 60),
            _upper(provider.get("first_name"), 35), "", "", "",
            "XX", provider.get("npi") or "")

    # Subscriber
    seg("HL", "3", "2", "22", "0")
    seg("TRN", "2", trace, (payer.get("electronic_payer_id") or "PAYER").upper())

    relationship = (policy.get("relationship_to_subscriber") or "self").lower()
    if relationship == "self":
        last = _upper(patient.get("last_name"), 60)
        first = _upper(patient.get("first_name"), 35)
    else:
        raw = (policy.get("subscriber_name") or "").strip()
        if " " in raw:
            parts = raw.rsplit(maxsplit=1)
            first = _upper(parts[0], 35)
            last = _upper(parts[1], 60)
        else:
            last = _upper(raw, 60)
            first = ""
    seg("NM1", "IL", "1", last, first, "", "", "",
        "MI", policy.get("member_id") or "")

    dob = (patient.get("date_of_birth")
           or policy.get("subscriber_dob") or "")
    gender = (patient.get("sex_at_birth") or "U")[:1].upper()
    if _yyyymmdd(dob):
        seg("DMG", "D8", _yyyymmdd(dob), gender)

    # 2110C — EB segments
    if profile["coverage_active"]:
        # EB*1 — active coverage, insurance type, plan description
        ins_type = {
            "medicare": "MB",
            "medicaid": "MC",
            "commercial": "CI",
            "workers_comp": "WC",
            "auto": "AM",
        }.get((payer.get("payer_type") or "commercial").lower(), "CI")
        seg("EB", "1", "IND", "30", ins_type,
            _upper(profile.get("plan_name"), 50))
        # EB*CB — explicit plan description (parser pulls `plan_name`)
        if profile.get("plan_name"):
            seg("EB", "CB", "", "", "",
                _upper(profile["plan_name"], 50))
        # EB*B (Copay) — per professional visit (service type 98)
        if profile.get("copay_cents") is not None:
            copay = profile["copay_cents"]
            seg("EB", "B", "IND", "98", ins_type, "", "27",
                f"{copay/100:.2f}")
            # Also emit for service type 30 for plan-level copay
            seg("EB", "B", "IND", "30", ins_type, "", "27",
                f"{copay/100:.2f}")
        # EB*A (coinsurance) — percent (EB08 is decimal like 0.20)
        if profile.get("coinsurance_pct") is not None:
            pct = profile["coinsurance_pct"] / 100
            seg("EB", "A", "IND", "30", ins_type, "", "", "",
                f"{pct:.2f}")
        # EB*C (deductible) — total calendar year (time period 23)
        if profile.get("deductible_cents") is not None:
            ded = profile["deductible_cents"]
            seg("EB", "C", "IND", "30", ins_type, "", "23",
                f"{ded/100:.2f}")
            # Remaining deductible (time period 29 — "remaining")
            remaining = (profile.get("deductible_cents") or 0) - (
                profile.get("deductible_met_cents") or 0
            )
            if remaining > 0:
                seg("EB", "C", "IND", "30", ins_type, "", "29",
                    f"{remaining/100:.2f}")
        # EB*G (out-of-pocket stop-loss)
        if profile.get("out_of_pocket_cents") is not None:
            oop = profile["out_of_pocket_cents"]
            seg("EB", "G", "IND", "30", ins_type, "", "23",
                f"{oop/100:.2f}")
        # Per-service coverage — flag chiropractic services (33) as
        # covered with the same deductible/coinsurance story.
        for code in service_type_codes:
            if code in ("33", "98") and code != "30":
                seg("EB", "1", "IND", code, ins_type)
    else:
        seg("EB", "6", "IND", "30", "", "INACTIVE")

    # DTP segments — plan begin/end
    if profile.get("effective_date"):
        seg("DTP", "356", "D8", _yyyymmdd(profile["effective_date"]))
    if profile.get("termination_date"):
        seg("DTP", "357", "D8", _yyyymmdd(profile["termination_date"]))

    # MSG — free-text notes
    for note in profile.get("notes", []):
        seg("MSG", note)

    # SE / GE / IEA
    segment_count = len(segments) - st_index + 1
    seg("SE", str(segment_count), "0001")
    seg("GE", "1", "1")
    seg("IEA", "1", isa13)

    return SEGMENT_JOIN.join(segments) + SEGMENT_TERMINATOR


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------
class EligibilityEngineError(Exception):
    """Raised by engines for deterministic, caller-surfaced failures.

    The router maps these to HTTP 4xx/5xx with the message intact.
    Generic exceptions still bubble through and become 500s with a
    sanitised payload."""


class MockEligibilityEngine:
    """Local deterministic engine — builds a 270, synthesises a 271,
    parses it, and returns the canonical result. No network I/O.

    The engine is the default binding for payers with no clearinghouse
    enrollment, and for demo / sandbox flows. It is side-effect-free
    — the caller is responsible for persisting the `eligibility_checks`
    row.
    """

    engine_id: str = "mock"
    sandbox: bool = True

    def check(
        self,
        *,
        submitter: dict,
        receiver: dict,
        provider: dict,
        payer: dict,
        patient: dict,
        policy: dict,
        service_type_codes: list[str] | None = None,
        inquiry_date: str | None = None,
    ) -> dict[str, Any]:
        svc_codes = service_type_codes or ["30", "33", "98"]
        request_wire = build_270_request(
            submitter=submitter,
            receiver=receiver,
            provider=provider,
            payer=payer,
            patient=patient,
            policy=policy,
            service_type_codes=svc_codes,
            inquiry_date=inquiry_date,
        )
        profile = _derive_plan_profile(policy, payer)
        response_wire = _build_271_response(
            request_wire=request_wire,
            submitter=submitter,
            provider=provider,
            payer=payer,
            patient=patient,
            policy=policy,
            profile=profile,
            service_type_codes=svc_codes,
        )
        parsed = parse_271_response(response_wire)
        # Ensure structured financial fields from the profile are
        # surfaced even when the parser didn't detect them (e.g.
        # `deductible_met_cents` is derived, not a dedicated EB
        # qualifier).
        for k in ("deductible_met_cents", "coinsurance_pct",
                  "out_of_pocket_cents"):
            if parsed.get(k) is None and profile.get(k) is not None:
                parsed[k] = profile[k]
        if not parsed.get("plan_name"):
            parsed["plan_name"] = profile.get("plan_name")
        if not parsed.get("effective_date"):
            parsed["effective_date"] = profile.get("effective_date")
        if not parsed.get("termination_date"):
            parsed["termination_date"] = profile.get("termination_date")
        if profile.get("notes") and not parsed.get("messages"):
            parsed["messages"] = list(profile["notes"])

        return {
            "engine": self.engine_id,
            "sandbox": self.sandbox,
            "service_type_codes": svc_codes,
            "request_wire": request_wire,
            "response_wire": response_wire,
            "result": parsed,
            "checked_at": _now_iso(),
        }


def default_engine() -> MockEligibilityEngine:
    """Return the active eligibility engine. Live clearinghouse
    integration should override this factory when credentials exist.
    """
    return MockEligibilityEngine()
