"""Whisper wrapper for the AI Scribe service.

Migrated 2026-05-04 off the Emergent Universal Key onto the official
``openai`` SDK so the customer's own ``OPENAI_API_KEY`` powers AI Scribe
audio transcription. Public surface (``transcribe_audio_bytes``) is
unchanged — callers still pass raw bytes from the multipart upload and
get a cleaned transcript string back.

Default model is read from ``OPENAI_TRANSCRIBE_MODEL`` (defaults to
``whisper-1``).
"""
from __future__ import annotations

import logging
import os
from io import BytesIO

logger = logging.getLogger("ccms.scribe.transcribe")

DEFAULT_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL") or "whisper-1"


async def transcribe_audio_bytes(
    *, payload: bytes, filename: str = "chunk.webm",
    language: str = "en",
) -> str:
    """Run a Whisper transcription on a single in-memory audio chunk.

    Returns the transcript text. Raises if the OpenAI key is missing or
    the call fails — callers persist the row with
    ``transcribe_status=error``.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured — set it in /app/backend/.env"
        )

    # Lazy import so unit tests that don't exercise transcription don't
    # require the SDK to be installed.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=key)
    buf = BytesIO(payload)
    # The OpenAI SDK uses the file's `name` attribute to infer the
    # MIME extension; setting it explicitly avoids "unsupported file
    # format" errors on opaque BytesIO uploads.
    buf.name = filename

    try:
        resp = await client.audio.transcriptions.create(
            file=buf,
            model=DEFAULT_MODEL,
            language=language,
            response_format="json",
        )
    except Exception:
        logger.exception(
            "scribe.transcribe.openai_call_failed model=%s filename=%s size=%d",
            DEFAULT_MODEL, filename, len(payload),
        )
        raise

    text = getattr(resp, "text", None)
    if text is None and isinstance(resp, dict):
        text = resp.get("text")
    return (text or "").strip()
