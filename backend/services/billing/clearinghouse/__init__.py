"""
services/billing/clearinghouse — Clearinghouse adapter abstraction.

Phase 2a introduces a thin abstraction layer between the canonical
billing/claims model and the transport used to deliver claims to
payers. Adapters plug in here; the rest of the system talks only to
`ClearinghouseAdapter` via `routing.get_adapter_for_payer(...)`.

Design principles
-----------------
1. The canonical claim model NEVER carries payer- or clearinghouse-
   specific fields. Adapter-specific concerns (999 / 277CA acks,
   connector ids, raw EDI echoes) ride on the `claim_events` stream.
2. Every adapter is a plain async class with an idempotent contract —
   calling `submit()` twice with the same `claim_id` is safe.
3. The bundled `NoneAdapter` mirrors today's manual workflow exactly
   (paper / fax / portal uploads) so existing submissions keep
   working without behavior change.
4. Credentials are **not** hardcoded; adapters pull them from
   environment variables plus an optional per-tenant
   `clearinghouse_credentials` row (introduced in Phase 2c).

Public API
----------
    from services.billing.clearinghouse import (
        ClearinghouseAdapter, SubmissionResult,
        get_adapter_for_payer,
    )
"""
from __future__ import annotations

from services.billing.clearinghouse.base import (
    Ack,
    ClearinghouseAdapter,
    SubmissionResult,
)
from services.billing.clearinghouse.routing import (
    get_adapter_for_payer,
    register_adapter,
)

__all__ = [
    "Ack",
    "ClearinghouseAdapter",
    "SubmissionResult",
    "get_adapter_for_payer",
    "register_adapter",
]
