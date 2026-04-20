"""
Centralised secrets provider.

- `SecretsProvider` is a Protocol with `get(name, default=None)`.
- `EnvSecretsProvider` (default) reads from `os.environ`. Suitable for
  local + preview (values come from the container env).
- `AwsSecretsProvider` is a stub showing how to swap in AWS Secrets
  Manager / Vault later (`import boto3`). Swap via `SECRETS_PROVIDER=aws`.
- `require(name)` is the hard-fail accessor — raises at startup if
  a required secret is missing. Use for DB URLs, JWT_SECRET, etc.
- `redact(text)` masks any value that matches a known secret pattern,
  so log lines + error responses never emit raw secrets.
- `validate_startup()` asserts all required secrets exist before serving
  the first request.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Protocol, runtime_checkable

logger = logging.getLogger("ccms.secrets")

REQUIRED = [
    "MONGO_URL",
    "DB_NAME",
    "JWT_SECRET",
    "DATA_ENCRYPTION_KEY",
]


@runtime_checkable
class SecretsProvider(Protocol):
    def get(self, name: str, default: str | None = None) -> str | None: ...


class EnvSecretsProvider:
    def get(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)


class AwsSecretsProvider:
    """Stub — replace body with boto3 SecretsManager fetch in production."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    def get(self, name: str, default: str | None = None) -> str | None:
        if name in self._cache:
            return self._cache[name]
        val = os.environ.get(name, default)
        if val is not None:
            self._cache[name] = val
        return val


_provider: SecretsProvider | None = None


def provider() -> SecretsProvider:
    global _provider
    if _provider is None:
        kind = os.environ.get("SECRETS_PROVIDER", "env").lower()
        _provider = {
            "env": EnvSecretsProvider(),
            "aws": AwsSecretsProvider(),
        }.get(kind, EnvSecretsProvider())
    return _provider


def get(name: str, default: str | None = None) -> str | None:
    return provider().get(name, default)


class MissingSecretError(RuntimeError):
    pass


def require(name: str) -> str:
    val = get(name)
    if not val:
        raise MissingSecretError(f"Required secret {name!r} is missing")
    return val


def validate_startup() -> list[str]:
    """Assert every required secret is present. Returns missing ones."""
    missing = [n for n in REQUIRED if not get(n)]
    if missing:
        logger.error("Missing required secrets: %s", missing)
    return missing


# ---------------------------------------------------------------------------
# Redaction — strip secret-looking values from logs/error messages.
# ---------------------------------------------------------------------------

# Patterns that look like real secrets (JWTs, Mongo URIs, API keys).
_REDACT_PATTERNS = [
    re.compile(r"mongodb(\+srv)?://[^\s\"'<>]+", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWTs
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+"),
    re.compile(r"(sk|pk)_(live|test)_[A-Za-z0-9]{8,}"),                    # Stripe keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                                       # AWS access keys
    re.compile(r"password=\"?[^\s\"'<>]+", re.IGNORECASE),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    # Redact explicit secret values by name.
    for name in REQUIRED:
        val = get(name)
        if val and len(val) > 6 and val in out:
            out = out.replace(val, "[REDACTED]")
    return out
