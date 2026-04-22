"""
services/billing/clearinghouse/payload_837p.py — canonical 837P payload
builders.

Phase 2a promotes the existing builders out of `submission.py` into a
dedicated module so:

  * The canonical claim model has a single home for its wire
    translation.
  * Phase 2b / 2c clearinghouse adapters import from here directly
    without reaching into `submission.py`.
  * We can cleanly split **preview** (today's lightweight, non-
    transmission-ready text) from **wire-ready** generation (Phase 2c).

The existing `submission.py` re-exports these names unchanged so
callers can migrate at their leisure — nothing to rewrite today.
"""
from __future__ import annotations

from services.billing.submission import (
    build_json_payload,
    build_x12_837p_preview,
)

__all__ = [
    "build_json_payload",
    "build_x12_837p_preview",
    "build_x12_837p_wire",
]


async def build_x12_837p_wire(*args, **kwargs) -> str:
    """Placeholder for the Phase 2c wire-ready 837P builder.

    The preview builder in `submission.py` emits a syntactically valid
    but intentionally-not-transmission-ready 837P. The wire builder
    (Phase 2c) will add ISA/GS control numbers, BHT hierarchy, proper
    subscriber / dependent loops (2000B/2000C), and payer-specific
    segment variants. Adapters MUST NOT rely on this function yet —
    attempting to call it raises until the wire builder lands.
    """
    raise NotImplementedError(
        "build_x12_837p_wire is scheduled for Phase 2c. Use "
        "build_x12_837p_preview() for non-transmission previews."
    )
