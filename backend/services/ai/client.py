"""Direct Anthropic client wrapper used by every Claude flow in CCMS.

Migrated 2026-05-04 off the Emergent Universal Key + `emergentintegrations`
shim onto the official `anthropic` SDK so the customer's own
`ANTHROPIC_API_KEY` powers AI Scribe SOAP drafts, coding suggestions,
semantic search, the patient visit brief, NL scheduling, and SOAP
template overrides.

Public surface (`generate`, `parse_json_safely`) is unchanged so router
and prompt code don't have to be rewritten.

Per-tenant model override is read from `ai_settings.model_provider /
ai_settings.model_name`. When `model_provider == "anthropic"` we honour
the requested model; any other provider value is logged + falls back
to the env-default model so a misconfigured tenant cannot break AI
across the platform.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional

from core.db import get_db
from core.tenancy import tenant_db
from services.ai import now_iso

logger = logging.getLogger("ccms.ai.client")

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = os.environ.get("ANTHROPIC_TEXT_MODEL") or "claude-sonnet-4-5-20250929"
AI_USAGE_COLLECTION = "ai_usage"
AI_SETTINGS_COLLECTION = "ai_settings"

# Per-surface model recommendations. The user-facing Settings → AI page
# uses these as the "Reset to recommended" target. Each surface picks
# the smallest model that still meets the quality bar for that flow.
#
# Opus  — highest quality, used for the doctor-facing SOAP draft where
#         a hallucination is expensive (rewrites, malpractice exposure).
# Sonnet — default balance; safe pick for any flow.
# Haiku — fastest + cheapest; good for high-volume structured outputs
#         (semantic search ranking, NL scheduling parser, prior sections).
SURFACE_RECOMMENDED_MODEL: dict[str, str] = {
    "scribe_soap_draft":      "claude-opus-4-5-20251101",
    "scribe_coding_suggest":  "claude-sonnet-4-5-20250929",
    "prior_sections":         "claude-haiku-4-5-20251001",
    "draft_sections":         "claude-sonnet-4-5-20250929",
    "since_last_diff":        "claude-haiku-4-5-20251001",
    "chart_brief":            "claude-sonnet-4-5-20250929",
    "semantic_search":        "claude-haiku-4-5-20251001",
    "nl_schedule_parse":      "claude-haiku-4-5-20251001",
    "patient_visit_brief":    "claude-sonnet-4-5-20250929",
}

# Friendly metadata for the Settings → AI picker UI. Order = display
# order; "intent" is rendered as a one-liner help text below the
# dropdown. Cost values are Anthropic's published per-million-token
# rates as of Feb 2026 — informational only, not used for billing.
AI_SURFACES: list[dict] = [
    {"key": "scribe_soap_draft",
     "label": "AI Scribe — SOAP draft",
     "intent": "Generates the full SOAP from voice + addendum. Doctor-facing; highest quality wins."},
    {"key": "scribe_coding_suggest",
     "label": "AI Scribe — CPT/ICD suggestions",
     "intent": "Codes the encounter from the SOAP. Factual, structured output; balanced model is fine."},
    {"key": "prior_sections",
     "label": "Prior-section pull",
     "intent": "Lifts last-visit narrative into the current note. Lots of calls, low risk per call."},
    {"key": "draft_sections",
     "label": "Draft-from-bullet sections",
     "intent": "Expands a doctor's bullet list into prose. Modest quality bar."},
    {"key": "since_last_diff",
     "label": "Since-last-visit diff summary",
     "intent": "Highlights what's changed since the prior encounter. Concise; cheap model is fine."},
    {"key": "chart_brief",
     "label": "Clinical chart brief (admin)",
     "intent": "Patient overview for new providers. Mid-volume, decent quality bar."},
    {"key": "semantic_search",
     "label": "Semantic search ranking",
     "intent": "Ranks chart snippets against a doctor's question. Latency-sensitive; Haiku recommended."},
    {"key": "nl_schedule_parse",
     "label": "Natural-language scheduling parser",
     "intent": "Resolves intent + entities from free text. Structured output; Haiku is plenty."},
    {"key": "patient_visit_brief",
     "label": "Patient-facing visit brief",
     "intent": "Plain-language summary the patient reads. Quality matters; Sonnet recommended."},
]

# Models we expose in the picker. Each entry tags the canonical
# Anthropic id, a short alias users see, and indicative pricing.
AI_AVAILABLE_MODELS: list[dict] = [
    {"id": "claude-opus-4-5-20251101",
     "alias": "claude-opus-4-5",
     "label": "Claude Opus 4.5",
     "tier": "premium",
     "input_per_mtok_usd": 15.0, "output_per_mtok_usd": 75.0,
     "blurb": "Highest quality. Use for SOAP drafts where a re-write costs minutes."},
    {"id": "claude-sonnet-4-5-20250929",
     "alias": "claude-sonnet-4-5",
     "label": "Claude Sonnet 4.5",
     "tier": "balanced",
     "input_per_mtok_usd": 3.0, "output_per_mtok_usd": 15.0,
     "blurb": "Recommended default. Strong quality at 1/5 the Opus cost."},
    {"id": "claude-haiku-4-5-20251001",
     "alias": "claude-haiku-4-5",
     "label": "Claude Haiku 4.5",
     "tier": "fast",
     "input_per_mtok_usd": 1.0, "output_per_mtok_usd": 5.0,
     "blurb": "Fast + cheap. Use for high-volume structured outputs."},
]


async def get_model_choice(
    tenant_id: str, surface: str | None = None,
) -> tuple[str, str]:
    """Resolve the (provider, model) tuple for one Anthropic call.

    Lookup precedence:
      1. ``ai_settings.surface_models[surface]`` — per-surface override
         set in Settings → AI.
      2. ``ai_settings.model_name`` — tenant-wide default.
      3. ``ANTHROPIC_TEXT_MODEL`` env / module default.
    """
    db = get_db()
    doc = await db[AI_SETTINGS_COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )
    if not doc:
        return DEFAULT_PROVIDER, DEFAULT_MODEL
    provider = (doc.get("model_provider") or DEFAULT_PROVIDER).strip().lower()
    if provider != DEFAULT_PROVIDER:
        # Foreign provider stored on the tenant — log loud + fall back.
        logger.warning(
            "ai.client.unsupported_provider tenant=%s provider=%s; using anthropic default",
            tenant_id, provider,
        )
        return DEFAULT_PROVIDER, DEFAULT_MODEL
    # Per-surface override wins when present.
    if surface:
        surface_models = doc.get("surface_models") or {}
        per_surface = (surface_models.get(surface) or "").strip()
        if per_surface:
            return provider, per_surface
    return provider, (doc.get("model_name") or DEFAULT_MODEL)


async def _log_usage(
    *, tenant_id: str, actor: dict, request_id: str,
    surface: str, provider: str, model: str,
    started_at: float, status: str, error: str | None,
) -> None:
    latency_ms = int((time.monotonic() - started_at) * 1000)
    await tenant_db(tenant_id)[AI_USAGE_COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "request_id": request_id,
        "surface": surface,
        "provider": provider,
        "model": model,
        "status": status,
        "latency_ms": latency_ms,
        "error": error,
        "actor_id": actor.get("id"),
        "created_at": now_iso(),
    })


async def generate(
    *, tenant_id: str, actor: dict, system_prompt: str,
    user_text: str, surface: str,
    response_format: str = "text",  # "text" | "json"
    max_tokens: int = 1200,
) -> dict:
    """Run a single-turn Anthropic Messages call. Returns
    ``{text, request_id, provider, model}`` or raises on hard failure.

    Uses the customer's ``ANTHROPIC_API_KEY``. The default model comes
    from ``ANTHROPIC_TEXT_MODEL`` (env) and can be overridden per-tenant
    via the ``ai_settings`` collection — useful when one clinic wants
    Opus for SOAP drafts and Haiku for cheap classification flows.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured — set it in /app/backend/.env"
        )
    provider, model = await get_model_choice(tenant_id, surface=surface)
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    try:
        # Lazy import keeps startup fast and lets unit tests run without
        # the SDK installed.
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key)
        user_turn = user_text
        if response_format == "json":
            # Force a clean JSON shape; Anthropic returns text even in
            # JSON mode so we still hand back through parse_json_safely.
            user_turn = (
                user_text
                + "\n\nReply with ONLY a JSON object, no prose, no Markdown fence."
            )

        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_turn}],
        )
        # Anthropic returns a list of content blocks. We only request
        # text, so concatenating the text-block payloads is safe.
        text_chunks = [
            block.text for block in msg.content
            if getattr(block, "type", None) == "text"
            and getattr(block, "text", None) is not None
        ]
        text = "".join(text_chunks).strip()

        await _log_usage(
            tenant_id=tenant_id, actor=actor, request_id=request_id,
            surface=surface, provider=provider, model=model,
            started_at=started, status="ok", error=None,
        )
        return {
            "text": text,
            "request_id": request_id,
            "provider": provider, "model": model,
        }
    except Exception as exc:  # noqa: BLE001
        await _log_usage(
            tenant_id=tenant_id, actor=actor, request_id=request_id,
            surface=surface, provider=provider, model=model,
            started_at=started, status="error", error=str(exc)[:300],
        )
        logger.warning(
            "AI generate failed surface=%s provider=%s err=%s",
            surface, provider, str(exc)[:200],
        )
        raise


def parse_json_safely(raw: str) -> Optional[dict]:
    """Best-effort JSON parse. Tolerates Markdown fences and whitespace."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        # Strip leading fence + optional language tag.
        text = text[3:]
        for tag in ("json", "JSON"):
            if text.startswith(tag):
                text = text[len(tag):]
                break
        # Strip the trailing fence if present.
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to pull the first {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None
