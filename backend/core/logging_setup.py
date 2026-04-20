"""
Structured-logging configuration.

Wires the root logger + the `security` logger to emit one-line output that a
SIEM / log collector can ingest without custom parsing. In dev we emit human
readable lines with an ISO timestamp; in production (APP_ENV=production) we
emit JSON so shipping agents can forward directly.

This module is intentionally idempotent — call `configure()` once at startup.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # If the message is already JSON (security_logger emits JSON), keep it.
        msg = record.getMessage()
        if msg.startswith("{") and msg.endswith("}"):
            try:
                payload = json.loads(msg)
                payload.setdefault("logger", record.name)
                payload.setdefault("level", record.levelname)
                payload.setdefault(
                    "ts",
                    datetime.now(timezone.utc).isoformat(),
                )
                return json.dumps(payload, default=str, separators=(",", ":"))
            except Exception:
                pass
        return json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "logger": record.name,
                "level": record.levelname,
                "message": msg,
            },
            default=str,
            separators=(",", ":"),
        )


def configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    app_env = (os.environ.get("APP_ENV") or "dev").lower().strip()

    handler = logging.StreamHandler(sys.stdout)
    if app_env == "production":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
            )
        )

    root = logging.getLogger()
    # Preserve uvicorn's handler but ensure our level is informative.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # The `security` logger always emits JSON regardless of env, so that
    # any SIEM forwarder can pick it up deterministically even in dev.
    sec = logging.getLogger("security")
    sec.setLevel(logging.INFO)
    if not any(getattr(h, "_ccms_security", False) for h in sec.handlers):
        sec_handler = logging.StreamHandler(sys.stdout)
        sec_handler.setFormatter(JsonFormatter())
        sec_handler._ccms_security = True  # type: ignore[attr-defined]
        sec.addHandler(sec_handler)
        # Do not bubble to root — avoids double printing.
        sec.propagate = False
