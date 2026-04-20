"""
Security response headers middleware.

Applies a conservative set of app-delivered headers on every response. These
headers are **layered** — they supplement, never replace, what the ingress
/ reverse proxy / load balancer should also set. Specifically:

  - HSTS enforcement (`Strict-Transport-Security`) is only meaningful over
    HTTPS. We only emit it when the effective scheme (after proxy-forwarded
    headers) is `https`. The ingress should also emit HSTS.
  - Nothing here can make a plaintext connection secure. A misconfigured
    ingress that accepts HTTP will still be insecure — app-layer headers
    will just not trigger HSTS in that case.

Configurable via env:
  - APP_ENV              : one of dev|staging|production. HSTS + upgrade
                           hints are only added in `production`.
  - HSTS_MAX_AGE_SECONDS : integer (default 15552000 = 180d).
  - CSP_EXTRA            : extra Content-Security-Policy directives appended
                           to the default policy (advanced).
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

DEFAULT_HSTS_MAX_AGE = 15_552_000  # 180 days


def _app_env() -> str:
    return (os.environ.get("APP_ENV") or "dev").strip().lower()


def _is_https_request(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


# Default CSP is deliberately permissive enough for a Create-React-App SPA
# under same-origin ingress, while banning plugins, mixed-content and base-uri
# hijacking. Tighten (e.g. nonce/sha-based script-src) once the frontend
# build pipeline emits hashes.
_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline' https:; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self' https:; "
    "font-src 'self' data: https:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "upgrade-insecure-requests"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        env = _app_env()

        # --- Content-type hardening ---
        response.headers.setdefault("X-Content-Type-Options", "nosniff")

        # --- Clickjacking / framing ---
        response.headers.setdefault("X-Frame-Options", "DENY")

        # --- Referrer hygiene ---
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )

        # --- Permissions-Policy: disable anything the clinic app doesn't need ---
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
            "accelerometer=(), gyroscope=(), magnetometer=()",
        )

        # --- Content-Security-Policy ---
        extra = (os.environ.get("CSP_EXTRA") or "").strip()
        csp = _DEFAULT_CSP
        if extra:
            csp = f"{csp}; {extra}"
        response.headers.setdefault("Content-Security-Policy", csp)

        # --- HSTS (production + HTTPS only) ---
        # NEVER advertise HSTS over plaintext — a browser that received it
        # once would refuse cleartext, but we should not tell browsers a
        # dev HTTP origin is HTTPS-only.
        if env == "production" and _is_https_request(request):
            max_age = int(
                os.environ.get("HSTS_MAX_AGE_SECONDS") or DEFAULT_HSTS_MAX_AGE
            )
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={max_age}; includeSubDomains; preload",
            )

        # --- Cross-origin isolation hints ---
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")

        return response


def install(app) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
