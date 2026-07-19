"""AI service — context-aware clinical documentation.

Four surfaces, one shared context loader + prompt pipeline:

  * `GET  /api/ai/chart-brief/{patient_id}` — cached chart-prep brief.
  * `POST /api/ai/chart-brief/{patient_id}/regenerate` — force refresh.
  * `GET  /api/ai/encounters/{note_id}/prior-sections` — last S/O/A/P
    (cached on the patient_id + prior-note_id pair).
  * `POST /api/ai/encounters/{note_id}/draft-sections` — AI-drafted S + P.
  * `GET  /api/ai/encounters/{note_id}/since-last-diff` — outcome/pain deltas.

Every call produces an audit-safe usage row in `ai_usage` (no PHI, just
model, token counts, latency, request_id). The raw prompt and response
are **never** persisted.

Model: Claude Sonnet 4.5 (configurable per-tenant via /settings/ai).
"""
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
