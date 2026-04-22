"""
services/billing/clearinghouse/change_healthcare.py — Change Healthcare / Optum adapter.

Phase 2c responsibilities
-------------------------
1. Pick up configuration from the environment (never from request
   inputs) and expose a redacted summary for the settings UI.
2. Implement `submit()` behind 3 explicit operating modes:
     * `disabled`   → no transmission, no synthetic id. Identical
                      user-facing behavior to the NoneAdapter but the
                      event stream records `adapter_route="change_healthcare"`.
     * `sandbox`    → log the payload (PHI-redacted) and return a
                      synthetic `chc-sbx-{uuid}` external id with
                      status `queued`. No HTTP call is made — this is
                      safe for CI, demos, and before production
                      enrollment completes.
     * `production` → reserved. Real HTTPS transport lands in the next
                      phase. For now a production-mode submit behaves
                      like sandbox but logs a WARNING — so anyone who
                      flips the switch prematurely sees a loud signal.
3. 999 / 277CA / ERA pollers + eligibility remain stubbed (see
   `fetch_ack_999` et al) so Phase 2d/2e can fill them in without
   changing the adapter contract.

Security
--------
* Secrets are read from env vars only. They are never logged, never
  returned by `config_summary()`, and never written to the DB.
* `submit()` intentionally DOES NOT persist the raw `payload_x12`
  beyond the size — the submission row in `claim_submissions` still
  holds the canonical payload, but the adapter layer keeps zero
  additional copies.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from services.billing.clearinghouse.base import Ack, SubmissionResult

log = logging.getLogger("ccms.billing.clearinghouse.chc")

# ---------------------------------------------------------------------------
# Env lookup helpers
# ---------------------------------------------------------------------------
_MODES = {"disabled", "sandbox", "production"}
_DEFAULT_BASE_URL = "https://sandbox.apigee.com/apip/sandbox/"


def _env(*keys: str, default: str | None = None) -> str | None:
    """Return the first non-empty env var in `keys` or `default`."""
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return default


def _redact(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"{value[:2]}****{value[-2:]}"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class ChangeHealthcareAdapter:
    """See module docstring. Intentionally narrow for Phase 2c."""

    route_id: str = "change_healthcare"
    supports_edi: bool = True
    supports_era: bool = True
    supports_eligibility: bool = True

    # Subclasses (e.g. OptumAdapter) override the env-var prefix so
    # the two adapters can coexist with separate credentials.
    _env_prefix: str = "CLEARINGHOUSE_CHC"

    def __init__(self) -> None:
        mode = (_env(f"{self._env_prefix}_MODE") or "disabled").lower().strip()
        if mode not in _MODES:
            log.warning(
                "billing.clearinghouse.unknown_mode",
                extra={"route": self.route_id, "mode": mode,
                       "falling_back_to": "disabled"},
            )
            mode = "disabled"
        self._mode: str = mode
        self._client_id: str | None = _env(f"{self._env_prefix}_CLIENT_ID")
        self._client_secret: str | None = _env(f"{self._env_prefix}_CLIENT_SECRET")
        self._base_url: str = _env(
            f"{self._env_prefix}_BASE_URL", default=_DEFAULT_BASE_URL,
        )
        # Phase 6 — ISA/GS/1000B identity fields. Stored as captured
        # (never logged in full). When absent, submission synthesises
        # placeholders so sandbox-mode payloads still look well-formed.
        self._receiver_id: str | None = _env(f"{self._env_prefix}_RECEIVER_ID")
        self._receiver_name: str | None = _env(f"{self._env_prefix}_RECEIVER_NAME")
        self._biller_id: str | None = _env(f"{self._env_prefix}_BILLER_ID")
        self._submitter_id: str | None = _env(f"{self._env_prefix}_SUBMITTER_ID")
        # Phase 6 — per-service credentials. Change/Optum separate
        # claims submission creds from report/ERA retrieval creds.
        self._claims_username: str | None = _env(
            f"{self._env_prefix}_CLAIMS_USERNAME",
        )
        self._claims_password: str | None = _env(
            f"{self._env_prefix}_CLAIMS_PASSWORD",
        )
        self._reports_username: str | None = _env(
            f"{self._env_prefix}_REPORTS_USERNAME",
        )
        self._reports_password: str | None = _env(
            f"{self._env_prefix}_REPORTS_PASSWORD",
        )
        # Auto-downgrade: production-mode without creds cannot transmit.
        if self._mode == "production" and not self._has_credentials():
            log.warning(
                "billing.clearinghouse.production_missing_credentials",
                extra={"route": self.route_id,
                       "env_prefix": self._env_prefix,
                       "downgrading_to": "disabled"},
            )
            self._mode = "disabled"

    # ------------------------------------------------------------------
    # Introspection — safe to surface to the admin UI (no secrets).
    # ------------------------------------------------------------------
    def _has_credentials(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def submission_identity(self) -> dict:
        """Snapshot of envelope-level identifiers for a submission.

        Persisted alongside the `claim_submissions` row so we can
        reproduce the exact envelope (ISA / GS / 1000B) that went on
        the wire, independently of any config change that happens
        afterwards.
        """
        return {
            "adapter_route": self.route_id,
            "receiver_id": self._receiver_id,
            "receiver_name": self._receiver_name,
            "biller_id": self._biller_id,
            "submitter_id": self._submitter_id,
        }

    def config_summary(self) -> dict[str, Any]:
        """Return a PHI/secret-safe summary for the settings page."""
        return {
            "route_id": self.route_id,
            "mode": self._mode,
            "base_url": self._base_url,
            "has_client_id": bool(self._client_id),
            "has_client_secret": bool(self._client_secret),
            "client_id_hint": _redact(self._client_id),
            "env_prefix": self._env_prefix,
            "supports_edi": self.supports_edi,
            "supports_era": self.supports_era,
            "supports_eligibility": self.supports_eligibility,
            # Phase 6 — envelope identity + service credentials.
            # Never return the raw secret values. `has_*` + redacted
            # hint strings are the only knobs exposed.
            "receiver_id": self._receiver_id,
            "receiver_name": self._receiver_name,
            "biller_id": self._biller_id,
            "submitter_id": self._submitter_id,
            "has_claims_username": bool(self._claims_username),
            "has_claims_password": bool(self._claims_password),
            "claims_username_hint": _redact(self._claims_username),
            "has_reports_username": bool(self._reports_username),
            "has_reports_password": bool(self._reports_password),
            "reports_username_hint": _redact(self._reports_username),
        }

    # ------------------------------------------------------------------
    # Transport — submit / ack / era / eligibility
    # ------------------------------------------------------------------
    async def submit(
        self,
        *,
        claim_id: str,
        payload_json: dict[str, Any],
        payload_x12: str,
        method: str,
        external_reference: str | None,
        payer: dict,
    ) -> SubmissionResult:
        """See ClearinghouseAdapter.submit.

        Modes
        -----
        disabled:  return status=manual; behave as if routed to the
                   NoneAdapter but with `adapter_route` tagged so the
                   claim event stream reflects the intent.
        sandbox:   return status=queued with a synthetic `chc-sbx-{uuid}`
                   external id. No HTTP. Logs a terse, non-PHI line.
        production: RESERVED. Currently behaves like sandbox but emits a
                   WARNING log so an accidental early switch is loud.
        """
        now = datetime.now(timezone.utc).isoformat()

        base_log_extra = {
            "route": self.route_id,
            "claim_id": claim_id,
            "method": method,
            "payload_bytes": len(payload_x12 or ""),
            "payer_enrollment": (payer or {}).get("enrollment_status"),
        }

        if self._mode == "disabled":
            log.info("billing.clearinghouse.submit.disabled", extra=base_log_extra)
            return SubmissionResult(
                adapter_route=self.route_id,
                status="manual",
                external_id=external_reference or None,
                message=(
                    f"{self.route_id} adapter is disabled; no transmission "
                    "performed. Configure env vars "
                    f"{self._env_prefix}_* to enable."
                ),
                submitted_at=now,
            )

        # Gate real transmission on enrollment. Adapter will never move
        # a claim over the wire if the payer isn't enrolled — policy
        # lives here so future adapters can share the pattern.
        enrollment_ok = (payer or {}).get("enrollment_status") == "enrolled"
        if self._mode == "production" and not enrollment_ok:
            log.warning(
                "billing.clearinghouse.submit.production_not_enrolled",
                extra=base_log_extra,
            )
            return SubmissionResult(
                adapter_route=self.route_id,
                status="manual",
                external_id=external_reference or None,
                message=(
                    f"Payer is not yet enrolled with {self.route_id}; "
                    "falling back to manual submission."
                ),
                submitted_at=now,
            )

        if self._mode == "production":
            # Phase 2c intentionally does NOT implement live HTTPS
            # transport. We log a WARNING so any premature flip is
            # visible, then behave like sandbox.
            log.warning(
                "billing.clearinghouse.submit.production_stubbed",
                extra=base_log_extra,
            )

        # sandbox (or production-stubbed) — synthesise a tracking id,
        # return queued, no I/O.
        synthetic_id = f"chc-sbx-{uuid.uuid4().hex[:12]}"
        log.info(
            "billing.clearinghouse.submit.sandbox",
            extra={**base_log_extra, "external_id": synthetic_id},
        )
        return SubmissionResult(
            adapter_route=self.route_id,
            status="queued",
            external_id=synthetic_id,
            raw={
                "mode": self._mode,
                "base_url": self._base_url,
                "synthetic": True,
            },
            message=(
                f"Submitted to {self.route_id} ({self._mode} mode). "
                "Transport is a local stub — no PHI left this system."
            ),
            submitted_at=now,
        )

    # Ack / ERA / eligibility are explicit no-ops in Phase 2c. Leaving
    # them on the adapter keeps the Protocol satisfied.
    async def fetch_ack_999(self, external_id: str) -> Ack | None:
        return None

    async def fetch_ack_277ca(self, external_id: str) -> Ack | None:
        return None

    async def fetch_era_list(self) -> list[dict]:
        return []

    async def eligibility_270_271(self, *, policy: dict) -> dict | None:
        return None


class OptumAdapter(ChangeHealthcareAdapter):
    """Optum-branded alias of ChangeHealthcareAdapter.

    Optum and Change Healthcare share transport & EDI envelope — the
    only practical difference is credential separation so a clinic can
    hold a distinct trading-partner agreement with either brand.
    Stores its config under `CLEARINGHOUSE_OPTUM_*` env vars.
    """

    route_id: str = "optum"
    _env_prefix: str = "CLEARINGHOUSE_OPTUM"
