"""
services/billing/clearinghouse/base.py — Adapter contract.

Every concrete adapter (NoneAdapter, ChangeHealthcareAdapter, …)
implements this interface. The router layer never imports a concrete
adapter directly — it always goes through
`routing.get_adapter_for_payer(payer)`.

The contract is intentionally narrow and transport-agnostic. Specific
adapters may implement additional private helpers but must not expose
clearinghouse-specific request / response shapes on these method
signatures — canonical types only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Canonical adapter outputs
# ---------------------------------------------------------------------------
# SubmissionResult.status vocabulary (NOT the claim status enum — that
# stays minimal). These describe the transport handoff, not
# adjudication.
#   manual      — adapter performs no transmission; operator delivered
#                 the claim themselves (paper/fax/portal).
#   queued      — accepted by the clearinghouse for processing.
#   accepted    — immediate synchronous acceptance (rare — most
#                 clearinghouses are async).
#   rejected    — synchronous reject at the adapter boundary (e.g.
#                 auth failure, 999 front-door reject returned inline).
#   error       — transport-level failure the operator should retry.
SUBMISSION_STATUSES = (
    "manual", "queued", "accepted", "rejected", "error",
)


@dataclass(slots=True)
class SubmissionResult:
    """Canonical return shape for `ClearinghouseAdapter.submit`."""
    adapter_route: str               # matches the payer's clearinghouse_route
    status: str                      # one of SUBMISSION_STATUSES
    external_id: str | None = None   # clearinghouse tracking id (when available)
    raw: dict | None = None          # opaque adapter echo for audit/debug
    message: str | None = None       # human-readable note for the timeline
    # Optional `submitted_at` override. Most adapters let the caller
    # stamp time; real clearinghouses that return a server timestamp
    # should populate this so reconciliation is exact.
    submitted_at: str | None = None


@dataclass(slots=True)
class Ack:
    """Canonical adapter acknowledgment (999 / 277CA / vendor-specific)."""
    kind: str                        # "999" | "277ca" | "portal_confirmation" | ...
    accepted: bool
    received_at: str
    external_id: str | None = None
    denial_code: str | None = None   # when rejected
    message: str | None = None
    raw: dict | None = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
@runtime_checkable
class ClearinghouseAdapter(Protocol):
    """Protocol every concrete adapter implements.

    NOTE: we intentionally use a Protocol (+ runtime_checkable) instead
    of an ABC so lightweight test doubles and the NoneAdapter can
    satisfy the contract without subclassing.
    """

    # Stable route id — must match `Payer.clearinghouse_route`.
    route_id: str
    # Transport capabilities. Used by the router to decide whether a
    # payer marked `claim_submission_mode="edi"` can actually talk EDI
    # through this adapter.
    supports_edi: bool
    supports_era: bool
    supports_eligibility: bool

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
        """Hand the canonical claim payload to the clearinghouse.

        - `method` is the caller-supplied transport hint from
          `ClaimSubmissionCreate` (`manual_paper` / `manual_portal` /
          `batch_file`). Adapters may ignore it if their own route
          takes precedence.
        - Implementations MUST be idempotent per `claim_id`; re-calling
          with the same claim_id should either succeed the same way or
          return a `status="rejected"` with a clear message.
        """
        ...

    async def fetch_ack_999(self, external_id: str) -> Ack | None:
        """Retrieve the 999 functional ack, if available."""
        ...

    async def fetch_ack_277ca(self, external_id: str) -> Ack | None:
        """Retrieve the 277CA claim-status ack, if available."""
        ...

    async def fetch_era_list(self) -> list[dict]:
        """List available 835 ERAs on the clearinghouse side.

        Each item is an opaque dict; callers pipe raw payloads into
        `services.billing.remittance_import.parse_835(...)`.
        """
        ...

    async def eligibility_270_271(self, *, policy: dict) -> dict | None:
        """Execute a 270 eligibility request; return parsed 271 payload."""
        ...
