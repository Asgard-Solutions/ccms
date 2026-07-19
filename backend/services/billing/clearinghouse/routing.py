"""
services/billing/clearinghouse/routing.py — adapter registry + resolver.

`get_adapter_for_payer(payer)` is the ONLY public entry point callers
should use. Phase 2a registers just the `NoneAdapter`; Phase 2c will
plug in `ChangeHealthcareAdapter` (and the Optum alias) behind this
registry without touching the router layer.
"""
from __future__ import annotations

import logging
from typing import Callable

from services.billing.clearinghouse.base import ClearinghouseAdapter
from services.billing.clearinghouse.change_healthcare import (
    ChangeHealthcareAdapter,
    OptumAdapter,
)
from services.billing.clearinghouse.none import NoneAdapter

log = logging.getLogger("ccms.billing.clearinghouse")

# Factory map: route id -> zero-arg factory returning an adapter instance.
# We instantiate on first use and cache so adapters that hold real
# connection state (Phase 2c) don't rebuild on every claim.
_FACTORIES: dict[str, Callable[[], ClearinghouseAdapter]] = {
    "none": NoneAdapter,
    "change_healthcare": ChangeHealthcareAdapter,
    "optum": OptumAdapter,
}
_CACHE: dict[str, ClearinghouseAdapter] = {}

# When a payer references an unknown / not-yet-implemented route we
# fall back to this. `"none"` keeps the manual workflow working and
# surfaces a structured log for operators to notice.
_FALLBACK_ROUTE = "none"


def register_adapter(
    route_id: str, factory: Callable[[], ClearinghouseAdapter],
) -> None:
    """Register an adapter factory under its route id.

    Safe to call at import time. Re-registering an existing id
    replaces the previous factory and evicts the cache entry.
    """
    _FACTORIES[route_id] = factory
    _CACHE.pop(route_id, None)


def available_routes() -> list[str]:
    return sorted(_FACTORIES.keys())


def _load(route_id: str) -> ClearinghouseAdapter:
    if route_id in _CACHE:
        return _CACHE[route_id]
    factory = _FACTORIES.get(route_id)
    if factory is None:
        if route_id != _FALLBACK_ROUTE:
            log.warning(
                "billing.clearinghouse.route_not_registered",
                extra={"route": route_id, "fallback": _FALLBACK_ROUTE},
            )
        factory = _FACTORIES[_FALLBACK_ROUTE]
    instance = factory()
    _CACHE[route_id] = instance
    return instance


def get_adapter_for_payer(payer: dict | None) -> ClearinghouseAdapter:
    """Resolve the clearinghouse adapter for a given payer doc.

    - A missing or `None` payer always routes to the NoneAdapter.
    - Payers stored before Phase 2a may be missing the
      `clearinghouse_route` field entirely; the default `"none"` keeps
      them on the manual path.
    - Enrollment gating (refusing to route through a real adapter when
      `enrollment_status != "enrolled"`) is handled by the caller; this
      function only resolves by route id.
    """
    route = (payer or {}).get("clearinghouse_route") or _FALLBACK_ROUTE
    return _load(route)


def config_summaries() -> list[dict]:
    """Introspection for the admin settings page — lists each
    registered route + its env-sourced mode / capability summary.

    NoneAdapter is filtered out because it has no config. Adapters
    that don't implement `config_summary()` get a minimal shape so
    the UI can still render a row.
    """
    out: list[dict] = []
    for route_id in sorted(_FACTORIES.keys()):
        if route_id == "none":
            continue
        adapter = _load(route_id)
        summary_fn = getattr(adapter, "config_summary", None)
        if callable(summary_fn):
            out.append(summary_fn())
        else:
            out.append({
                "route_id": route_id,
                "mode": "unknown",
                "supports_edi": getattr(adapter, "supports_edi", False),
                "supports_era": getattr(adapter, "supports_era", False),
                "supports_eligibility": getattr(
                    adapter, "supports_eligibility", False,
                ),
                "has_client_id": False,
                "has_client_secret": False,
            })
    return out
