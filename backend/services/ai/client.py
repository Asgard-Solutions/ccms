"""Thin wrapper around `emergentintegrations.LlmChat` for Claude Sonnet 4.5.

Uses `EMERGENT_LLM_KEY` from the environment. Callers pass a system
prompt + user text and we return the raw text and a small usage record
(token counts + latency) suitable for auditing without logging PHI.

Per-tenant model override is read from `ai_settings.model_provider /
ai_settings.model_name` — if unset we default to Claude Sonnet 4.5.
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
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
AI_USAGE_COLLECTION = "ai_usage"
AI_SETTINGS_COLLECTION = "ai_settings"


async def get_model_choice(tenant_id: str) -> tuple[str, str]:
    db = get_db()
    doc = await db[AI_SETTINGS_COLLECTION].find_one(
        {"tenant_id": tenant_id}, {"_id": 0},
    )
    if not doc:
        return DEFAULT_PROVIDER, DEFAULT_MODEL
    return (
        doc.get("model_provider") or DEFAULT_PROVIDER,
        doc.get("model_name") or DEFAULT_MODEL,
    )


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
    """Run a single-turn LLM request. Returns
    ``{text, request_id, provider, model}`` or raises on hard failure.
    """
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError("EMERGENT_LLM_KEY is not configured")
    provider, model = await get_model_choice(tenant_id)
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=key,
            session_id=f"{surface}-{request_id}",
            system_message=system_prompt,
        ).with_model(provider, model)
        # Ask for JSON by enforcing it in the user turn when needed.
        user_turn = user_text
        if response_format == "json":
            user_turn = (
                user_text
                + "\n\nReply with ONLY a JSON object, no prose, no Markdown fence."
            )
        resp = await chat.send_message(UserMessage(text=user_turn))
        await _log_usage(
            tenant_id=tenant_id, actor=actor, request_id=request_id,
            surface=surface, provider=provider, model=model,
            started_at=started, status="ok", error=None,
        )
        return {
            "text": resp,
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
