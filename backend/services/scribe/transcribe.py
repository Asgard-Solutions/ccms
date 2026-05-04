"""Whisper wrapper for the scribe service.

Thin, focused — uses `OpenAISpeechToText` from emergentintegrations and
hands back the recognised text. The input is a raw bytes payload (the
multipart upload body) so callers don't have to write to disk.
"""
from __future__ import annotations

import logging
import os
from io import BytesIO

logger = logging.getLogger("ccms.scribe.transcribe")


async def transcribe_audio_bytes(
    *, payload: bytes, filename: str = "chunk.webm",
    language: str = "en",
) -> str:
    """Run a Whisper transcription on a single in-memory audio chunk.

    Returns the transcript text. Raises if the LLM key is missing or the
    call fails — callers persist the row with `transcribe_status=error`.
    """
    key = os.environ.get("EMERGENT_LLM_KEY")
    if not key:
        raise RuntimeError("EMERGENT_LLM_KEY is not configured")

    from emergentintegrations.llm.openai import OpenAISpeechToText

    stt = OpenAISpeechToText(api_key=key)
    buf = BytesIO(payload)
    # Some emergentintegrations builds expect a file-like object exposing
    # a `name` attribute (mimicking `open(...)`), so we set it explicitly.
    buf.name = filename
    response = await stt.transcribe(
        file=buf,
        model="whisper-1",
        response_format="json",
        language=language,
    )
    text = getattr(response, "text", None)
    if text is None and isinstance(response, dict):
        text = response.get("text")
    if text is None:
        text = str(response or "")
    return (text or "").strip()
