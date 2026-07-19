"""
services/billing/clearinghouse/none.py — no-op adapter.

This adapter is the default binding for payers that have NOT yet been
enrolled with a clearinghouse. It preserves the existing manual
workflow exactly:

  * `submit()` returns `status="manual"` and performs no I/O. The
    caller records the claim submission as usual; the operator still
    delivers the claim via paper / fax / portal themselves.
  * Ack fetchers and ERA listing return `None` / `[]`.

Keeping manual submissions routed through this adapter means the rest
of the system can assume *every* submission went through an adapter —
no special-casing at the router layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.billing.clearinghouse.base import Ack, SubmissionResult


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class NoneAdapter:
    """See module docstring."""

    route_id: str = "none"
    supports_edi: bool = False
    supports_era: bool = False
    supports_eligibility: bool = True  # Mock engine is always available

    async def submit(
        self,
        *,
        claim_id: str,
        payload_json: dict[str, Any],
        payload_x12: str,
        method: str,
        external_reference: str | None,
        payer: dict,
    ) -> SubmissionResult:
        # We intentionally do not transmit anything. The operator has
        # already (or will shortly) delivered the claim by the declared
        # method (paper / portal / batch file upload).
        return SubmissionResult(
            adapter_route="none",
            status="manual",
            external_id=external_reference or None,
            raw=None,
            message=f"Manual submission recorded ({method}).",
            submitted_at=_now_iso(),
        )

    async def fetch_ack_999(self, external_id: str) -> Ack | None:
        return None

    async def fetch_ack_277ca(self, external_id: str) -> Ack | None:
        return None

    async def fetch_era_list(self) -> list[dict]:
        return []

    async def eligibility_270_271(self, *, policy: dict) -> dict | None:
        return None
