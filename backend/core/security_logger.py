"""
Structured security-event logger.

Emits one JSON object per line to the Python logger named `"security"`, so
that any downstream log collector (ELK, Datadog, Loki, CloudWatch) can parse
events deterministically. These events are a shape-stable contract — rename
with care.

We never emit:
  - raw passwords / tokens / secrets
  - raw PHI values

We always emit:
  - `event`     — kebab-case event name (e.g. `auth.login.failure`)
  - `outcome`   — `success` | `failure` | `blocked` | `warning`
  - `ts`        — ISO-8601 UTC timestamp
  - `component` — `auth` | `privacy` | `phi` | `rate_limit` | `session` | `system`
  - any additional fields callers pass via **meta (already scrubbed by caller)

Usage:
    from core.security_logger import event
    event("auth.login.failure", outcome="failure", component="auth",
          reason="invalid_credentials", ip=ip, actor_email=email)

Callers are responsible for not shoving PHI into `meta`. Bans are documented
in OPERATIONAL_SECURITY_READINESS.md and enforced by code review + a tiny
sanity filter below.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

_BANNED_META_KEYS = {
    "password", "current_password", "new_password", "old_password",
    "token", "refresh_token", "access_token", "mfa_secret",
    "data_encryption_key", "jwt_secret",
}

logger = logging.getLogger("security")


def _scrub(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k.lower() in _BANNED_META_KEYS:
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def event(
    name: str,
    *,
    outcome: str = "success",
    component: str = "system",
    level: int = logging.INFO,
    **meta: Any,
) -> None:
    payload = {
        "event": name,
        "outcome": outcome,
        "component": component,
        "ts": datetime.now(timezone.utc).isoformat(),
        **_scrub(meta),
    }
    try:
        line = json.dumps(payload, default=str, separators=(",", ":"))
    except Exception:
        # Defensive — never raise from the logger itself.
        line = json.dumps({"event": name, "outcome": outcome, "component": component})
    logger.log(level, line)


def suspicious(name: str, **meta: Any) -> None:
    """Shortcut for alert-worthy events at WARNING level."""
    event(name, outcome="warning", component="suspicious", level=logging.WARNING, **meta)
