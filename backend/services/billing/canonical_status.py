"""
services/billing/canonical_status.py — Canonical claim lifecycle.

Motivation
----------
The underlying `ClaimStatus` enum has 12 states so the state machine
(see `models.CLAIM_TRANSITIONS`) can express every payer adjudication
outcome precisely. Operations staff, however, only want to reason in
a handful of operational buckets:

    Draft → Ready → Submitted → Accepted → Paid
                                         ↘ Denied → Follow-up
                                         ↗ Needs fixes

This module is the single source of truth that maps the raw 12-state
vocabulary onto the 8 canonical buckets. Every consumer (queue,
dashboards, reports) calls `canonical_status(claim, is_stale=…)` —
there are no parallel `if status == "..."` chains anywhere else.

The raw enum is NOT changing. Transitions, scrubber output, and
remittance posting continue to use raw statuses internally. The
canonical layer is strictly additive.

Mapping table
-------------
    draft              → draft
    validation_failed  → needs_fixes
    ready              → ready
    submitted          → submitted   (→ follow_up when stale)
    pending            → submitted   (→ follow_up when stale)
    accepted           → accepted
    rejected           → denied      (→ follow_up when stale)
    denied             → denied      (→ follow_up when stale)
    paid               → paid
    partially_paid     → follow_up   (requires balance work)
    appealed           → follow_up   (manual follow-up)
    closed             → paid        (→ follow_up if balance > 0)

Stale rule
----------
A claim in `submitted / pending / rejected / denied` that has been
sitting past the follow-up threshold (`DEFAULT_FOLLOWUP_DAYS`) rolls
over to the canonical `follow_up` bucket so operators surface it for
action. The staleness flag is computed ONCE at the top of any queue
query (via `submission.followup_claim_ids`) and threaded through to
avoid N-per-row timestamp math.
"""
from __future__ import annotations

from typing import Iterable, Literal, get_args

CanonicalStatus = Literal[
    "draft",
    "ready",
    "submitted",
    "accepted",
    "needs_fixes",
    "denied",
    "paid",
    "follow_up",
]

CANONICAL_STATUSES: tuple[CanonicalStatus, ...] = tuple(get_args(CanonicalStatus))


# Human-friendly labels for every canonical state. The UI uses the
# same labels so a backend dashboard and a frontend badge stay in
# sync even when the display surface differs.
CANONICAL_LABELS: dict[str, str] = {
    "draft":       "Draft",
    "ready":       "Ready",
    "submitted":   "Submitted",
    "accepted":    "Accepted",
    "needs_fixes": "Needs fixes",
    "denied":      "Denied",
    "paid":        "Paid",
    "follow_up":   "Follow-up needed",
}


_RAW_TO_CANONICAL: dict[str, CanonicalStatus] = {
    "draft":             "draft",
    "validation_failed": "needs_fixes",
    "ready":             "ready",
    "submitted":         "submitted",
    "pending":           "submitted",
    "accepted":          "accepted",
    "rejected":          "denied",
    "denied":            "denied",
    "paid":              "paid",
    "partially_paid":    "follow_up",
    "appealed":          "follow_up",
    "closed":            "paid",
}


# When `is_stale` is true these canonical states upgrade to `follow_up`
# because they've been sitting without resolution long enough to
# require operator attention.
_STALE_UPGRADES: frozenset[str] = frozenset({
    "submitted", "denied",
})


def canonical_status(claim: dict, *, is_stale: bool = False) -> CanonicalStatus:
    """Return the canonical lifecycle bucket for one claim.

    `is_stale` must be supplied by the caller — it's not derived here
    because the staleness query is batched once per queue load (see
    `services.billing.submission.followup_claim_ids`).
    """
    raw = claim.get("status") or "draft"
    base = _RAW_TO_CANONICAL.get(raw, "draft")

    # Phase 10 — a manual / auto follow-up flag always wins over the
    # underlying raw status. This keeps any claim with an unresolved
    # question on the Follow-up tab regardless of lifecycle stage.
    if claim.get("followup_flag"):
        return "follow_up"

    # `closed` with a remaining balance still needs human review —
    # most commonly a write-off or secondary filing decision.
    if raw == "closed":
        billed = int(claim.get("billed_cents") or 0)
        paid = int(claim.get("paid_cents") or 0)
        if billed > 0 and paid < billed:
            return "follow_up"
        return "paid"

    if is_stale and base in _STALE_UPGRADES:
        return "follow_up"

    return base


def canonical_status_label(canonical: str) -> str:
    return CANONICAL_LABELS.get(canonical, canonical)


# ---------------------------------------------------------------------------
# Raw-status sets for each canonical bucket.
#
# Used by queue filtering: instead of the UI filtering rows after
# loading them, callers can convert "give me canonical 'denied'
# claims" into a raw-status Mongo query that's index-friendly. This
# deliberately returns MULTIPLE raw statuses for canonicals that are
# derived (e.g. `follow_up` covers partially_paid + appealed + stale
# submitted/rejected/denied — the staleness piece needs a separate
# id filter the caller must add).
# ---------------------------------------------------------------------------
_CANONICAL_TO_RAW: dict[CanonicalStatus, tuple[str, ...]] = {
    "draft":       ("draft",),
    "ready":       ("ready",),
    "submitted":   ("submitted", "pending"),
    "accepted":    ("accepted",),
    "needs_fixes": ("validation_failed",),
    "denied":      ("rejected", "denied"),
    "paid":        ("paid", "closed"),
    # `follow_up` returns its PRIMARY raw sources. Stale submitted /
    # rejected / denied also belong to follow_up but must be added by
    # the caller via an id-based filter (see router).
    "follow_up":   ("partially_paid", "appealed"),
}


def raw_statuses_for_canonical(
    canonicals: Iterable[str],
) -> list[str]:
    """Expand one or more canonical buckets to their raw statuses.

    Non-overlapping union. Unknown canonical names are silently
    skipped so the caller can pass user input safely.
    """
    out: set[str] = set()
    for c in canonicals:
        for raw in _CANONICAL_TO_RAW.get(c, ()):   # type: ignore[arg-type]
            out.add(raw)
    return sorted(out)
