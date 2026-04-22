"""
services/billing/clearinghouse/payload_837p.py — canonical 837P payload
builders.

Phase 2a promoted the preview builders out of `submission.py` into this
dedicated module; Phase 7 adds the real wire-ready 837 Professional
005010X222A1 generator.

Callers should prefer:

  * `build_x12_837p_wire` — HIPAA-compliant 837P, ISA...IEA envelope,
    full segment coverage (1000A submitter, 1000B receiver, 2010AA
    billing provider, 2000B/2010BA subscriber, 2010BB payer, 2000C
    patient when applicable, 2300 claim, 2310B/C providers + facility,
    2400 service lines). Persisted on `claim_submissions.payload_x12`.
  * `build_x12_837p_preview` — lightweight, human-skimmable preview.
    Retained for backward compatibility with callers that only want
    the legacy shape; all new adapters should use the wire builder.

`build_json_payload` remains unchanged — it's a canonical-model dump,
not an 837P artifact, and is consumed by internal tooling only.
"""
from __future__ import annotations

from services.billing.clearinghouse.x12_837p import (
    build_837p_document,
    build_claim_context,
    build_x12_837p_wire,
)
from services.billing.submission import (
    build_json_payload,
    build_x12_837p_preview,
)

__all__ = [
    "build_json_payload",
    "build_x12_837p_preview",
    "build_x12_837p_wire",
    "build_claim_context",
    "build_837p_document",
]
