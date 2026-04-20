"""
Central application configuration.

Owns:
  - declaring which env vars are REQUIRED vs RECOMMENDED
  - fail-fast validation at startup so a misconfigured deploy never boots
  - a `describe()` view that masks all secret values for diagnostics /
    admin dashboards

Never raises in production *because a value is readable here* — we want the
router that asks for a secret to keep using `os.environ`. This module is the
single place that knows "what is required" and "how to represent it safely".
"""
from __future__ import annotations

import os

REQUIRED = (
    "MONGO_URL",
    "DB_NAME",
    "JWT_SECRET",
    "DATA_ENCRYPTION_KEY",
)

RECOMMENDED = (
    "FRONTEND_URL",    # lock CORS to this origin in prod
    "REDIS_URL",       # shared rate-limit + cache in prod
    "ADMIN_PASSWORD",  # explicit seed password; never use the default in prod
    "MFA_ISSUER",      # branding in auth apps
)

# Strength guidance — enforced only as warnings, never as hard failures at
# runtime (a boot-break on a slightly-short JWT_SECRET is worse than a
# surfaced warning).
SECRET_MIN_LENGTH = {
    "JWT_SECRET": 32,
    "DATA_ENCRYPTION_KEY": 32,
}


def _present(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def validate_required() -> list[str]:
    """Return a list of missing REQUIRED env vars. Empty list == OK."""
    return [name for name in REQUIRED if not _present(name)]


def ensure_required() -> None:
    """Fail-fast guard for startup. Raises if any REQUIRED var is missing."""
    missing = validate_required()
    if missing:
        raise RuntimeError(
            "Refusing to start — missing required configuration: "
            + ", ".join(missing)
            + ". Populate these in the environment (or your secrets manager) "
            "before booting the service."
        )


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """Return a log-safe rendering of a secret: e.g. 'abcd…(32)'.

    Used for diagnostics. Never return the raw secret."""
    if not value:
        return ""
    trimmed = value.strip()
    if not trimmed:
        return ""
    head = trimmed[:keep] if len(trimmed) > keep * 2 else "****"
    return f"{head}…({len(trimmed)})"


def describe() -> dict:
    """Admin-safe description of the current configuration. No secret values."""
    missing_required = validate_required()
    weak_secrets: list[str] = []
    for name, min_len in SECRET_MIN_LENGTH.items():
        val = os.environ.get(name) or ""
        if val and len(val) < min_len:
            weak_secrets.append(name)

    env_label = os.environ.get("APP_ENV", "dev").strip() or "dev"

    return {
        "app_env": env_label,
        "required": {name: _present(name) for name in REQUIRED},
        "recommended": {name: _present(name) for name in RECOMMENDED},
        "missing_required": missing_required,
        "weak_secrets": weak_secrets,
        "secret_lengths": {
            name: len(os.environ.get(name) or "") for name in SECRET_MIN_LENGTH
        },
        "cors_locked_to_frontend": _present("FRONTEND_URL"),
        "production_ready": (
            not missing_required
            and not weak_secrets
            and _present("FRONTEND_URL")
            and _present("REDIS_URL")
            and _present("ADMIN_PASSWORD")
            and env_label == "production"
        ),
    }


def transport_warnings() -> list[str]:
    """Detect deployment assumptions that are unsafe in production.

    Returns a list of human-readable warning strings. Empty list = OK.
    In `APP_ENV=production` these are logged at WARNING on startup; in dev
    they're informational."""
    warnings: list[str] = []
    env = (os.environ.get("APP_ENV") or "dev").strip().lower()
    is_prod = env == "production"

    frontend_url = (os.environ.get("FRONTEND_URL") or "").strip()
    if is_prod and not frontend_url:
        warnings.append("FRONTEND_URL is not set — CORS will fall back to a wildcard or disabled credentials; production must lock origins.")
    if is_prod and frontend_url and not frontend_url.lower().startswith("https://"):
        warnings.append(f"FRONTEND_URL is not HTTPS ({frontend_url!r}) — secure cookies will be rejected by browsers.")

    cors_raw = (os.environ.get("CORS_ORIGINS") or "*").strip()
    if is_prod and cors_raw == "*":
        warnings.append("CORS_ORIGINS='*' in production — set explicit allowed origins.")

    if is_prod and (os.environ.get("TRUSTED_PROXY_COUNT") or "") == "":
        warnings.append(
            "TRUSTED_PROXY_COUNT not set — x-forwarded-for / x-forwarded-proto are read verbatim. "
            "Set to the number of proxies in front of the app so client IPs / schemes cannot be spoofed."
        )

    if is_prod and not (os.environ.get("HSTS_MAX_AGE_SECONDS") or "").strip():
        # Default still emitted by middleware; only warn if operator wants to customise.
        pass

    if is_prod and (os.environ.get("COOKIE_SAMESITE") or "none").lower() == "none":
        # Current default in the app is SameSite=None (+ Secure) which is required
        # for cross-origin SPA flows. Just surface it so operators see the choice.
        pass

    return warnings
