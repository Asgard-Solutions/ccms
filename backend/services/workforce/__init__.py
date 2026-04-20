"""Workforce & patient identity workflows — Iteration 19.

One router that covers the identity + emergency-access surface area that
the HIPAA / SOC 2 / ISO 27001 access-control controls expect to exist:

- Workforce invitations + activation  (mocked email — dev_token returned)
- Patient proxy / personal-representative grants
- Active-session visibility + one-shot revocation (self + admin)
- One-shot atomic deprovisioning of a workforce user
- Formal break-glass with auto-expiry + 24h post-use attestation window
- Suspicious-login detection hook (audit + step-up MFA enforcement)

All endpoints are tenant-scoped via `TenantContext`, audited via
`core.audit.log_audit`, and gated by `require_permission()` using the
canonical permission catalog from `services.authz.constants`.
"""
from __future__ import annotations
