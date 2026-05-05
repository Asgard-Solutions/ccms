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


async def get_model_choice(tenant_id: str) -> tuple[str, str]:
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
    provider, model = await get_model_choice(tenant_id)
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
